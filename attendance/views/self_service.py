"""
self_service.py

Public employee self-service clock in/out views.
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

# NTP servers to try in order
_NTP_SERVERS = ["time.cloudflare.com", "pool.ntp.org", "time.google.com"]
_NTP_DELTA = 2208988800  # seconds between NTP epoch (1900) and Unix epoch (1970)


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


def public_self_service(request):
    """
    Render the public self-service clock in/out page.
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
    <p>This self-service kiosk is not accessible.<br>Restricted to allowed networks only.</p>
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
        "attendance/self_service/self_service.html",
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
    Search for employees by badge_id or name.
    Returns JSON list of matching employees.

    POST params:
        query (str): Search string (name or badge ID)

    Returns:
        JSON: [
            {
                "id": int,
                "badge_id": str,
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

        logger.info(f"Employee lookup query: {query}, POST data: {request.POST}")

        if not query or not re.fullmatch(r"\d{7}", query):
            return JsonResponse({"success": True, "results": []})

        # Search by badge_id only (exact match on complete 7-digit ID)
        employees = Employee.objects.filter(
            badge_id__exact=query,
            is_active=True,
        )[:10]

        results = []
        for emp in employees:
            # Check if employee is currently clocked in
            is_clocked_in = AttendanceActivity.objects.filter(
                employee_id=emp, clock_out__isnull=True
            ).exists()

            results.append({
                "id": emp.id,
                "badge_id": emp.badge_id or "",
                "name": emp.get_full_name(),
                "avatar": emp.get_avatar(),
                "is_clocked_in": is_clocked_in,
            })

        return JsonResponse({"success": True, "results": results})
    except Exception as e:
        logger.error(f"Employee lookup error: {str(e)}", exc_info=True)
        return JsonResponse(
            {"success": False, "message": f"Search error: {str(e)}"}, status=200
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
        ).last()

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
                activity.latitude = float(latitude)
                activity.longitude = float(longitude)
                activity.location_verified = True
            else:
                activity.location_verified = False
        except (ValueError, TypeError):
            activity.location_verified = False

        activity.save()

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

        # Check if employee is clocked in
        open_activity = AttendanceActivity.objects.filter(
            employee_id=employee, clock_out__isnull=True
        ).last()

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
        closed_activity = AttendanceActivity.objects.filter(
            employee_id=employee, attendance_date=attendance.attendance_date
        ).order_by("-id").first()

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
                    closed_activity.latitude = float(latitude)
                    closed_activity.longitude = float(longitude)
                    closed_activity.location_verified = True
                else:
                    closed_activity.location_verified = False
            except (ValueError, TypeError):
                closed_activity.location_verified = False

            closed_activity.save()

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
