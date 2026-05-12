"""
portal.py

Public employee portal clock in/out views.
No authentication required - suitable for kiosk-style access.
Employees identify themselves by badge ID or name.
"""

import ipaddress
import logging
import re
import socket
import struct
from datetime import date, datetime, timedelta

import pytz
from django.conf import settings
from django.utils import timezone
from geopy.distance import geodesic

from django.contrib.auth.models import User
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from attendance.methods.utils import (
    format_time,
    shift_schedule_today,
    strtime_seconds,
)
from attendance.models import (
    Attendance,
    AttendanceActivity,
    AttendanceGeneralSetting,
)
from attendance.views.clock_in_out import (
    clock_in_attendance_and_activity,
    clock_out_attendance_and_activity,
)
from base.models import AttendanceAllowedIP, Company, EmployeeShiftDay
from employee.models import Employee

logger = logging.getLogger(__name__)


def _reverse_geocode(lat, lng):
    try:
        from geopy.geocoders import Nominatim
        geolocator = Nominatim(user_agent="hris_attendance")
        location = geolocator.reverse((lat, lng), exactly_one=True, timeout=3)
        return location.address if location else None
    except Exception:
        return None


# NTP servers to try in order
_NTP_SERVERS = ["time.cloudflare.com", "pool.ntp.org", "time.google.com"]
_NTP_DELTA = 2208988800  # seconds between NTP epoch (1900) and Unix epoch (1970)
_PORTAL_PIN_ATTEMPTS_MAX = 3
_PORTAL_PIN_VERIFICATION_TTL_SECONDS = 120
_PORTAL_PIN_ATTEMPTS_SESSION_KEY = "portal_pin_attempts"
_PORTAL_PIN_VERIFICATION_SESSION_KEY = "portal_pin_verification"


def get_real_now():
    """
    Return the current datetime from an NTP internet time server so that
    employees cannot manipulate attendance times by changing their PC clock.

    Falls back to Django's timezone.now() only when no NTP server is reachable
    (e.g. no internet connection), so the system keeps working offline.

    Returns a *naive* datetime in the configured TIME_ZONE, matching the
    behaviour of datetime.now() that the rest of the attendance code expects.
    """
    tz = pytz.timezone(settings.TIME_ZONE)
    for server in _NTP_SERVERS:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(2)
            # Minimal NTP request packet (LI=0, VN=3, Mode=3)
            sock.sendto(b"\x1b" + 47 * b"\x00", (server, 123))
            data, _ = sock.recvfrom(1024)
            sock.close()
            # Transmit Timestamp is at bytes 40-47; upper 32 bits = seconds
            tx_seconds = struct.unpack("!I", data[40:44])[0] - _NTP_DELTA
            utc_dt = datetime.fromtimestamp(tx_seconds, tz=pytz.UTC)
            # Convert to local timezone and strip tzinfo to stay naive
            return utc_dt.astimezone(tz).replace(tzinfo=None)
        except Exception:
            continue
    # All NTP servers failed — fall back to system time and log a warning
    logger.warning("NTP sync failed; falling back to system clock for attendance time")
    return datetime.now()


def _get_client_ip(request):
    """
    Return the real client IP, checking proxy/CDN headers in priority order:
      1. CF-Connecting-IP  — set by Cloudflare, most reliable when behind CF
      2. X-Real-IP         — set by nginx and other single-hop proxies
      3. X-Forwarded-For   — leftmost (original client) entry in the chain
      4. REMOTE_ADDR       — direct connection fallback
    When the resolved IP is loopback (127.x / ::1), substitute the server's
    own LAN IP so local rules still match.
    """
    for header in ("HTTP_CF_CONNECTING_IP", "HTTP_X_REAL_IP"):
        ip = request.META.get(header, "").strip()
        if ip:
            logger.debug(f"[IP] resolved from {header}: {ip}")
            return ip

    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "").strip()
    if forwarded:
        ip = forwarded.split(",")[0].strip()
        logger.debug(f"[IP] resolved from X-Forwarded-For: {ip}")
        return ip

    ip = request.META.get("REMOTE_ADDR", "")
    try:
        if ipaddress.ip_address(ip).is_loopback:
            ip = socket.gethostbyname(socket.gethostname())
    except (ValueError, OSError):
        pass
    logger.debug(f"[IP] resolved from REMOTE_ADDR: {ip}")
    return ip


def _geofence_check(employee, work_info, latitude, longitude):
    """
    Return a dict with error details if the employee is outside their branch geofence,
    or None if the clock action should be allowed.
    """
    try:
        from geofencing.models import GeoFencing
        branch = work_info.branch_id if work_info else None
        if not branch:
            return None
        try:
            geo = GeoFencing.objects.get(branch_id=branch, start=True)
        except GeoFencing.DoesNotExist:
            return None
        if geo.excluded_employees.filter(pk=employee.pk).exists():
            return None
        distance = geodesic(
            (geo.latitude, geo.longitude),
            (float(latitude), float(longitude)),
        ).meters
        if distance > geo.radius_in_meters:
            return {
                "message": "You are outside the allowed location for your branch.",
                "geo_center_lat": geo.latitude,
                "geo_center_lng": geo.longitude,
                "geo_radius_meters": geo.radius_in_meters,
                "user_lat": float(latitude),
                "user_lng": float(longitude),
            }
    except Exception:
        pass
    return None


def _ip_is_allowed(request):
    """
    Return True if IP restrictions are disabled, or if the client IP is in the
    allowed list stored in AttendanceAllowedIP.
    """
    restriction = AttendanceAllowedIP.objects.first()
    if not restriction or not restriction.is_enabled:
        return True

    client_ip = _get_client_ip(request)
    allowed = restriction.additional_data.get("allowed_ips", [])
    logger.info(f"[IP restriction] client_ip={client_ip!r}  allowed_list={allowed}")
    for entry in allowed:
        try:
            if ipaddress.ip_address(client_ip) in ipaddress.ip_network(
                entry, strict=False
            ):
                return True
        except ValueError:
            continue
    return False


def _get_pin_attempts(request):
    """
    Return PIN attempts map from session.
    """
    session = getattr(request, "session", None)
    if session is None:
        return {}

    attempts = session.get(_PORTAL_PIN_ATTEMPTS_SESSION_KEY, {})
    return attempts if isinstance(attempts, dict) else {}


def _set_pin_attempts(request, attempts):
    """
    Persist PIN attempts map to session.
    """
    session = getattr(request, "session", None)
    if session is None:
        return

    session[_PORTAL_PIN_ATTEMPTS_SESSION_KEY] = attempts
    session.modified = True


def _clear_pin_attempts(request, employee_id=None):
    """
    Clear PIN attempts for one employee or all employees.
    """
    session = getattr(request, "session", None)
    if session is None:
        return

    if employee_id is None:
        session.pop(_PORTAL_PIN_ATTEMPTS_SESSION_KEY, None)
        session.modified = True
        return

    attempts = _get_pin_attempts(request)
    key = str(employee_id)
    if key in attempts:
        attempts.pop(key, None)
        _set_pin_attempts(request, attempts)


def _increment_pin_attempts(request, employee_id):
    """
    Increment PIN attempts for an employee and return remaining attempts.
    """
    attempts = _get_pin_attempts(request)
    key = str(employee_id)
    current_count = int(attempts.get(key, 0)) + 1

    if current_count >= _PORTAL_PIN_ATTEMPTS_MAX:
        attempts.pop(key, None)
        _set_pin_attempts(request, attempts)
        return 0

    attempts[key] = current_count
    _set_pin_attempts(request, attempts)
    return _PORTAL_PIN_ATTEMPTS_MAX - current_count


def _clear_pin_verification(request):
    """
    Remove active PIN verification from session.
    """
    session = getattr(request, "session", None)
    if session is None:
        return

    session.pop(_PORTAL_PIN_VERIFICATION_SESSION_KEY, None)
    session.modified = True


def _set_pin_verification(request, employee_id):
    """
    Store successful PIN verification in session with timestamp.
    """
    session = getattr(request, "session", None)
    if session is None:
        return

    session[_PORTAL_PIN_VERIFICATION_SESSION_KEY] = {
        "employee_id": str(employee_id),
        "verified_at": timezone.now().timestamp(),
    }
    session.modified = True


def _get_pin_verification(request):
    """
    Return active PIN verification from session, or None if invalid/expired.
    """
    session = getattr(request, "session", None)
    if session is None:
        return None

    verification = session.get(_PORTAL_PIN_VERIFICATION_SESSION_KEY)
    if not isinstance(verification, dict):
        return None

    employee_id = verification.get("employee_id")
    verified_at = verification.get("verified_at")

    if employee_id is None or not isinstance(verified_at, (int, float)):
        _clear_pin_verification(request)
        return None

    age_seconds = timezone.now().timestamp() - float(verified_at)
    if age_seconds > _PORTAL_PIN_VERIFICATION_TTL_SECONDS:
        _clear_pin_verification(request)
        return None

    return verification


def _require_verified_pin(request, employee_id):
    """
    Check if a valid PIN verification exists for the given employee.
    """
    verification = _get_pin_verification(request)
    if not verification:
        return (
            False,
            "PIN verification required. Please search and enter your 6-digit PIN.",
        )

    if str(verification.get("employee_id")) != str(employee_id):
        _clear_pin_verification(request)
        return (
            False,
            "PIN verification does not match the selected employee. Please search again.",
        )

    return True, ""


def public_portal(request):
    """
    Render the public portal clock in/out page.
    No authentication required.
    """
    if not _ip_is_allowed(request):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden(
            """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Access Denied</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      background: #f4f6fa;
      font-family: 'Segoe UI', Arial, sans-serif;
    }
    .card {
      background: #fff;
      border-radius: 16px;
      box-shadow: 0 4px 32px rgba(0,0,0,0.10);
      padding: 52px 48px 44px;
      max-width: 420px;
      width: 90%;
      text-align: center;
    }
    .icon {
      width: 72px;
      height: 72px;
      background: #fff0f0;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      margin: 0 auto 24px;
    }
    .icon svg { width: 36px; height: 36px; }
    h1 {
      font-size: 1.6rem;
      font-weight: 700;
      color: #1a1a2e;
      margin-bottom: 12px;
    }
    p {
      font-size: 0.97rem;
      color: #555;
      line-height: 1.6;
      margin-bottom: 28px;
    }
    .divider {
      border: none;
      border-top: 1px solid #eee;
      margin-bottom: 24px;
    }
    .contact {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      background: #f4f6fa;
      border-radius: 8px;
      padding: 10px 20px;
      font-size: 0.9rem;
      color: #444;
      font-weight: 500;
    }
    .contact svg { width: 18px; height: 18px; flex-shrink: 0; }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">
      <svg viewBox="0 0 24 24" fill="none" stroke="#e53935" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="10"/>
        <line x1="12" y1="8" x2="12" y2="12"/>
        <line x1="12" y1="16" x2="12.01" y2="16"/>
      </svg>
    </div>
    <h1>Access Denied</h1>
    <p>This portal kiosk is not accessible.<br>Restricted to allowed networks only.</p>
    <hr class="divider" />
    <span class="contact">
      <svg viewBox="0 0 24 24" fill="none" stroke="#555" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M22 16.92V19a2 2 0 0 1-2.18 2A19.86 19.86 0 0 1 3 4.18 2 2 0 0 1 5 2h2.09a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.9.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z"/>
      </svg>
      Contact Administrator
    </span>
  </div>
</body>
</html>"""
        )
    real_now = get_real_now()
    tz = pytz.timezone(settings.TIME_ZONE)
    aware_now = tz.localize(real_now)
    return render(
        request,
        "attendance/portal/portal.html",
        {
            "server_time_iso": aware_now.isoformat(),
            "TIME_ZONE": settings.TIME_ZONE,
        },
    )


def server_time(request):
    """
    Return the current NTP internet time as JSON so the browser clock
    cannot be spoofed by changing the client PC's system time.
    """
    real_now = get_real_now()
    tz = pytz.timezone(settings.TIME_ZONE)
    aware_now = tz.localize(real_now)
    return JsonResponse({"server_time_iso": aware_now.isoformat()})


@csrf_exempt
@require_http_methods(["POST"])
def employee_lookup(request):
    """
    Search for employees by employee_no or name.
    Returns JSON list of matching employees.

    POST params:
        query (str): Search string (name or employee no)

    Returns:
        JSON: [
            {
                "id": int,
                "employee_no": str,
                "name": str,
                "avatar": str,
                "is_clocked_in": bool
            }
        ]
    """
    if not _ip_is_allowed(request):
        return JsonResponse(
            {"success": False, "message": "Access denied: your network is not allowed."},
            status=403,
        )
    try:
        # Handle both POST data and FormData
        query = request.POST.get("query", "").strip()
        _clear_pin_verification(request)

        logger.info(f"Employee lookup query: {query}, POST data: {request.POST}")

        if not query or not re.fullmatch(r"\d{7}", query):
            return JsonResponse({"success": True, "results": []})

        # Search by employee_no only (exact match on complete 7-digit ID)
        employees = Employee.objects.filter(
            employee_no__exact=query,
            is_active=True,
        )[:10]

        results = []
        for emp in employees:
            active_activity = AttendanceActivity.objects.filter(
                employee_id=emp, clock_out__isnull=True
            ).order_by('-clock_in_date', '-clock_in').first()

            is_clocked_in = active_activity is not None
            clock_in_datetime = None
            if active_activity:
                if active_activity.in_datetime:
                    clock_in_datetime = active_activity.in_datetime.isoformat()
                elif active_activity.clock_in_date and active_activity.clock_in:
                    from datetime import datetime as _dt
                    clock_in_datetime = _dt.combine(
                        active_activity.clock_in_date, active_activity.clock_in
                    ).isoformat()

            geo_data = None
            branch_name = ""
            try:
                from geofencing.models import GeoFencing
                work_info = getattr(emp, "employee_work_info", None)
                branch = work_info.branch_id if work_info else None
                if branch:
                    branch_name = branch.branch or ""
                    geo = GeoFencing.objects.filter(branch_id=branch, start=True).first()
                    if geo and not geo.excluded_employees.filter(pk=emp.pk).exists():
                        geo_data = {
                            "geo_center_lat": geo.latitude,
                            "geo_center_lng": geo.longitude,
                            "geo_radius_meters": geo.radius_in_meters,
                        }
            except Exception:
                pass

            results.append({
                "id": emp.id,
                "employee_no": emp.employee_no or "",
                "name": emp.get_full_name(),
                "avatar": emp.get_avatar(),
                "is_clocked_in": is_clocked_in,
                "clock_in_datetime": clock_in_datetime,
                "geo_fence": geo_data,
                "branch": branch_name,
            })

        return JsonResponse({"success": True, "results": results})
    except Exception as e:
        logger.error(f"Employee lookup error: {str(e)}", exc_info=True)
        return JsonResponse(
            {"success": False, "message": f"Search error: {str(e)}"}, status=200
        )


@csrf_exempt
@require_http_methods(["POST"])
def verify_pin(request):
    """
    Verify 6-digit employee PIN before allowing clock in/out actions.

    POST params:
        employee_id (int): Employee ID
        pin (str): 6-digit numeric PIN

    Returns:
        JSON: {
            "success": bool,
            "message": str,
            "attempts_remaining": int,
            "reset_required": bool
        }
    """
    if not _ip_is_allowed(request):
        return JsonResponse(
            {"success": False, "message": "Access denied: your network is not allowed."},
            status=403,
        )

    employee_id = request.POST.get("employee_id", "").strip()
    pin = request.POST.get("pin", "").strip()

    if not employee_id:
        return JsonResponse(
            {
                "success": False,
                "message": "Employee ID required",
                "attempts_remaining": _PORTAL_PIN_ATTEMPTS_MAX,
                "reset_required": False,
            },
            status=200,
        )

    if not re.fullmatch(r"\d{6}", pin):
        return JsonResponse(
            {
                "success": False,
                "message": "PIN must be exactly 6 digits.",
                "attempts_remaining": _PORTAL_PIN_ATTEMPTS_MAX,
                "reset_required": False,
            },
            status=200,
        )

    try:
        employee = Employee.objects.get(id=employee_id, is_active=True)
    except Employee.DoesNotExist:
        return JsonResponse(
            {
                "success": False,
                "message": "Employee not found",
                "attempts_remaining": _PORTAL_PIN_ATTEMPTS_MAX,
                "reset_required": False,
            },
            status=200,
        )

    work_info = getattr(employee, "employee_work_info", None)
    stored_pin = getattr(work_info, "pin", None) if work_info else None
    stored_pin = str(stored_pin).strip() if stored_pin is not None else ""

    if not stored_pin:
        return JsonResponse(
            {
                "success": False,
                "message": "Employee PIN is not configured.",
                "attempts_remaining": _PORTAL_PIN_ATTEMPTS_MAX,
                "reset_required": False,
            },
            status=200,
        )

    if not re.fullmatch(r"\d{6}", stored_pin):
        return JsonResponse(
            {
                "success": False,
                "message": "Employee PIN is invalid. Please contact HR.",
                "attempts_remaining": _PORTAL_PIN_ATTEMPTS_MAX,
                "reset_required": False,
            },
            status=200,
        )

    if pin != stored_pin:
        _clear_pin_verification(request)
        attempts_remaining = _increment_pin_attempts(request, employee.id)
        reset_required = attempts_remaining == 0
        message = (
            "Invalid PIN. Maximum attempts reached. Please search again."
            if reset_required
            else f"Invalid PIN. Attempts remaining: {attempts_remaining}."
        )
        return JsonResponse(
            {
                "success": False,
                "message": message,
                "attempts_remaining": attempts_remaining,
                "reset_required": reset_required,
            },
            status=200,
        )

    _clear_pin_attempts(request, employee.id)
    _set_pin_verification(request, employee.id)
    return JsonResponse(
        {
            "success": True,
            "message": "PIN verified successfully.",
            "attempts_remaining": _PORTAL_PIN_ATTEMPTS_MAX,
            "reset_required": False,
        },
        status=200,
    )


@csrf_exempt
@require_http_methods(["POST"])
def public_clock_in(request):
    """
    Clock in an employee with selfie photo and GPS location.

    POST params:
        employee_id (int): Employee ID
        selfie (file): Selfie image file
        latitude (float): GPS latitude
        longitude (float): GPS longitude

    Returns:
        JSON: {
            "success": bool,
            "message": str,
            "employee_name": str,
            "clock_in_time": str
        }
    """
    if not _ip_is_allowed(request):
        return JsonResponse(
            {"success": False, "message": "Access denied: your network is not allowed."},
            status=403,
        )
    try:
        # Get employee ID from POST data
        employee_id = request.POST.get("employee_id")

        logger.info(f"Clock in request - employee_id: {employee_id}, POST keys: {list(request.POST.keys())}, FILES keys: {list(request.FILES.keys())}")

        if not employee_id:
            return JsonResponse(
                {"success": False, "message": "Employee ID required"}, status=200
            )

        has_verified_pin, pin_message = _require_verified_pin(request, employee_id)
        if not has_verified_pin:
            return JsonResponse({"success": False, "message": pin_message}, status=200)

        # Require GPS location
        latitude = request.POST.get("latitude")
        longitude = request.POST.get("longitude")
        if not latitude or not longitude:
            return JsonResponse(
                {"success": False, "message": "Location is required to clock in. Please enable location access and try again."},
                status=200,
            )

        # Find employee
        try:
            employee = Employee.objects.get(id=employee_id, is_active=True)
        except Employee.DoesNotExist:
            logger.warning(f"Employee not found: {employee_id}")
            return JsonResponse(
                {"success": False, "message": "Employee not found"}, status=200
            )

        # Check if employee has work info
        work_info = getattr(employee, 'employee_work_info', None)

        if not work_info or work_info is None:
            logger.warning(f"No work info for employee: {employee_id}")
            return JsonResponse(
                {"success": False, "message": "Employee work information not configured"},
                status=200,
            )

        # Geofence check
        geo_error = _geofence_check(employee, work_info, latitude, longitude)
        if geo_error:
            return JsonResponse({"success": False, "geo_fence_violation": True, **geo_error}, status=200)

        # Check if already clocked in
        if AttendanceActivity.objects.filter(
            employee_id=employee, clock_out__isnull=True
        ).exists():
            logger.info(f"Employee already clocked in: {employee_id}")
            return JsonResponse(
                {"success": False, "message": f"{employee.get_full_name()} is already clocked in"},
                status=200,
            )

        # Get shift and schedule info
        shift = work_info.shift_id

        if not shift:
            logger.warning(f"No shift configured for employee: {employee_id}")
            return JsonResponse(
                {"success": False, "message": "Employee shift not configured"}, status=200
            )

        # Get current date and time from NTP (tamper-proof)
        datetime_now = get_real_now()
        date_today = datetime_now.date()
        day_name = date_today.strftime("%A").lower()

        try:
            day = EmployeeShiftDay.objects.get(day=day_name)
        except EmployeeShiftDay.DoesNotExist:
            logger.error(f"Shift day configuration error for {day_name}")
            return JsonResponse(
                {"success": False, "message": "Shift day configuration error"}, status=200
            )

        # Get shift schedule
        now_str = datetime_now.strftime("%H:%M")
        now_sec = strtime_seconds(now_str)
        mid_day_sec = strtime_seconds("12:00")
        minimum_hour, start_time_sec, end_time_sec = shift_schedule_today(
            day=day, shift=shift
        )

        # Handle night shift
        attendance_date = date_today
        if start_time_sec > end_time_sec:  # Night shift
            if mid_day_sec > now_sec:
                # Before noon - belongs to yesterday's shift
                date_yesterday = date_today - timedelta(days=1)
                day_yesterday = date_yesterday.strftime("%A").lower()

                try:
                    day = EmployeeShiftDay.objects.get(day=day_yesterday)
                except EmployeeShiftDay.DoesNotExist:
                    logger.error(f"Shift day configuration error for {day_yesterday}")
                    return JsonResponse(
                        {"success": False, "message": "Shift day configuration error"},
                        status=200,
                    )

                minimum_hour, start_time_sec, end_time_sec = shift_schedule_today(
                    day=day, shift=shift
                )
                attendance_date = date_yesterday

        # Call the business logic function
        try:
            attendance = clock_in_attendance_and_activity(
                employee=employee,
                date_today=date_today,
                attendance_date=attendance_date,
                day=day,
                now=now_str,
                shift=shift,
                minimum_hour=minimum_hour,
                start_time=start_time_sec,
                end_time=end_time_sec,
                in_datetime=datetime_now,
            )
        except Exception as e:
            logger.error(f"Clock in error for {employee.id}: {str(e)}", exc_info=True)
            return JsonResponse(
                {"success": False, "message": f"Clock in failed: {str(e)}"}, status=200
            )

        # Get the newly created activity and ensure location_verified is set
        activity = AttendanceActivity.objects.filter(
            employee_id=employee, clock_out__isnull=True
        ).order_by("attendance_date", "id").last()

        if not activity:
            logger.error(f"Activity not created for employee {employee.id}")
            return JsonResponse(
                {"success": False, "message": "Activity creation failed"}, status=200
            )

        # Ensure location_verified has a value (required field)
        if activity.location_verified is None:
            activity.location_verified = False
            activity.save()

        # Save selfie photo
        if "selfie" in request.FILES:
            activity.clock_in_selfie = request.FILES["selfie"]

        # Save GPS location
        try:
            latitude = request.POST.get("latitude")
            longitude = request.POST.get("longitude")
            if latitude and longitude:
                lat_f = float(latitude)
                lng_f = float(longitude)
                activity.clock_in_latitude = lat_f
                activity.clock_in_longitude = lng_f
                activity.clock_in_gps_address = _reverse_geocode(lat_f, lng_f)
                activity.latitude = lat_f
                activity.longitude = lng_f
                activity.gps_address = activity.clock_in_gps_address
                activity.location_verified = True
            else:
                activity.location_verified = False
        except (ValueError, TypeError):
            activity.location_verified = False

        activity.save()
        _clear_pin_verification(request)

        return JsonResponse({
            "success": True,
            "message": f"{employee.get_full_name()} clocked in successfully",
            "employee_name": employee.get_full_name(),
            "clock_in_time": datetime_now.strftime("%I:%M %p"),
        })

    except Exception as e:
        logger.error(f"Public clock in error: {str(e)}")
        return JsonResponse(
            {"success": False, "message": f"Error: {str(e)}"}, status=500
        )


@csrf_exempt
@require_http_methods(["POST"])
def public_clock_out(request):
    """
    Clock out an employee with selfie photo and GPS location.

    POST params:
        employee_id (int): Employee ID
        selfie (file): Selfie image file
        latitude (float): GPS latitude
        longitude (float): GPS longitude

    Returns:
        JSON: {
            "success": bool,
            "message": str,
            "employee_name": str,
            "clock_out_time": str,
            "worked_hours": str
        }
    """
    if not _ip_is_allowed(request):
        return JsonResponse(
            {"success": False, "message": "Access denied: your network is not allowed."},
            status=403,
        )
    try:
        # Get employee ID from POST data
        employee_id = request.POST.get("employee_id")

        logger.info(f"Clock out request - employee_id: {employee_id}")

        if not employee_id:
            return JsonResponse(
                {"success": False, "message": "Employee ID required"}, status=200
            )

        has_verified_pin, pin_message = _require_verified_pin(request, employee_id)
        if not has_verified_pin:
            return JsonResponse({"success": False, "message": pin_message}, status=200)

        # Require GPS location
        latitude = request.POST.get("latitude")
        longitude = request.POST.get("longitude")
        if not latitude or not longitude:
            return JsonResponse(
                {"success": False, "message": "Location is required to clock out. Please enable location access and try again."},
                status=200,
            )

        # Find employee
        try:
            employee = Employee.objects.get(id=employee_id, is_active=True)
        except Employee.DoesNotExist:
            logger.warning(f"Clock out - Employee not found: {employee_id}")
            return JsonResponse(
                {"success": False, "message": "Employee not found"}, status=200
            )

        # Geofence check
        work_info_out = getattr(employee, "employee_work_info", None)
        geo_error = _geofence_check(employee, work_info_out, latitude, longitude)
        if geo_error:
            return JsonResponse({"success": False, "geo_fence_violation": True, **geo_error}, status=200)

        # Check if employee is clocked in
        open_activity = AttendanceActivity.objects.filter(
            employee_id=employee, clock_out__isnull=True
        ).order_by("attendance_date", "id").last()

        if not open_activity:
            logger.info(f"Clock out - Employee not clocked in: {employee_id}")
            return JsonResponse(
                {"success": False, "message": f"{employee.get_full_name()} is not clocked in"},
                status=200,
            )

        # Get current date and time from NTP (tamper-proof)
        datetime_now = get_real_now()
        date_today = datetime_now.date()
        now_str = datetime_now.strftime("%H:%M")

        # Call the business logic function
        try:
            attendance = clock_out_attendance_and_activity(
                employee=employee,
                date_today=date_today,
                now=now_str,
                out_datetime=datetime_now,
                auto_validate=False,
            )
        except Exception as e:
            logger.error(f"Clock out error for {employee.id}: {str(e)}", exc_info=True)
            return JsonResponse(
                {"success": False, "message": f"Clock out failed: {str(e)}"}, status=200
            )

        if not attendance:
            logger.warning(f"No active attendance found for employee {employee.id}")
            return JsonResponse(
                {"success": False, "message": "No active attendance found"}, status=200
            )

        # Get the just-closed activity and update it
        open_activity.refresh_from_db()
        closed_activity = open_activity

        # Ensure location_verified has a value (required field)
        if closed_activity and closed_activity.location_verified is None:
            closed_activity.location_verified = False

        if closed_activity and closed_activity.clock_out:
            # Save selfie photo
            if "selfie" in request.FILES:
                closed_activity.clock_out_selfie = request.FILES["selfie"]

            # Save GPS location
            try:
                latitude = request.POST.get("latitude")
                longitude = request.POST.get("longitude")
                if latitude and longitude:
                    lat_f = float(latitude)
                    lng_f = float(longitude)
                    closed_activity.clock_out_latitude = lat_f
                    closed_activity.clock_out_longitude = lng_f
                    closed_activity.clock_out_gps_address = _reverse_geocode(lat_f, lng_f)
                    closed_activity.latitude = lat_f
                    closed_activity.longitude = lng_f
                    closed_activity.gps_address = closed_activity.clock_out_gps_address
                    closed_activity.location_verified = True
            except (ValueError, TypeError):
                pass

            closed_activity.save()

        _clear_pin_verification(request)
        return JsonResponse({
            "success": True,
            "message": f"{employee.get_full_name()} clocked out successfully",
            "employee_name": employee.get_full_name(),
            "clock_out_time": datetime_now.strftime("%I:%M %p"),
            "worked_hours": attendance.attendance_worked_hour or "00:00",
        })

    except Exception as e:
        logger.error(f"Public clock out error: {str(e)}", exc_info=True)
        return JsonResponse(
            {"success": False, "message": f"Error: {str(e)}"}, status=200
        )
