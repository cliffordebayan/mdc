"""
portal.py

Public employee portal clock in/out views.
No authentication required - suitable for kiosk-style access.
Employees identify themselves by badge ID or name.
"""

import os
import ipaddress
import logging
import re
import socket
import struct
import threading
import time as time_module
from datetime import date, datetime, time, timedelta

import pytz
from django import forms
from django.apps import apps
from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core import signing
from django.core.mail import EmailMessage
from django.core.signing import BadSignature, SignatureExpired
from django.db import DatabaseError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import strip_tags
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.translation import gettext as _
from geopy.distance import geodesic
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from attendance.methods.utils import (
    calculate_worked_hours,
    format_time,
    shift_schedule_today,
    shift_schedule_with_weekday_fallback,
    strtime_seconds,
)
from attendance.models import (
    Attendance,
    AttendanceActivity,
    AttendanceGeneralSetting,
    AttendancePortalMultiPunchEmployee,
    AttendancePortalShiftContext,
    AttendancePortalShiftOverride,
    AttendanceRequestComment,
    AttendanceRequestFile,
)
from attendance.views.clock_in_out import (
    clock_in_attendance_and_activity,
    clock_out_attendance_and_activity,
    early_out,
    late_come,
)
from base.backends import ConfiguredEmailBackend
from base.models import (
    AttendanceAllowedIP,
    Company,
    EmployeeShiftDay,
    EmployeeShift,
    EmployeeShiftSchedule,
)
from employee.models import Employee, EmployeeBankDetails, EmployeeInsurance

logger = logging.getLogger(__name__)


class PortalShiftScheduleError(Exception):
    """Expected failure while resolving a portal shift schedule."""

PORTAL_WORK_ACTIVITY = "work"
PORTAL_BREAK_ACTIVITY = "break"
PORTAL_LUNCH_ACTIVITY = "lunch"
PORTAL_MULTI_MORNING_IN = "morning_in"
PORTAL_MULTI_MORNING_OUT = "morning_out"
PORTAL_MULTI_AFTERNOON_IN = "afternoon_in"
PORTAL_MULTI_AFTERNOON_OUT = "afternoon_out"
PORTAL_MULTI_COMPLETE = "complete"
PORTAL_MULTI_MORNING_OUT_START = time(11, 0)
PORTAL_MULTI_MORNING_OUT_END = time(13, 0)
PORTAL_MULTI_AFTERNOON_IN_START = time(12, 0)
PORTAL_MULTI_AFTERNOON_IN_END = time(14, 0)
PORTAL_MULTI_AFTERNOON_OUT_FALLBACK_START = time(14, 0)
PORTAL_DEFAULT_BREAK_LIMIT = 2
PORTAL_WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
PORTAL_DEFAULT_BREAK_MINUTES = 15
PORTAL_DEFAULT_LUNCH_MINUTES = 60
PORTAL_HELPDESK_BLOCKED_EXTENSIONS = {
    ".html",
    ".htm",
    ".js",
    ".svg",
    ".xml",
    ".php",
    ".py",
    ".sh",
    ".exe",
}
PORTAL_NON_WORK_ACTIVITY_TYPES = {
    PORTAL_BREAK_ACTIVITY: _("Break"),
    PORTAL_LUNCH_ACTIVITY: _("Lunch"),
}
PORTAL_MULTI_PUNCH_LABELS = {
    PORTAL_MULTI_MORNING_IN: _("Morning Clock In"),
    PORTAL_MULTI_MORNING_OUT: _("Morning Clock Out"),
    PORTAL_MULTI_AFTERNOON_IN: _("Afternoon Clock In"),
    PORTAL_MULTI_AFTERNOON_OUT: _("Afternoon Clock Out"),
}


def _portal_cache_timeout(name, default):
    try:
        return max(int(getattr(settings, name, default)), 0)
    except (TypeError, ValueError):
        return default


_PORTAL_REVERSE_GEOCODER = None
_PORTAL_REVERSE_GEOCODER_LOCK = threading.Lock()


def _reverse_geocode(lat, lng):
    if not getattr(settings, "PORTAL_REVERSE_GEOCODE_ENABLED", True):
        return None

    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except (TypeError, ValueError):
        return None

    cache_key = f"attendance_portal:reverse_geocode:{round(lat_f, 5)}:{round(lng_f, 5)}"
    cached_address = cache.get(cache_key)
    if cached_address is not None:
        return cached_address or None

    try:
        global _PORTAL_REVERSE_GEOCODER
        from geopy.geocoders import Nominatim

        if _PORTAL_REVERSE_GEOCODER is None:
            with _PORTAL_REVERSE_GEOCODER_LOCK:
                if _PORTAL_REVERSE_GEOCODER is None:
                    _PORTAL_REVERSE_GEOCODER = Nominatim(user_agent="hris_attendance")

        location = _PORTAL_REVERSE_GEOCODER.reverse(
            (lat_f, lng_f),
            exactly_one=True,
            timeout=getattr(settings, "PORTAL_REVERSE_GEOCODE_TIMEOUT", 1),
        )
        address = location.address if location else None
        cache.set(
            cache_key,
            address or "",
            _portal_cache_timeout("PORTAL_REVERSE_GEOCODE_CACHE_SECONDS", 86400),
        )
        return address
    except Exception:
        return None


# NTP servers to try in order
_NTP_SERVERS = ["time.cloudflare.com", "pool.ntp.org", "time.google.com"]
_NTP_DELTA = 2208988800  # seconds between NTP epoch (1900) and Unix epoch (1970)
_PORTAL_NTP_CACHE_LOCK = threading.Lock()
_PORTAL_NTP_CACHE = {
    "synced_at": None,
    "monotonic_at": None,
}
_PORTAL_PIN_ATTEMPTS_MAX = 3
_PORTAL_PIN_ATTEMPTS_SESSION_KEY = "portal_pin_attempts"
_PORTAL_PIN_VERIFICATION_SESSION_KEY = "portal_pin_verification"
_PORTAL_PIN_RESET_SALT = "attendance.portal.pin-reset"
_PORTAL_PIN_RESET_TIMEOUT_SECONDS = 86400
_PORTAL_PIN_RESET_GENERIC_MESSAGE = _(
    "If an active employee matches that email, a PIN reset link has been sent."
)


class PortalForgotPINForm(forms.Form):
    email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={
                "class": "oh-input w-100",
                "placeholder": "example@mail.com",
                "autocomplete": "email",
                "autofocus": "autofocus",
            }
        )
    )


class PortalResetPINForm(forms.Form):
    new_pin = forms.CharField(
        max_length=6,
        min_length=6,
        widget=forms.PasswordInput(
            attrs={
                "class": "oh-input w-100",
                "maxlength": "6",
                "minlength": "6",
                "placeholder": "123456",
                "pattern": r"\d{6}",
                "inputmode": "numeric",
                "autocomplete": "new-password",
            },
            render_value=True,
        ),
    )
    confirm_pin = forms.CharField(
        max_length=6,
        min_length=6,
        widget=forms.PasswordInput(
            attrs={
                "class": "oh-input w-100",
                "maxlength": "6",
                "minlength": "6",
                "placeholder": "123456",
                "pattern": r"\d{6}",
                "inputmode": "numeric",
                "autocomplete": "new-password",
            },
            render_value=True,
        ),
    )

    def clean_new_pin(self):
        pin = self.cleaned_data.get("new_pin", "")
        if not re.fullmatch(r"\d{6}", pin):
            raise forms.ValidationError(_("PIN must be exactly 6 digits."))
        return pin

    def clean_confirm_pin(self):
        pin = self.cleaned_data.get("confirm_pin", "")
        if not re.fullmatch(r"\d{6}", pin):
            raise forms.ValidationError(_("PIN must be exactly 6 digits."))
        return pin

    def clean(self):
        cleaned_data = super().clean()
        new_pin = cleaned_data.get("new_pin")
        confirm_pin = cleaned_data.get("confirm_pin")
        if new_pin and confirm_pin and new_pin != confirm_pin:
            raise forms.ValidationError(_("PIN values do not match."))
        return cleaned_data


def _portal_pin_reset_timeout():
    return int(
        getattr(
            settings,
            "PORTAL_PIN_RESET_TIMEOUT",
            _PORTAL_PIN_RESET_TIMEOUT_SECONDS,
        )
    )


def _portal_employee_id(employee):
    return str(getattr(employee, "pk", None) or getattr(employee, "id", ""))


def _portal_employee_work_email(employee):
    work_info = getattr(employee, "employee_work_info", None)
    return (getattr(work_info, "email", None) or "").strip()


def _portal_employee_personal_email(employee):
    return (getattr(employee, "email", None) or "").strip()


def _portal_employee_preferred_email(employee):
    return _portal_employee_work_email(employee) or _portal_employee_personal_email(employee)


def _portal_pin_reset_digest(employee):
    work_info = getattr(employee, "employee_work_info", None)
    pin = (getattr(work_info, "pin", None) or "").strip()
    digest_value = ":".join(
        [
            _portal_employee_id(employee),
            _portal_employee_personal_email(employee).lower(),
            _portal_employee_work_email(employee).lower(),
            pin,
        ]
    )
    return salted_hmac(_PORTAL_PIN_RESET_SALT, digest_value).hexdigest()


def _make_portal_pin_reset_token(employee):
    return signing.dumps(
        {
            "employee_id": _portal_employee_id(employee),
            "digest": _portal_pin_reset_digest(employee),
        },
        salt=_PORTAL_PIN_RESET_SALT,
    )


def _get_portal_pin_reset_employee(token):
    try:
        payload = signing.loads(
            token,
            salt=_PORTAL_PIN_RESET_SALT,
            max_age=_portal_pin_reset_timeout(),
        )
    except SignatureExpired:
        return None, _("This PIN reset link has expired.")
    except BadSignature:
        return None, _("This PIN reset link is invalid.")

    employee_id = payload.get("employee_id")
    token_digest = payload.get("digest", "")
    if not employee_id or not token_digest:
        return None, _("This PIN reset link is invalid.")

    try:
        employee = Employee.objects.get(pk=employee_id, is_active=True)
    except Employee.DoesNotExist:
        return None, _("This PIN reset link is invalid.")

    current_digest = _portal_pin_reset_digest(employee)
    if not constant_time_compare(token_digest, current_digest):
        return None, _("This PIN reset link is no longer valid.")

    return employee, ""


def _get_portal_pin_reset_employees(email):
    return Employee.objects.filter(
        Q(email__iexact=email) | Q(employee_work_info__email__iexact=email),
        is_active=True,
    ).distinct()


def _send_portal_pin_reset_email(request, employee):
    send_to_mail = _portal_employee_preferred_email(employee)
    if not send_to_mail:
        return False

    token = _make_portal_pin_reset_token(employee)
    reset_url = request.build_absolute_uri(
        reverse("portal-reset-pin", kwargs={"token": token})
    )
    portal_url = request.build_absolute_uri(reverse("public-portal"))
    subject = _("Reset your attendance portal PIN")
    html_message = render_to_string(
        "attendance/portal/pin_reset_email.html",
        {
            "employee": employee,
            "reset_url": reset_url,
            "portal_url": portal_url,
        },
    )
    email_backend = ConfiguredEmailBackend()
    email = EmailMessage(
        subject=str(subject),
        body=html_message,
        from_email=email_backend.dynamic_from_email_with_display_name,
        to=[send_to_mail],
    )
    email.content_subtype = "html"

    try:
        email.send()
        return True
    except Exception:
        logger.exception("Failed to send attendance portal PIN reset email")
        return False


def _get_real_now_uncached():
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
            sock.settimeout(getattr(settings, "PORTAL_NTP_TIMEOUT", 1))
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
    # All NTP servers failed - fall back to system time and log a warning
    logger.warning("NTP sync failed; falling back to system clock for attendance time")
    return datetime.now()


def get_real_now():
    """
    Return portal time from NTP with a short monotonic cache.

    The cache keeps the anti-client-clock behavior while avoiding a UDP request
    to public NTP servers on every portal request.

    The lookup itself runs in a background thread with a hard overall timeout
    (PORTAL_NTP_TIMEOUT_TOTAL) so a slow/blocked network - e.g. UDP/123 filtered
    or DNS to the NTP hosts hanging, as can happen on cellular carriers - can
    never stall a request beyond that bound. socket.settimeout() alone doesn't
    cover this because it only bounds the UDP send/recv, not the hostname
    resolution sendto() performs internally. If the timeout is hit we fall back
    to the system clock for this request only, while the thread keeps running
    to populate the cache for subsequent requests once it finishes.
    """
    monotonic_now = time_module.monotonic()
    max_age = _portal_cache_timeout("PORTAL_NTP_CACHE_SECONDS", 300)
    cached_synced_at = _PORTAL_NTP_CACHE.get("synced_at")
    cached_monotonic_at = _PORTAL_NTP_CACHE.get("monotonic_at")

    if cached_synced_at is not None and cached_monotonic_at is not None:
        age = monotonic_now - cached_monotonic_at
        if 0 <= age <= max_age:
            return cached_synced_at + timedelta(seconds=age)

        if not _PORTAL_NTP_CACHE_LOCK.acquire(False):
            return cached_synced_at + timedelta(seconds=max(age, 0))
    else:
        _PORTAL_NTP_CACHE_LOCK.acquire()

    try:
        monotonic_now = time_module.monotonic()
        cached_synced_at = _PORTAL_NTP_CACHE.get("synced_at")
        cached_monotonic_at = _PORTAL_NTP_CACHE.get("monotonic_at")
        if cached_synced_at is not None and cached_monotonic_at is not None:
            age = monotonic_now - cached_monotonic_at
            if 0 <= age <= max_age:
                return cached_synced_at + timedelta(seconds=age)

        result = {}

        def _sync():
            synced_now = _get_real_now_uncached()
            result["synced_now"] = synced_now
            _PORTAL_NTP_CACHE["synced_at"] = synced_now
            _PORTAL_NTP_CACHE["monotonic_at"] = time_module.monotonic()

        sync_thread = threading.Thread(target=_sync, daemon=True)
        sync_thread.start()
        sync_thread.join(_portal_cache_timeout("PORTAL_NTP_TIMEOUT_TOTAL", 2))

        if "synced_now" in result:
            return result["synced_now"]

        logger.warning(
            "NTP sync exceeded PORTAL_NTP_TIMEOUT_TOTAL; using system clock "
            "for this request while the sync finishes in the background"
        )
        return datetime.now()
    finally:
        _PORTAL_NTP_CACHE_LOCK.release()


def _get_client_ip(request):
    """
    Return the real client IP, checking proxy/CDN headers in priority order:
      1. CF-Connecting-IP  - set by Cloudflare, most reliable when behind CF
      2. X-Real-IP         - set by nginx and other single-hop proxies
      3. X-Forwarded-For   - leftmost (original client) entry in the chain
      4. REMOTE_ADDR       - direct connection fallback
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


def _active_global_geofences(employee=None):
    """Return active geofences that apply to ``employee``.

    Geofences are opt-in per employee.  The optional cached attribute is kept
    for lightweight callers and tests, but database-backed employees are
    filtered through the explicit many-to-many assignment.
    """
    if (
        employee is not None
        and "active_global_geofences" in getattr(employee, "__dict__", {})
    ):
        return employee.active_global_geofences

    try:
        GeoFencing = apps.get_model("geofencing", "GeoFencing")
    except LookupError:
        return []

    geofences = GeoFencing.objects.filter(start=True)
    if employee is not None:
        geofences = geofences.filter(assigned_employees=employee)
    return geofences.prefetch_related("excluded_employees")


def _active_assigned_geofences(employee):
    return _active_global_geofences(employee)


def _portal_face_detection_required(employee):
    """Portal attendance always requires local face presence detection."""
    return True


def _employee_is_excluded_from_geofence(employee, geofence):
    excluded_employees = getattr(geofence, "excluded_employees", None)
    if excluded_employees is None:
        return False

    employee_pk = getattr(employee, "pk", None) or getattr(employee, "id", None)

    try:
        if hasattr(excluded_employees, "filter"):
            return excluded_employees.filter(pk=employee_pk).exists()
    except Exception:
        return False

    try:
        return any(
            (getattr(excluded_employee, "pk", None) or getattr(excluded_employee, "id", None))
            == employee_pk
            for excluded_employee in excluded_employees
        )
    except TypeError:
        return False


def _enforced_assigned_geofences(employee):
    """
    Return active geofences explicitly assigned to this employee. Empty means
    the employee has no geofence restriction.
    """
    enforced_geofences = []
    for geofence in _active_assigned_geofences(employee):
        if not getattr(geofence, "start", True):
            continue
        if _employee_is_excluded_from_geofence(employee, geofence):
            continue
        enforced_geofences.append(geofence)
    return enforced_geofences


def _geofence_check(employee, work_info, latitude, longitude):
    """
    Return a dict with error details if the employee is outside every active
    global geofence, or None if the clock action should be allowed.
    Employees with no enforced global geofences are always allowed.
    """
    try:
        enforced_geos = _enforced_assigned_geofences(employee)
    except Exception:
        return None

    if not enforced_geos:
        return None

    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return {
            "message": "Waiting for location...",
            "location_pending": True,
        }

    try:
        inside = False
        nearest_geo = None
        min_distance = float('inf')
        for geo in enforced_geos:
            distance = geodesic(
                (geo.latitude, geo.longitude),
                (latitude, longitude),
            ).meters
            if distance < min_distance:
                min_distance = distance
                nearest_geo = geo
            if distance <= geo.radius_in_meters:
                inside = True
                break
        if not inside:
            return {
                "message": "You are outside the active geofenced locations.",
                "geo_center_lat": nearest_geo.latitude if nearest_geo else None,
                "geo_center_lng": nearest_geo.longitude if nearest_geo else None,
                "geo_radius_meters": nearest_geo.radius_in_meters if nearest_geo else None,
                "user_lat": latitude,
                "user_lng": longitude,
            }
    except Exception:
        pass
    return None


def _portal_geo_error_response(geo_error):
    response_data = {"success": False, **geo_error}
    if not geo_error.get("location_pending"):
        response_data["geo_fence_violation"] = True
    return JsonResponse(response_data, status=200)


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
    Return active PIN verification from session, or None if invalid.
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


def _activity_datetime_iso(activity, date_field, time_field, datetime_field):
    if not activity:
        return None

    direct_datetime = getattr(activity, datetime_field, None)
    if isinstance(direct_datetime, datetime):
        return direct_datetime.isoformat()

    activity_date = getattr(activity, date_field, None)
    activity_time = getattr(activity, time_field, None)
    if isinstance(activity_date, date) and isinstance(activity_time, time):
        return datetime.combine(activity_date, activity_time).isoformat()

    return None


def _portal_activity_type(activity):
    activity_type = getattr(activity, "activity_type", PORTAL_WORK_ACTIVITY)
    if activity_type in {
        PORTAL_WORK_ACTIVITY,
        PORTAL_BREAK_ACTIVITY,
        PORTAL_LUNCH_ACTIVITY,
    }:
        return activity_type
    return PORTAL_WORK_ACTIVITY


def _open_portal_activity(employee):
    return (
        AttendanceActivity.objects.filter(employee_id=employee, clock_out__isnull=True)
        .order_by("-clock_in_date", "-clock_in")
        .first()
    )


def _latest_portal_activity(employee):
    return (
        AttendanceActivity.objects.filter(employee_id=employee)
        .order_by(
            "-attendance_date",
            "-clock_out_date",
            "-clock_out",
            "-clock_in_date",
            "-clock_in",
            "-id",
        )
        .first()
    )


def _latest_portal_activity_for_date(employee, attendance_date):
    return (
        AttendanceActivity.objects.filter(
            employee_id=employee,
            attendance_date=attendance_date,
        )
        .order_by("-clock_out_date", "-clock_out", "-clock_in_date", "-clock_in", "-id")
        .first()
    )


def _portal_multi_punch_enabled(employee):
    try:
        return AttendancePortalMultiPunchEmployee.objects.filter(
            employee_id=employee,
            is_active=True,
        ).exists()
    except Exception:
        return False


def _portal_work_activities_for_date(employee, attendance_date):
    if not attendance_date:
        return []
    return list(
        AttendanceActivity.objects.filter(
            employee_id=employee,
            attendance_date=attendance_date,
            activity_type=PORTAL_WORK_ACTIVITY,
        ).order_by("clock_in_date", "clock_in", "id")
    )


def _portal_multi_punch_time_in_window(current_clock, start, end):
    return bool(current_clock and start <= current_clock < end)


def _portal_day_index(day_name):
    return {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }.get(day_name, -1)


def _portal_shift_duration_seconds(start_time, end_time):
    start_seconds = start_time.hour * 3600 + start_time.minute * 60 + start_time.second
    end_seconds = end_time.hour * 3600 + end_time.minute * 60 + end_time.second
    duration = end_seconds - start_seconds
    if duration < 0:
        duration += 24 * 60 * 60
    return duration


def _portal_copy_shift_context(employee, base_shift):
    context = AttendancePortalShiftContext._base_manager.filter(
        employee_id=employee,
    ).select_related("shift_id").first()
    if context:
        return context

    work_info = getattr(employee, "employee_work_info", None)
    portal_shift = EmployeeShift.objects.create(
        employee_shift=f"{employee.employee_no or employee.pk}_shift",
        weekly_full_time=getattr(base_shift, "weekly_full_time", "40:00"),
        full_time=getattr(base_shift, "full_time", "200:00"),
        grace_time_id=getattr(base_shift, "grace_time_id", None),
    )
    company_id = getattr(work_info, "company_id_id", None) if work_info else None
    if isinstance(company_id, int):
        portal_shift.company_id.add(company_id)

    return AttendancePortalShiftContext._base_manager.create(
        employee_id=employee,
        shift_id=portal_shift,
    )


def _portal_override_map(employee):
    return {
        override.day.day: override
        for override in AttendancePortalShiftOverride._base_manager.filter(
            employee_id=employee,
            is_active=True,
        ).select_related("day")
    }


def _portal_base_schedule(employee, schedule_date):
    work_info = getattr(employee, "employee_work_info", None)
    base_shift = getattr(work_info, "shift_id", None) if work_info else None
    if not base_shift:
        return base_shift, None, None
    day_name = schedule_date.strftime("%A").lower()
    day = EmployeeShiftDay.objects.filter(day=day_name).first()
    if not day:
        return base_shift, day, None

    minimum_hour, start_time_sec, end_time_sec = shift_schedule_today(
        day=day,
        shift=base_shift,
    )
    if start_time_sec == 0 and end_time_sec == 0:
        return base_shift, day, None

    def seconds_to_time(value):
        value = value % (24 * 60 * 60)
        return time(value // 3600, (value % 3600) // 60, value % 60)

    start_time = seconds_to_time(start_time_sec)
    end_time = seconds_to_time(end_time_sec)
    source_schedule = None
    if isinstance(getattr(base_shift, "pk", None), int) and isinstance(
        getattr(day, "pk", None), int
    ):
        source_schedule = shift_schedule_with_weekday_fallback(
            day=day,
            shift=base_shift,
        )
    return (
        base_shift,
        day,
        {
            "start_time": start_time,
            "end_time": end_time,
            "minimum_working_hour": minimum_hour,
            "is_night_shift": start_time > end_time,
            "is_auto_punch_out_enabled": bool(
                getattr(source_schedule, "is_auto_punch_out_enabled", False)
            ),
            "auto_punch_out_time": getattr(source_schedule, "auto_punch_out_time", None),
            "override_day": None,
        },
    )


def _portal_effective_schedule(employee, schedule_date):
    base_shift, day, base_schedule = _portal_base_schedule(employee, schedule_date)
    if not base_shift or not day:
        return base_shift, day, None

    if isinstance(getattr(base_shift, "pk", None), int) and isinstance(
        getattr(employee, "pk", None), int
    ):
        overrides = _portal_override_map(employee)
    else:
        overrides = {}
    day_name = schedule_date.strftime("%A").lower()
    target_index = _portal_day_index(day_name)
    selected_override = None
    if target_index >= 0:
        for candidate_day in reversed(PORTAL_WEEKDAYS[: target_index + 1]):
            if candidate_day in overrides:
                selected_override = overrides[candidate_day]
                break

    if selected_override:
        return (
            base_shift,
            day,
            {
                "start_time": selected_override.start_time,
                "end_time": selected_override.end_time,
                "minimum_working_hour": selected_override.minimum_working_hour,
                "is_night_shift": selected_override.is_night_shift,
                "is_auto_punch_out_enabled": bool(
                    base_schedule.get("is_auto_punch_out_enabled", False)
                )
                if base_schedule
                else False,
                "auto_punch_out_time": base_schedule.get("auto_punch_out_time")
                if base_schedule
                else None,
                "override_day": selected_override.day.day,
            },
        )

    if not base_schedule:
        return base_shift, day, None
    return base_shift, day, base_schedule


def _portal_sync_shift_schedule(portal_shift, day, schedule):
    portal_schedule = EmployeeShiftSchedule._base_manager.filter(
        shift_id=portal_shift,
        day=day,
    ).first()
    if not portal_schedule:
        portal_schedule = EmployeeShiftSchedule(
            shift_id=portal_shift,
            day=day,
        )
    portal_schedule.minimum_working_hour = schedule["minimum_working_hour"]
    portal_schedule.start_time = schedule["start_time"]
    portal_schedule.end_time = schedule["end_time"]
    portal_schedule.is_night_shift = schedule["is_night_shift"]
    portal_schedule.is_rest_day = False
    portal_schedule.is_auto_punch_out_enabled = schedule.get(
        "is_auto_punch_out_enabled", False
    )
    portal_schedule.auto_punch_out_time = schedule.get("auto_punch_out_time")
    portal_schedule.save()
    return portal_schedule


def _portal_schedule_context(employee, schedule_date, persist=False):
    base_shift, day, schedule = _portal_effective_schedule(employee, schedule_date)
    if not base_shift:
        return {
            "shift": None,
            "day": day,
            "schedule": None,
            "error": "Employee shift not configured",
        }
    if not day:
        return {
            "shift": base_shift,
            "day": None,
            "schedule": None,
            "error": "Shift day configuration error",
        }
    if not schedule:
        return {
            "shift": base_shift,
            "day": day,
            "schedule": None,
            "error": "Employee shift schedule not configured",
        }

    shift = base_shift
    if persist and not isinstance(getattr(base_shift, "pk", None), int):
        # Keep lightweight mocked/simple employee objects usable in callers
        # and tests that patch the existing shift helpers.
        persist = False
    if persist:
        portal_context = _portal_copy_shift_context(employee, base_shift)
        shift = portal_context.shift_id
        _portal_sync_shift_schedule(shift, day, schedule)

    start_time_sec = strtime_seconds(schedule["start_time"].strftime("%H:%M"))
    end_time_sec = strtime_seconds(schedule["end_time"].strftime("%H:%M"))
    return {
        "shift": shift,
        "day": day,
        "schedule": schedule,
        "minimum_hour": schedule["minimum_working_hour"],
        "start_time_sec": start_time_sec,
        "end_time_sec": end_time_sec,
        "is_night_shift": schedule["is_night_shift"],
        "error": "",
    }


def _portal_current_attendance_context(
    work_info,
    current_time,
    require_shift=False,
    employee=None,
    persist=False,
):
    current_time = _make_naive_datetime(current_time)
    date_today = current_time.date()
    now_str = current_time.strftime("%H:%M")
    context = {
        "date_today": date_today,
        "attendance_date": date_today,
        "day": None,
        "shift": None,
        "now_str": now_str,
        "minimum_hour": None,
        "start_time_sec": None,
        "end_time_sec": None,
        "error": "",
    }

    employee = employee or getattr(work_info, "employee_id", None)
    if employee:
        schedule_context = _portal_schedule_context(
            employee,
            date_today,
            persist=False,
        )
        if schedule_context["error"]:
            if require_shift:
                context["error"] = schedule_context["error"]
            return context

        now_sec = strtime_seconds(now_str)
        mid_day_sec = strtime_seconds("12:00")
        attendance_date = date_today
        if (
            schedule_context["start_time_sec"] > schedule_context["end_time_sec"]
            and mid_day_sec > now_sec
        ):
            attendance_date = date_today - timedelta(days=1)
            schedule_context = _portal_schedule_context(
                employee,
                attendance_date,
                persist=False,
            )
            if schedule_context["error"]:
                if require_shift:
                    context["error"] = schedule_context["error"]
                return context

        if persist:
            schedule_context = _portal_schedule_context(
                employee,
                attendance_date,
                persist=True,
            )

        context.update(
            {
                "attendance_date": attendance_date,
                "day": schedule_context["day"],
                "shift": schedule_context["shift"],
                "minimum_hour": schedule_context["minimum_hour"],
                "start_time_sec": schedule_context["start_time_sec"],
                "end_time_sec": schedule_context["end_time_sec"],
            }
        )
        return context

    shift = getattr(work_info, "shift_id", None) if work_info else None
    if not shift:
        if require_shift:
            context["error"] = "Employee shift not configured"
        return context

    day_name = date_today.strftime("%A").lower()
    day = EmployeeShiftDay.objects.filter(day=day_name).first()
    if not day:
        if require_shift:
            context["error"] = "Shift day configuration error"
        return context

    now_sec = strtime_seconds(now_str)
    mid_day_sec = strtime_seconds("12:00")
    minimum_hour, start_time_sec, end_time_sec = shift_schedule_today(
        day=day,
        shift=shift,
    )
    attendance_date = date_today
    if start_time_sec > end_time_sec and mid_day_sec > now_sec:
        attendance_date = date_today - timedelta(days=1)
        day_name = attendance_date.strftime("%A").lower()
        day = EmployeeShiftDay.objects.filter(day=day_name).first()
        if not day:
            if require_shift:
                context["error"] = "Shift day configuration error"
            return context
        minimum_hour, start_time_sec, end_time_sec = shift_schedule_today(
            day=day,
            shift=shift,
        )

    context.update(
        {
            "attendance_date": attendance_date,
            "day": day,
            "shift": shift,
            "minimum_hour": minimum_hour,
            "start_time_sec": start_time_sec,
            "end_time_sec": end_time_sec,
        }
    )
    return context


def _portal_shift_payload(employee, schedule_date=None):
    schedule_date = schedule_date or get_real_now().date()
    context = _portal_schedule_context(employee, schedule_date, persist=False)
    schedule = context.get("schedule")
    if context.get("error") or not schedule:
        return {
            "configured": False,
            "day": schedule_date.strftime("%a"),
            "message": context.get("error") or "Employee shift schedule not configured",
        }
    return {
        "configured": True,
        "day": schedule_date.strftime("%a"),
        "weekday": schedule_date.strftime("%A").lower(),
        "start_time": schedule["start_time"].strftime("%H:%M"),
        "end_time": schedule["end_time"].strftime("%H:%M"),
        "start_display": schedule["start_time"].strftime("%I:%M %p").lstrip("0"),
        "end_display": schedule["end_time"].strftime("%I:%M %p").lstrip("0"),
        "is_night_shift": bool(schedule["is_night_shift"]),
        "minimum_working_hour": schedule["minimum_working_hour"],
    }


def _portal_multi_punch_state_from_activities(
    activities,
    current_time=None,
    enabled=True,
):
    current_time = _make_naive_datetime(current_time or get_real_now())
    current_clock = current_time.time() if isinstance(current_time, datetime) else None
    work_activities = [
        activity
        for activity in activities
        if _portal_activity_type(activity) == PORTAL_WORK_ACTIVITY
    ]
    open_activities = [
        activity for activity in work_activities if getattr(activity, "clock_out", None) is None
    ]

    state = {
        "multi_punch_enabled": bool(enabled),
        "multi_punch_step": PORTAL_MULTI_COMPLETE,
        "multi_punch_message": _("All work punches are complete for this attendance day."),
        "multi_punch_can_morning_in": False,
        "multi_punch_can_morning_out": False,
        "multi_punch_can_afternoon_in": False,
        "multi_punch_can_afternoon_out": False,
        "multi_punch_work_segments": len(work_activities),
    }

    if not enabled:
        state["multi_punch_step"] = ""
        state["multi_punch_message"] = ""
        return state

    if not work_activities:
        state.update(
            {
                "multi_punch_step": PORTAL_MULTI_MORNING_IN,
                "multi_punch_message": _("Morning Clock In is available."),
                "multi_punch_can_morning_in": True,
            }
        )
        return state

    open_activity = open_activities[-1] if open_activities else None
    if open_activity:
        try:
            open_index = work_activities.index(open_activity)
        except ValueError:
            open_index = len(work_activities) - 1

        if open_index % 2 == 1:
            state.update(
                {
                    "multi_punch_step": PORTAL_MULTI_AFTERNOON_OUT,
                    "multi_punch_message": _("Afternoon Clock Out is available."),
                    "multi_punch_can_afternoon_out": True,
                }
            )
            return state

        # The first segment of the current cycle is open. During the overlap
        # between the two daytime windows, either the regular Morning Out or
        # the recovery Afternoon In can be selected.
        morning_out_available = _portal_multi_punch_time_in_window(
            current_clock,
            PORTAL_MULTI_MORNING_OUT_START,
            PORTAL_MULTI_MORNING_OUT_END,
        )
        afternoon_in_available = _portal_multi_punch_time_in_window(
            current_clock,
            PORTAL_MULTI_AFTERNOON_IN_START,
            PORTAL_MULTI_AFTERNOON_IN_END,
        )
        afternoon_out_fallback = bool(
            current_clock
            and current_clock >= PORTAL_MULTI_AFTERNOON_OUT_FALLBACK_START
        )
        state.update(
            {
                "multi_punch_can_morning_out": morning_out_available,
                "multi_punch_can_afternoon_in": afternoon_in_available,
                "multi_punch_can_afternoon_out": afternoon_out_fallback,
            }
        )

        if afternoon_out_fallback:
            state.update(
                {
                    "multi_punch_step": PORTAL_MULTI_AFTERNOON_OUT,
                    "multi_punch_message": _(
                        "Afternoon Clock Out is available."
                    ),
                }
            )
        elif afternoon_in_available:
            state.update(
                {
                    "multi_punch_step": PORTAL_MULTI_AFTERNOON_IN,
                    "multi_punch_message": _(
                        "Afternoon Clock In is available."
                    ),
                }
            )
        else:
            state.update(
                {
                    "multi_punch_step": PORTAL_MULTI_MORNING_OUT,
                    "multi_punch_message": (
                        _("Morning Clock Out is available.")
                        if morning_out_available
                        else _("Morning Clock Out opens at 11:00 AM.")
                    ),
                }
            )
        return state

    if len(work_activities) % 2 == 1:
        afternoon_in_available = _portal_multi_punch_time_in_window(
            current_clock,
            PORTAL_MULTI_AFTERNOON_IN_START,
            PORTAL_MULTI_AFTERNOON_IN_END,
        )
        afternoon_out_fallback = bool(
            current_clock
            and current_clock >= PORTAL_MULTI_AFTERNOON_OUT_FALLBACK_START
        )
        state.update(
            {
                "multi_punch_can_afternoon_in": afternoon_in_available,
                "multi_punch_can_afternoon_out": afternoon_out_fallback,
            }
        )
        if afternoon_out_fallback:
            state.update(
                {
                    "multi_punch_step": PORTAL_MULTI_AFTERNOON_OUT,
                    "multi_punch_message": _(
                        "Afternoon Clock Out is available."
                    ),
                }
            )
        else:
            state.update(
                {
                    "multi_punch_step": PORTAL_MULTI_AFTERNOON_IN,
                    "multi_punch_message": (
                        _("Afternoon Clock In is available.")
                        if afternoon_in_available
                        else _("Afternoon Clock In opens at 12:00 PM.")
                    ),
                }
            )
        return state

    state.update(
        {
            "multi_punch_step": PORTAL_MULTI_MORNING_IN,
            "multi_punch_message": _("Morning Clock In is available."),
            "multi_punch_can_morning_in": True,
        }
    )
    return state


def _portal_multi_punch_state(employee, attendance_date, current_time=None):
    enabled = _portal_multi_punch_enabled(employee)
    activities = (
        _portal_work_activities_for_date(employee, attendance_date) if enabled else []
    )
    return _portal_multi_punch_state_from_activities(
        activities,
        current_time=current_time,
        enabled=enabled,
    )


def _portal_multi_punch_action_error(state, requested_action, allowed_actions):
    if requested_action not in allowed_actions:
        return _("Invalid multiple clock in/out action.")

    can_key = f"multi_punch_can_{requested_action}"
    if state.get(can_key):
        return ""

    if state.get("multi_punch_step") == PORTAL_MULTI_COMPLETE:
        return _("All work punches are complete for this attendance day.")

    message = state.get("multi_punch_message")
    if message:
        return message

    next_label = PORTAL_MULTI_PUNCH_LABELS.get(state.get("multi_punch_step"))
    if next_label:
        return _("Next available action is %(action)s.") % {"action": next_label}

    return _("This attendance action is not available yet.")


def _make_naive_datetime(value):
    if isinstance(value, datetime) and timezone.is_aware(value):
        return timezone.make_naive(value, timezone.get_current_timezone())
    return value


def _activity_start_datetime(activity):
    started_at = getattr(activity, "in_datetime", None)
    if isinstance(started_at, datetime):
        return _make_naive_datetime(started_at)

    clock_in_date = getattr(activity, "clock_in_date", None)
    clock_in = getattr(activity, "clock_in", None)
    if isinstance(clock_in_date, date) and isinstance(clock_in, time):
        return datetime.combine(clock_in_date, clock_in)

    return None


def _auto_checkout_datetime(attendance_date, schedule):
    auto_time = getattr(schedule, "auto_punch_out_time", None)
    if not isinstance(attendance_date, date) or not isinstance(auto_time, time):
        return None

    checkout_date = attendance_date
    start_time = getattr(schedule, "start_time", None)
    end_time = getattr(schedule, "end_time", None)
    is_night_shift = bool(getattr(schedule, "is_night_shift", False))
    if (
        isinstance(start_time, time)
        and isinstance(end_time, time)
        and (is_night_shift or start_time > end_time)
        and auto_time < start_time
    ):
        checkout_date = attendance_date + timedelta(days=1)

    return datetime.combine(checkout_date, auto_time)


def _maybe_auto_checkout_employee(employee, current_time=None):
    """
    Close a stale open portal work attendance when its shift schedule auto
    checkout time has passed. Auto checkout intentionally skips PIN, selfie,
    and GPS because it is a server-side cleanup for forgotten checkouts.
    """
    current_time = _make_naive_datetime(current_time or get_real_now())
    open_activity = _open_portal_activity(employee)
    if not open_activity or _portal_activity_type(open_activity) != PORTAL_WORK_ACTIVITY:
        return None

    attendance_date = getattr(open_activity, "attendance_date", None)
    if not isinstance(attendance_date, date):
        return None

    attendance = Attendance.objects.filter(
        employee_id=employee,
        attendance_date=attendance_date,
    ).first()
    if not attendance:
        return None

    shift = getattr(attendance, "shift_id", None)
    if not shift:
        work_info = getattr(employee, "employee_work_info", None)
        shift = getattr(work_info, "shift_id", None) if work_info else None
    if not shift:
        return None

    shift_day = (
        getattr(attendance, "attendance_day", None)
        or getattr(open_activity, "shift_day", None)
    )
    if not shift_day:
        shift_day = EmployeeShiftDay.objects.filter(
            day=attendance_date.strftime("%A").lower()
        ).first()
        if not shift_day:
            return None

    schedule = EmployeeShiftSchedule.objects.filter(
        shift_id=shift,
        day=shift_day,
    ).first()
    if not (
        schedule
        and schedule.is_auto_punch_out_enabled
        and schedule.auto_punch_out_time
    ):
        return None

    checkout_at = _auto_checkout_datetime(attendance_date, schedule)
    if not checkout_at or current_time < checkout_at:
        return None

    started_at = _activity_start_datetime(open_activity)
    if started_at and started_at > checkout_at:
        return None

    return clock_out_attendance_and_activity(
        employee=employee,
        date_today=checkout_at.date(),
        now=checkout_at.strftime("%H:%M"),
        out_datetime=checkout_at,
        auto_validate=True,
        auto_approve_overtime=False,
    )


def _lunch_taken(employee, attendance_date):
    if not attendance_date:
        return False
    return bool(AttendanceActivity.objects.filter(
        employee_id=employee,
        attendance_date=attendance_date,
        activity_type=PORTAL_LUNCH_ACTIVITY,
    ).exists())


def _positive_int(value, default):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _portal_break_policy(employee):
    setting = None
    work_info = getattr(employee, "employee_work_info", None)
    company = getattr(work_info, "company_id", None) if work_info else None

    try:
        if company:
            setting = AttendanceGeneralSetting.objects.filter(company_id=company).first()
        if not setting:
            setting = AttendanceGeneralSetting.objects.filter(company_id=None).first()
    except Exception:
        setting = None

    return {
        "breaks_allowed": _positive_int(
            getattr(setting, "portal_break_limit", None),
            PORTAL_DEFAULT_BREAK_LIMIT,
        ),
        "break_minutes_allowed": _positive_int(
            getattr(setting, "portal_break_minutes", None),
            PORTAL_DEFAULT_BREAK_MINUTES,
        ),
        "lunch_minutes_allowed": _positive_int(
            getattr(setting, "portal_lunch_minutes", None),
            PORTAL_DEFAULT_LUNCH_MINUTES,
        ),
    }


def _portal_breaks_taken(employee, attendance_date):
    if not attendance_date:
        return 0
    try:
        count = AttendanceActivity.objects.filter(
            employee_id=employee,
            attendance_date=attendance_date,
            activity_type=PORTAL_BREAK_ACTIVITY,
        ).count()
    except Exception:
        return 0
    return count if isinstance(count, int) else 0


def _portal_break_metadata(employee, attendance_date, policy=None):
    policy = policy or _portal_break_policy(employee)
    breaks_taken = _portal_breaks_taken(employee, attendance_date)
    breaks_allowed = policy["breaks_allowed"]
    return {
        "breaks_taken": breaks_taken,
        "breaks_allowed": breaks_allowed,
        "break_minutes_allowed": policy["break_minutes_allowed"],
        "lunch_minutes_allowed": policy["lunch_minutes_allowed"],
        "break_limit_reached": breaks_taken >= breaks_allowed,
    }


def _activity_duration_seconds(activity, ended_at):
    started_at = getattr(activity, "in_datetime", None)
    if not isinstance(started_at, datetime):
        clock_in_date = getattr(activity, "clock_in_date", None)
        clock_in = getattr(activity, "clock_in", None)
        if isinstance(clock_in_date, date) and isinstance(clock_in, time):
            started_at = datetime.combine(clock_in_date, clock_in)

    if not isinstance(started_at, datetime) or not isinstance(ended_at, datetime):
        return 0

    if timezone.is_aware(started_at):
        started_at = timezone.make_naive(started_at, timezone.get_current_timezone())
    if timezone.is_aware(ended_at):
        ended_at = timezone.make_naive(ended_at, timezone.get_current_timezone())

    seconds = int((ended_at - started_at).total_seconds())
    return max(seconds, 0)


def _activity_end_datetime(activity):
    ended_at = getattr(activity, "out_datetime", None)
    if isinstance(ended_at, datetime):
        return ended_at

    clock_out_date = getattr(activity, "clock_out_date", None)
    clock_out = getattr(activity, "clock_out", None)
    if isinstance(clock_out_date, date) and isinstance(clock_out, time):
        return datetime.combine(clock_out_date, clock_out)

    return None


def _portal_activity_total_seconds(
    employee,
    attendance_date,
    activity_type,
    current_time=None,
):
    if not attendance_date:
        return 0

    total_seconds = 0
    current_time = current_time or timezone.now()
    try:
        activities = AttendanceActivity.objects.filter(
            employee_id=employee,
            attendance_date=attendance_date,
            activity_type=activity_type,
        )

        for activity in activities:
            ended_at = _activity_end_datetime(activity)
            if ended_at is None and getattr(activity, "clock_out", None) is None:
                ended_at = current_time
            total_seconds += _activity_duration_seconds(activity, ended_at)
    except Exception:
        return 0

    return total_seconds


def _portal_activity_duration_metadata(employee, attendance_date, current_time=None):
    break_total_seconds = _portal_activity_total_seconds(
        employee,
        attendance_date,
        PORTAL_BREAK_ACTIVITY,
        current_time,
    )
    lunch_total_seconds = _portal_activity_total_seconds(
        employee,
        attendance_date,
        PORTAL_LUNCH_ACTIVITY,
        current_time,
    )
    return {
        "break_total_seconds": break_total_seconds,
        "break_total_time": format_time(break_total_seconds),
        "lunch_total_seconds": lunch_total_seconds,
        "lunch_total_time": format_time(lunch_total_seconds),
    }


def _attendance_elapsed_seconds(attendance, current_time=None):
    if not attendance:
        return 0

    clock_in_date = getattr(attendance, "attendance_clock_in_date", None)
    clock_in = getattr(attendance, "attendance_clock_in", None)
    if not isinstance(clock_in_date, date) or not isinstance(clock_in, time):
        return 0

    started_at = datetime.combine(clock_in_date, clock_in)
    clock_out_date = getattr(attendance, "attendance_clock_out_date", None)
    clock_out = getattr(attendance, "attendance_clock_out", None)
    if isinstance(clock_out_date, date) and isinstance(clock_out, time):
        ended_at = datetime.combine(clock_out_date, clock_out)
    else:
        ended_at = current_time or timezone.now()

    if timezone.is_aware(started_at):
        started_at = timezone.make_naive(started_at, timezone.get_current_timezone())
    if timezone.is_aware(ended_at):
        ended_at = timezone.make_naive(ended_at, timezone.get_current_timezone())

    seconds = int((ended_at - started_at).total_seconds())
    return max(seconds, 0)


def _portal_worked_duration_metadata(employee, attendance_date, current_time=None):
    attendance = None
    if attendance_date:
        attendance = Attendance.objects.filter(
            employee_id=employee,
            attendance_date=attendance_date,
        ).first()

    worked_total_seconds = _attendance_elapsed_seconds(attendance, current_time)
    if not worked_total_seconds and attendance_date:
        worked_total_seconds = _portal_activity_total_seconds(
            employee,
            attendance_date,
            PORTAL_WORK_ACTIVITY,
            current_time,
        )
    return {
        "worked_total_seconds": worked_total_seconds,
        "worked_total_time": format_time(worked_total_seconds),
    }


def _attendance_datetime_iso(attendance, date_field, time_field):
    if not attendance:
        return None

    attendance_date = getattr(attendance, date_field, None)
    attendance_time = getattr(attendance, time_field, None)
    if isinstance(attendance_date, date) and isinstance(attendance_time, time):
        return datetime.combine(attendance_date, attendance_time).isoformat()

    return None


def _activity_label(activity_type):
    return PORTAL_NON_WORK_ACTIVITY_TYPES.get(activity_type, _("Work"))


def _save_activity_location(activity, request, latitude, longitude, clock_event):
    # The selfie is independent of GPS - attach it regardless of whether the
    # coordinates below parse successfully, so a location hiccup never
    # silently discards an already-captured photo.
    if clock_event == "in":
        if "selfie" in request.FILES:
            activity.clock_in_selfie = request.FILES["selfie"]
    else:
        if "selfie" in request.FILES:
            activity.clock_out_selfie = request.FILES["selfie"]

    try:
        lat_f = float(latitude)
        lng_f = float(longitude)
    except (TypeError, ValueError):
        activity.location_verified = False
        return

    gps_address = _reverse_geocode(lat_f, lng_f)
    if clock_event == "in":
        activity.clock_in_latitude = lat_f
        activity.clock_in_longitude = lng_f
        activity.clock_in_gps_address = gps_address
    else:
        activity.clock_out_latitude = lat_f
        activity.clock_out_longitude = lng_f
        activity.clock_out_gps_address = gps_address

    activity.latitude = lat_f
    activity.longitude = lng_f
    activity.gps_address = gps_address
    activity.location_verified = True


def _update_attendance_worked_hours(employee, attendance_date):
    attendance = Attendance.objects.filter(
        employee_id=employee,
        attendance_date=attendance_date,
    ).first()
    if attendance:
        attendance.attendance_worked_hour = calculate_worked_hours(
            employee, attendance_date
        )
        attendance.save()
    return attendance


def _portal_update_current_attendance_schedule(employee, attendance_context):
    """Apply a newly saved portal schedule to today's attendance, if present."""
    attendance = Attendance.objects.filter(
        employee_id=employee,
        attendance_date=attendance_context.get("attendance_date"),
    ).first()
    if not attendance or not attendance_context.get("shift"):
        return attendance

    # Update only the schedule fields. Calling Attendance.save() here also
    # runs overtime/accounting side effects, which can reject an otherwise
    # valid portal shift change for an attendance that is already clocked in.
    Attendance.objects.filter(pk=attendance.pk).update(
        shift_id=attendance_context["shift"].pk,
        minimum_hour=attendance_context["minimum_hour"],
    )
    attendance.shift_id = attendance_context["shift"]
    attendance.minimum_hour = attendance_context["minimum_hour"]
    attendance.late_come_early_out.all().delete()
    late_come(
        attendance=attendance,
        start_time=attendance_context["start_time_sec"],
        end_time=attendance_context["end_time_sec"],
        shift=attendance_context["shift"],
    )
    if attendance.attendance_clock_out:
        early_out(
            attendance=attendance,
            start_time=attendance_context["start_time_sec"],
            end_time=attendance_context["end_time_sec"],
            shift=attendance_context["shift"],
        )
    return attendance


def _portal_display_value(value):
    if value is None or value == "":
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%b %d, %Y")
    return str(value)


def _portal_profile_item(label, value):
    return {
        "label": str(label),
        "value": _portal_display_value(value),
    }


def _portal_choice_display(instance, field_name):
    display_method = getattr(instance, f"get_{field_name}_display", None)
    if callable(display_method):
        return display_method()
    return getattr(instance, field_name, None)


def _portal_employee_profile_payload(employee):
    work_info = getattr(employee, "employee_work_info", None)
    primary_bank = (
        EmployeeBankDetails.objects.filter(employee_id=employee, is_primary=True).first()
        or EmployeeBankDetails.objects.filter(employee_id=employee).first()
    )
    insurances = EmployeeInsurance.objects.filter(employee_id=employee).order_by(
        "start_date",
        "id",
    )

    personal_items = [
        _portal_profile_item(_("Employee ID"), employee.employee_no),
        _portal_profile_item(_("Personal Email"), employee.email),
        _portal_profile_item(_("Phone"), employee.phone),
        _portal_profile_item(_("Date of Birth"), employee.dob),
        _portal_profile_item(_("Gender"), _portal_choice_display(employee, "gender")),
        _portal_profile_item(
            _("Marital Status"),
            _portal_choice_display(employee, "marital_status"),
        ),
        _portal_profile_item(_("TIN Number"), employee.tin_number),
        _portal_profile_item(_("SSS Number"), employee.sss_number),
        _portal_profile_item(_("HDMF Number"), employee.hdmf_number),
        _portal_profile_item(_("PhilHealth Number"), employee.philhealth_number),
        _portal_profile_item(_("Address"), employee.address),
        _portal_profile_item(_("City"), employee.city),
        _portal_profile_item(_("State"), employee.state),
        _portal_profile_item(_("Country"), employee.country),
        _portal_profile_item(_("ZIP"), employee.zip),
        _portal_profile_item(_("Emergency Contact"), employee.emergency_contact_name),
        _portal_profile_item(_("Emergency Phone"), employee.emergency_contact),
        _portal_profile_item(
            _("Emergency Relation"),
            employee.emergency_contact_relation,
        ),
    ]

    work_items = [
        _portal_profile_item(_("Company"), getattr(work_info, "company_id", None)),
        _portal_profile_item(_("Branch"), getattr(work_info, "branch_id", None)),
        _portal_profile_item(_("Department"), getattr(work_info, "department_id", None)),
        _portal_profile_item(
            _("Job Position"),
            getattr(work_info, "job_position_id", None),
        ),
        _portal_profile_item(_("Job Role"), getattr(work_info, "job_role_id", None)),
        _portal_profile_item(_("Work Type"), getattr(work_info, "work_type_id", None)),
        _portal_profile_item(
            _("Employee Type"),
            getattr(work_info, "employee_type_id", None),
        ),
        _portal_profile_item(
            _("Reporting Manager"),
            getattr(work_info, "reporting_manager_id", None),
        ),
        _portal_profile_item(_("Shift"), getattr(work_info, "shift_id", None)),
        _portal_profile_item(_("Work Location"), getattr(work_info, "location", None)),
        _portal_profile_item(_("Work Email"), getattr(work_info, "email", None)),
        _portal_profile_item(_("Work Phone"), getattr(work_info, "mobile", None)),
        _portal_profile_item(_("Joining Date"), getattr(work_info, "date_joining", None)),
        _portal_profile_item(
            _("Contract End Date"),
            getattr(work_info, "contract_end_date", None),
        ),
        _portal_profile_item(
            _("Employee Status"),
            _portal_choice_display(work_info, "employee_status") if work_info else None,
        ),
    ]

    bank_items = [
        _portal_profile_item(_("Bank"), getattr(primary_bank, "bank", None)),
        _portal_profile_item(
            _("Account Number"),
            getattr(primary_bank, "account_number", None),
        ),
    ]

    insurance_rows = [
        {
            "name": _portal_display_value(insurance.name),
            "description": _portal_display_value(insurance.description),
            "start_date": _portal_display_value(insurance.start_date),
            "end_date": _portal_display_value(insurance.end_date),
        }
        for insurance in insurances
    ]

    return {
        "employee": {
            "name": employee.get_full_name(),
            "employee_no": employee.employee_no or "",
            "avatar": employee.get_avatar(),
        },
        "sections": {
            "personal": [item for item in personal_items if item["value"]],
            "work": [item for item in work_items if item["value"]],
            "bank": [item for item in bank_items if item["value"]],
            "insurance": insurance_rows,
        },
    }


def _portal_validation_messages(error):
    if hasattr(error, "message_dict"):
        messages = []
        for field_errors in error.message_dict.values():
            messages.extend(str(item) for item in field_errors)
        return messages
    if hasattr(error, "messages"):
        return [str(item) for item in error.messages]
    return [str(error)]


def _portal_parse_date(value):
    try:
        return datetime.strptime(value or "", "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _portal_leave_breakdown_options():
    from leave.models import BREAKDOWN

    return [{"value": value, "label": str(label)} for value, label in BREAKDOWN]


def _portal_leave_type_payload(available_leave):
    leave_type = available_leave.leave_type_id
    if leave_type.carryforward_type == "no carryforward":
        taken = available_leave.leave_taken()
        available_days = max(round(leave_type.total_days - taken, 3), 0)
        total_leave_days = available_days
    else:
        available_days = available_leave.available_days
        total_leave_days = round(
            available_leave.available_days + available_leave.carryforward_days, 3
        )
    return {
        "id": leave_type.id,
        "name": leave_type.name,
        "available_days": available_days,
        "carryforward_days": available_leave.carryforward_days,
        "total_leave_days": total_leave_days,
        "require_attachment": leave_type.require_attachment == "yes",
    }


def _portal_leave_request_payload(leave_request):
    return {
        "id": leave_request.id,
        "leave_type": str(leave_request.leave_type_id),
        "start_date": leave_request.start_date.strftime("%b %d, %Y"),
        "end_date": (leave_request.end_date or leave_request.start_date).strftime(
            "%b %d, %Y"
        ),
        "requested_days": leave_request.requested_days,
        "status": leave_request.status,
        "status_label": leave_request.get_status_display(),
    }


def _portal_form_error_message(form):
    messages = []
    for field_errors in form.errors.values():
        messages.extend(str(error) for error in field_errors)
    return " ".join(messages) or _("Please check the form and try again.")


def _portal_document_request_payload(document_request):
    created_at = getattr(document_request, "created_at", None)
    return {
        "id": document_request.id,
        "title": document_request.title,
        "description": document_request.description or "",
        "status": document_request.status,
        "status_label": document_request.get_status_display(),
        "issue_date": _portal_display_value(document_request.issue_date),
        "expiry_date": _portal_display_value(document_request.expiry_date),
        "created_at": _portal_display_value(created_at),
        "has_attachment": bool(document_request.attachment),
        "has_fulfilled_document": bool(document_request.fulfilled_document),
    }


def _portal_ticket_type_payload(ticket_type):
    return {
        "id": ticket_type.id,
        "title": ticket_type.title,
        "type": ticket_type.type,
        "type_label": ticket_type.get_type_display(),
        "prefix": ticket_type.prefix,
    }


def _portal_ticket_payload(ticket):
    try:
        raised_on = ticket.get_raised_on()
    except Exception:
        raised_on = ""

    return {
        "id": ticket.id,
        "title": ticket.title,
        "description": ticket.description or "",
        "ticket_type": str(ticket.ticket_type) if ticket.ticket_type_id else "",
        "priority": ticket.priority,
        "priority_label": ticket.get_priority_display(),
        "assigning_type": ticket.assigning_type,
        "assigning_type_label": ticket.get_assigning_type_display(),
        "raised_on": raised_on,
        "deadline": _portal_display_value(ticket.deadline),
        "created_date": _portal_display_value(ticket.created_date),
        "status": ticket.status,
        "status_label": ticket.get_status_display(),
        "attachment_count": ticket.ticket_attachment.count(),
    }


def _portal_faq_payload(faq):
    tags = []
    try:
        tags = [str(tag) for tag in faq.tags.all()]
    except Exception:
        tags = []

    return {
        "id": faq.id,
        "question": faq.question,
        "answer": strip_tags(faq.answer or ""),
        "tags": tags,
    }


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


@require_http_methods(["GET", "POST"])
def forgot_pin(request):
    """
    Let employees request a signed attendance portal PIN reset link by email.
    """
    form = PortalForgotPINForm(request.POST or None)
    sent = False

    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].strip()
        for employee in _get_portal_pin_reset_employees(email):
            _send_portal_pin_reset_email(request, employee)
        sent = True
        form = PortalForgotPINForm()

    return render(
        request,
        "attendance/portal/forgot_pin.html",
        {
            "form": form,
            "sent": sent,
            "generic_message": _PORTAL_PIN_RESET_GENERIC_MESSAGE,
        },
    )


@require_http_methods(["GET", "POST"])
def reset_pin(request, token):
    """
    Let employees set a new 6-digit attendance portal PIN from a signed link.
    """
    employee, token_error = _get_portal_pin_reset_employee(token)
    portal_url = reverse("public-portal")

    if not employee:
        return render(
            request,
            "attendance/portal/reset_pin.html",
            {
                "token_error": token_error,
                "portal_url": portal_url,
            },
        )

    form = PortalResetPINForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        work_info = getattr(employee, "employee_work_info", None)
        if not work_info:
            return render(
                request,
                "attendance/portal/reset_pin.html",
                {
                    "token_error": _("Employee PIN is not configured. Please contact HR."),
                    "portal_url": portal_url,
                },
            )

        work_info.pin = form.cleaned_data["new_pin"]
        work_info.save(update_fields=["pin"])
        return render(
            request,
            "attendance/portal/reset_pin.html",
            {
                "reset_success": True,
                "employee": employee,
                "portal_url": portal_url,
            },
        )

    return render(
        request,
        "attendance/portal/reset_pin.html",
        {
            "form": form,
            "employee": employee,
            "portal_url": portal_url,
        },
    )


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

        if not query or not re.fullmatch(r"\d{7}(-\d+)?", query):
            return JsonResponse({"success": True, "results": []})

        # Search by employee_no (exact match on complete query, or prefix match with suffix if search doesn't include suffix)
        if "-" in query:
            employee_filter = Q(employee_no__exact=query)
        else:
            employee_filter = Q(employee_no__exact=query) | Q(employee_no__startswith=query + "-")

        employees = Employee.objects.filter(
            employee_filter,
            is_active=True,
        )[:10]

        lookup_now = get_real_now()
        results = []
        for emp in employees:
            _maybe_auto_checkout_employee(emp, lookup_now)
            active_activity = _open_portal_activity(emp)
            work_info = getattr(emp, "employee_work_info", None)
            attendance_context = _portal_current_attendance_context(
                work_info,
                lookup_now,
                employee=emp,
            )
            current_attendance_date = attendance_context["attendance_date"]
            # When not clocked in, only use today's activities so the summary strip
            # shows --:-- instead of the previous day's times.
            latest_activity = active_activity or _latest_portal_activity_for_date(
                emp,
                current_attendance_date,
            )
            attendance_date = getattr(latest_activity, "attendance_date", None)
            attendance = None
            if attendance_date:
                attendance = Attendance.objects.filter(
                    employee_id=emp,
                    attendance_date=attendance_date,
                ).first()
            portal_attendance_date = (
                attendance_date if active_activity else current_attendance_date
            )

            is_clocked_in = active_activity is not None
            active_activity_type = (
                _portal_activity_type(active_activity) if active_activity else None
            )
            active_activity_started_at = _activity_datetime_iso(
                active_activity,
                "clock_in_date",
                "clock_in",
                "in_datetime",
            )
            lunch_taken = _lunch_taken(
                emp,
                attendance_date,
            )
            break_metadata = _portal_break_metadata(emp, attendance_date)
            multi_punch_state = _portal_multi_punch_state(
                emp,
                portal_attendance_date,
                lookup_now,
            )
            clock_in_datetime = _attendance_datetime_iso(
                attendance,
                "attendance_clock_in_date",
                "attendance_clock_in",
            ) or _activity_datetime_iso(
                latest_activity,
                "clock_in_date",
                "clock_in",
                "in_datetime",
            )
            clock_out_datetime = _attendance_datetime_iso(
                attendance,
                "attendance_clock_out_date",
                "attendance_clock_out",
            ) or _activity_datetime_iso(
                latest_activity,
                "clock_out_date",
                "clock_out",
                "out_datetime",
            )

            geo_data = []
            branch_name = ""
            try:
                branch = work_info.branch_id if work_info else None
                if branch:
                    branch_name = branch.branch or ""
                
                # Fetch geofences that actively restrict this employee.
                enforced_geos = _enforced_assigned_geofences(emp)
                for geo in enforced_geos:
                    geo_data.append({
                        "name": geo.name or "",
                        "geo_center_lat": geo.latitude,
                        "geo_center_lng": geo.longitude,
                        "geo_radius_meters": geo.radius_in_meters,
                    })
            except Exception:
                pass

            results.append({
                "id": emp.id,
                "employee_no": emp.employee_no or "",
                "name": emp.get_full_name(),
                "avatar": emp.get_avatar(),
                "is_clocked_in": is_clocked_in,
                "active_activity_type": active_activity_type,
                "active_activity_started_at": active_activity_started_at,
                "lunch_taken": lunch_taken,
                **break_metadata,
                **_portal_activity_duration_metadata(emp, attendance_date, lookup_now),
                **_portal_worked_duration_metadata(emp, attendance_date, lookup_now),
                **multi_punch_state,
                "clock_in_datetime": clock_in_datetime,
                "clock_out_datetime": clock_out_datetime,
                "geo_fence": geo_data,
                "branch": branch_name,
                "face_detection_required": _portal_face_detection_required(emp),
                "today_shift": _portal_shift_payload(emp, lookup_now.date()),
            })

        return JsonResponse({"success": True, "results": results})
    except Exception as e:
        logger.error(f"Employee lookup error: {str(e)}", exc_info=True)
        return JsonResponse(
            {"success": False, "message": f"Search error: {str(e)}"}, status=200
        )


@csrf_exempt
@require_http_methods(["GET", "POST"])
def portal_shift_schedule(request):
    """Read or save the selected employee's recurring shift for today's weekday."""
    if not _ip_is_allowed(request):
        return JsonResponse(
            {"success": False, "message": "Access denied: your network is not allowed."},
            status=403,
        )

    request_data = request.POST if request.method == "POST" else request.GET
    employee_id = request_data.get("employee_id", "").strip()
    if not employee_id:
        return JsonResponse({"success": False, "message": "Employee ID required"}, status=200)

    has_verified_pin, pin_message = _require_verified_pin(request, employee_id)
    if not has_verified_pin:
        return JsonResponse({"success": False, "message": pin_message}, status=200)

    try:
        employee = Employee.objects.get(id=employee_id, is_active=True)
    except Employee.DoesNotExist:
        return JsonResponse({"success": False, "message": "Employee not found"}, status=200)

    work_info = getattr(employee, "employee_work_info", None)
    if not work_info or not getattr(work_info, "shift_id", None):
        return JsonResponse(
            {"success": False, "message": "Employee shift not configured"},
            status=200,
        )

    datetime_now = get_real_now()
    date_today = datetime_now.date()
    if request.method == "GET":
        return JsonResponse(
            {
                "success": True,
                "today_shift": _portal_shift_payload(employee, date_today),
            },
            status=200,
        )

    start_time_value = (request.POST.get("start_time") or "").strip()
    end_time_value = (request.POST.get("end_time") or "").strip()
    try:
        start_time = time.fromisoformat(start_time_value)
        end_time = time.fromisoformat(end_time_value)
    except ValueError:
        return JsonResponse(
            {"success": False, "message": "Valid start and end times are required."},
            status=200,
        )

    if start_time == end_time:
        return JsonResponse(
            {"success": False, "message": "Start and end times must be different."},
            status=200,
        )

    day_name = date_today.strftime("%A").lower()
    day = EmployeeShiftDay.objects.filter(day=day_name).first()
    if not day:
        return JsonResponse(
            {"success": False, "message": "Shift day configuration error"},
            status=200,
        )

    minimum_working_hour = format_time(
        _portal_shift_duration_seconds(start_time, end_time)
    )
    is_night_shift = start_time > end_time

    try:
        with transaction.atomic():
            AttendancePortalShiftOverride._base_manager.update_or_create(
                employee_id=employee,
                day=day,
                defaults={
                    "start_time": start_time,
                    "end_time": end_time,
                    "minimum_working_hour": minimum_working_hour,
                    "is_night_shift": is_night_shift,
                    "is_active": True,
                },
            )
            today_schedule_context = _portal_schedule_context(
                employee,
                date_today,
                persist=True,
            )
            if today_schedule_context["error"]:
                raise PortalShiftScheduleError(today_schedule_context["error"])

            attendance_context = _portal_current_attendance_context(
                work_info,
                datetime_now,
                require_shift=False,
                employee=employee,
                persist=True,
            )
            _portal_update_current_attendance_schedule(employee, attendance_context)
    except PortalShiftScheduleError as error:
        logger.warning(
            "Portal shift schedule rejected for employee %s: %s",
            employee_id,
            error,
        )
        return JsonResponse(
            {"success": False, "message": str(error)},
            status=200,
        )
    except DatabaseError:
        logger.exception(
            "Portal shift schedule database error for employee %s",
            employee_id,
        )
        return JsonResponse(
            {
                "success": False,
                "message": (
                    "Shift schedule storage is unavailable. "
                    "Please ask an administrator to apply the latest attendance migrations."
                ),
            },
            status=503,
        )
    except Exception:
        logger.exception(
            "Unexpected portal shift schedule failure for employee %s",
            employee_id,
        )
        return JsonResponse(
            {
                "success": False,
                "message": "Unable to save the shift schedule. Please try again or contact an administrator.",
            },
            status=200,
        )

    return JsonResponse(
        {
            "success": True,
            "message": "Today’s shift was saved successfully.",
            "today_shift": _portal_shift_payload(employee, date_today),
        },
        status=200,
    )


@csrf_exempt
@require_http_methods(["POST"])
def attendance_history(request):
    """
    Return the selected employee's recent attendance rows for the public portal.
    """
    if not _ip_is_allowed(request):
        return JsonResponse(
            {"success": False, "message": "Access denied: your network is not allowed."},
            status=403,
        )

    employee_id = request.POST.get("employee_id", "").strip()
    if not employee_id:
        return JsonResponse(
            {"success": False, "message": "Employee ID required"},
            status=200,
        )

    has_verified_pin, pin_message = _require_verified_pin(request, employee_id)
    if not has_verified_pin:
        return JsonResponse({"success": False, "message": pin_message}, status=200)

    try:
        employee = Employee.objects.get(id=employee_id, is_active=True)
    except Employee.DoesNotExist:
        return JsonResponse(
            {"success": False, "message": "Employee not found"},
            status=200,
        )

    default_end_date = timezone.localdate()
    default_start_date = default_end_date - timedelta(days=14)

    def parse_history_date(value, fallback):
        try:
            return datetime.strptime(value or "", "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return fallback

    start_date = parse_history_date(
        request.POST.get("start_date"),
        default_start_date,
    )
    end_date = parse_history_date(
        request.POST.get("end_date"),
        default_end_date,
    )
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    def format_time_value(value):
        if not value:
            return "--:--"
        if hasattr(value, "strftime"):
            return value.strftime("%I:%M %p")
        return str(value)

    rows = []
    attendance_rows = list(
        Attendance.objects.filter(
            employee_id=employee,
            attendance_date__gte=start_date,
            attendance_date__lte=end_date,
        )
        .order_by("-attendance_date", "-id")
    )

    current_time = timezone.now()
    activity_totals_by_date = {}
    activities = AttendanceActivity.objects.filter(
        employee_id=employee,
        attendance_date__gte=start_date,
        attendance_date__lte=end_date,
        activity_type__in=[PORTAL_BREAK_ACTIVITY, PORTAL_LUNCH_ACTIVITY],
    ).only(
        "attendance_date",
        "activity_type",
        "clock_in_date",
        "clock_in",
        "clock_out_date",
        "clock_out",
        "in_datetime",
        "out_datetime",
    )
    for activity in activities:
        attendance_date = activity.attendance_date
        if attendance_date not in activity_totals_by_date:
            activity_totals_by_date[attendance_date] = {
                PORTAL_BREAK_ACTIVITY: 0,
                PORTAL_LUNCH_ACTIVITY: 0,
            }
        ended_at = _activity_end_datetime(activity)
        if ended_at is None and getattr(activity, "clock_out", None) is None:
            ended_at = current_time
        activity_totals_by_date[attendance_date][activity.activity_type] += (
            _activity_duration_seconds(activity, ended_at)
        )

    for attendance in attendance_rows:
        activity_totals = activity_totals_by_date.get(
            attendance.attendance_date,
            {
                PORTAL_BREAK_ACTIVITY: 0,
                PORTAL_LUNCH_ACTIVITY: 0,
            },
        )
        rows.append(
            {
                "attendance_date": attendance.attendance_date.strftime("%b %d, %Y"),
                "clock_in_time": format_time_value(attendance.attendance_clock_in),
                "clock_out_time": format_time_value(attendance.attendance_clock_out),
                "worked_hours": attendance.attendance_worked_hour or "00:00",
                "break_time": format_time(activity_totals[PORTAL_BREAK_ACTIVITY]),
                "lunch_time": format_time(activity_totals[PORTAL_LUNCH_ACTIVITY]),
            }
        )

    return JsonResponse(
        {
            "success": True,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "rows": rows,
        },
        status=200,
    )


@csrf_exempt
@require_http_methods(["POST"])
def employee_profile(request):
    """
    Return read-only profile information for the selected public portal employee.
    """
    if not _ip_is_allowed(request):
        return JsonResponse(
            {"success": False, "message": "Access denied: your network is not allowed."},
            status=403,
        )

    employee_id = request.POST.get("employee_id", "").strip()
    if not employee_id:
        return JsonResponse(
            {"success": False, "message": "Employee ID required"},
            status=200,
        )

    has_verified_pin, pin_message = _require_verified_pin(request, employee_id)
    if not has_verified_pin:
        return JsonResponse({"success": False, "message": pin_message}, status=200)

    try:
        employee = (
            Employee.objects.select_related(
                "employee_work_info__company_id",
                "employee_work_info__branch_id",
                "employee_work_info__department_id",
                "employee_work_info__job_position_id",
                "employee_work_info__job_role_id",
                "employee_work_info__reporting_manager_id",
                "employee_work_info__shift_id",
                "employee_work_info__work_type_id",
                "employee_work_info__employee_type_id",
            )
            .prefetch_related("employee_bank_details__bank", "employee_insurance")
            .get(id=employee_id, is_active=True)
        )
    except Employee.DoesNotExist:
        return JsonResponse(
            {"success": False, "message": "Employee not found"},
            status=200,
        )

    return JsonResponse(
        {
            "success": True,
            **_portal_employee_profile_payload(employee),
        },
        status=200,
    )


@csrf_exempt
@require_http_methods(["POST"])
def faq_data(request):
    """
    Return Helpdesk FAQ categories and questions for the selected portal employee.
    """
    if not _ip_is_allowed(request):
        return JsonResponse(
            {"success": False, "message": "Access denied: your network is not allowed."},
            status=403,
        )

    if not apps.is_installed("helpdesk"):
        return JsonResponse(
            {"success": False, "message": "FAQ is not available."},
            status=200,
        )

    employee_id = request.POST.get("employee_id", "").strip()
    if not employee_id:
        return JsonResponse(
            {"success": False, "message": "Employee ID required"},
            status=200,
        )

    has_verified_pin, pin_message = _require_verified_pin(request, employee_id)
    if not has_verified_pin:
        return JsonResponse({"success": False, "message": pin_message}, status=200)

    try:
        employee = Employee.objects.get(id=employee_id, is_active=True)
    except Employee.DoesNotExist:
        return JsonResponse(
            {"success": False, "message": "Employee not found"},
            status=200,
        )

    from helpdesk.models import FAQ, FAQCategory

    faqs = (
        FAQ.objects.select_related("category")
        .prefetch_related("tags")
        .filter(is_active=True, category__is_active=True)
        .order_by("category__title", "question", "id")
    )
    category_map = {}
    for faq in faqs:
        category = faq.category
        key = category.id
        if key not in category_map:
            category_map[key] = {
                "id": category.id,
                "title": category.title,
                "description": category.description or "",
                "faqs": [],
            }
        category_map[key]["faqs"].append(_portal_faq_payload(faq))

    uncategorized_categories = FAQCategory.objects.filter(
        is_active=True,
        faq__isnull=True,
    ).order_by("title")
    for category in uncategorized_categories:
        category_map.setdefault(
            category.id,
            {
                "id": category.id,
                "title": category.title,
                "description": category.description or "",
                "faqs": [],
            },
        )

    return JsonResponse(
        {
            "success": True,
            "employee": {
                "name": employee.get_full_name(),
                "employee_no": employee.employee_no or "",
                "avatar": employee.get_avatar(),
            },
            "categories": list(category_map.values()),
        },
        status=200,
    )


@csrf_exempt
@require_http_methods(["POST"])
def leave_data(request):
    """
    Return leave balances and recent leave requests for the selected portal employee.
    """
    if not _ip_is_allowed(request):
        return JsonResponse(
            {"success": False, "message": "Access denied: your network is not allowed."},
            status=403,
        )

    if not apps.is_installed("leave"):
        return JsonResponse(
            {"success": False, "message": "Leave is not available."},
            status=200,
        )

    employee_id = request.POST.get("employee_id", "").strip()
    if not employee_id:
        return JsonResponse(
            {"success": False, "message": "Employee ID required"},
            status=200,
        )

    has_verified_pin, pin_message = _require_verified_pin(request, employee_id)
    if not has_verified_pin:
        return JsonResponse({"success": False, "message": pin_message}, status=200)

    try:
        employee = Employee.objects.get(id=employee_id, is_active=True)
    except Employee.DoesNotExist:
        return JsonResponse(
            {"success": False, "message": "Employee not found"},
            status=200,
        )

    from leave.models import AvailableLeave, LeaveRequest

    available_leaves = (
        AvailableLeave.objects.select_related("leave_type_id")
        .filter(employee_id=employee, leave_type_id__is_compensatory_leave=False)
        .order_by("leave_type_id__name")
    )
    leave_requests_qs = (
        LeaveRequest.objects.select_related("leave_type_id")
        .filter(employee_id=employee)
        .order_by("-start_date", "-id")
    )
    filter_start = _portal_parse_date(request.POST.get("start_date"))
    filter_end = _portal_parse_date(request.POST.get("end_date"))
    if filter_start or filter_end:
        if filter_start:
            leave_requests_qs = leave_requests_qs.filter(start_date__gte=filter_start)
        if filter_end:
            leave_requests_qs = leave_requests_qs.filter(start_date__lte=filter_end)
        leave_requests = leave_requests_qs
    else:
        leave_requests = leave_requests_qs[:8]

    return JsonResponse(
        {
            "success": True,
            "employee": {
                "name": employee.get_full_name(),
                "employee_no": employee.employee_no or "",
                "avatar": employee.get_avatar(),
            },
            "leave_types": [
                _portal_leave_type_payload(available_leave)
                for available_leave in available_leaves
                if available_leave.leave_type_id
            ],
            "breakdown_options": _portal_leave_breakdown_options(),
            "recent_requests": [
                _portal_leave_request_payload(leave_request)
                for leave_request in leave_requests
            ],
        },
        status=200,
    )


@csrf_exempt
@require_http_methods(["POST"])
def leave_request_create(request):
    """
    Create a leave request from the public attendance portal.
    """
    if not _ip_is_allowed(request):
        return JsonResponse(
            {"success": False, "message": "Access denied: your network is not allowed."},
            status=403,
        )

    if not apps.is_installed("leave"):
        return JsonResponse(
            {"success": False, "message": "Leave is not available."},
            status=200,
        )

    employee_id = request.POST.get("employee_id", "").strip()
    if not employee_id:
        return JsonResponse(
            {"success": False, "message": "Employee ID required"},
            status=200,
        )

    has_verified_pin, pin_message = _require_verified_pin(request, employee_id)
    if not has_verified_pin:
        return JsonResponse({"success": False, "message": pin_message}, status=200)

    try:
        employee = Employee.objects.get(id=employee_id, is_active=True)
    except Employee.DoesNotExist:
        return JsonResponse(
            {"success": False, "message": "Employee not found"},
            status=200,
        )

    from leave.models import (
        AvailableLeave,
        LeaveRequest,
        LeaveRequestConditionApproval,
        LeaveType,
    )

    leave_type_id = request.POST.get("leave_type_id", "").strip()
    start_date = _portal_parse_date(request.POST.get("start_date"))
    end_date = _portal_parse_date(request.POST.get("end_date")) or start_date
    description = (request.POST.get("description") or "").strip()
    start_breakdown = request.POST.get("start_date_breakdown") or "full_day"
    end_breakdown = request.POST.get("end_date_breakdown") or "full_day"

    if not leave_type_id or not start_date or not end_date:
        return JsonResponse(
            {
                "success": False,
                "message": "Leave type, start date, and end date are required.",
            },
            status=200,
        )

    try:
        leave_type = LeaveType.objects.get(id=leave_type_id)
    except (LeaveType.DoesNotExist, ValueError):
        return JsonResponse(
            {"success": False, "message": "Selected leave type was not found."},
            status=200,
        )

    if not AvailableLeave.objects.filter(
        employee_id=employee,
        leave_type_id=leave_type,
    ).exists():
        return JsonResponse(
            {
                "success": False,
                "message": "The selected leave type is not assigned to this employee.",
            },
            status=200,
        )

    leave_request = LeaveRequest(
        employee_id=employee,
        leave_type_id=leave_type,
        start_date=start_date,
        end_date=end_date,
        start_date_breakdown=start_breakdown,
        end_date_breakdown=end_breakdown,
        description=description,
        attachment=request.FILES.get("attachment"),
        created_by=employee,
    )

    try:
        leave_request.full_clean()
        leave_request.save()
        if leave_type.require_approval == "no":
            leave_request.no_approval()
            leave_request.save()
            LeaveRequestConditionApproval.objects.filter(
                leave_request_id=leave_request
            ).delete()
    except ValidationError as error:
        return JsonResponse(
            {
                "success": False,
                "message": " ".join(_portal_validation_messages(error)),
            },
            status=200,
        )
    except Exception as error:
        logger.error("Portal leave request creation failed: %s", error, exc_info=True)
        return JsonResponse(
            {"success": False, "message": f"Leave request failed: {error}"},
            status=200,
        )

    return JsonResponse(
        {
            "success": True,
            "message": "Leave request created successfully.",
            "request": _portal_leave_request_payload(leave_request),
        },
        status=200,
    )


@csrf_exempt
@require_http_methods(["POST"])
def document_request_data(request):
    """
    Return recent document requests for the selected public portal employee.
    """
    if not _ip_is_allowed(request):
        return JsonResponse(
            {"success": False, "message": "Access denied: your network is not allowed."},
            status=403,
        )

    if not apps.is_installed("horilla_documents"):
        return JsonResponse(
            {"success": False, "message": "Document requests are not available."},
            status=200,
        )

    employee_id = request.POST.get("employee_id", "").strip()
    if not employee_id:
        return JsonResponse(
            {"success": False, "message": "Employee ID required"},
            status=200,
        )

    has_verified_pin, pin_message = _require_verified_pin(request, employee_id)
    if not has_verified_pin:
        return JsonResponse({"success": False, "message": pin_message}, status=200)

    try:
        employee = Employee.objects.get(id=employee_id, is_active=True)
    except Employee.DoesNotExist:
        return JsonResponse(
            {"success": False, "message": "Employee not found"},
            status=200,
        )

    from horilla_documents.models import EmployeeDocumentRequest

    document_requests_qs = EmployeeDocumentRequest.objects.filter(
        employee_id=employee
    ).order_by("-created_at", "-id")

    filter_start = _portal_parse_date(request.POST.get("start_date"))
    filter_end = _portal_parse_date(request.POST.get("end_date"))
    if filter_start:
        document_requests_qs = document_requests_qs.filter(
            created_at__date__gte=filter_start
        )
    if filter_end:
        document_requests_qs = document_requests_qs.filter(
            created_at__date__lte=filter_end
        )

    document_requests = (
        document_requests_qs if (filter_start or filter_end) else document_requests_qs[:8]
    )

    return JsonResponse(
        {
            "success": True,
            "employee": {
                "name": employee.get_full_name(),
                "employee_no": employee.employee_no or "",
                "avatar": employee.get_avatar(),
            },
            "recent_requests": [
                _portal_document_request_payload(document_request)
                for document_request in document_requests
            ],
        },
        status=200,
    )


@csrf_exempt
@require_http_methods(["POST"])
def document_request_create(request):
    """
    Create an employee document request from the public attendance portal.
    """
    if not _ip_is_allowed(request):
        return JsonResponse(
            {"success": False, "message": "Access denied: your network is not allowed."},
            status=403,
        )

    if not apps.is_installed("horilla_documents"):
        return JsonResponse(
            {"success": False, "message": "Document requests are not available."},
            status=200,
        )

    employee_id = request.POST.get("employee_id", "").strip()
    if not employee_id:
        return JsonResponse(
            {"success": False, "message": "Employee ID required"},
            status=200,
        )

    has_verified_pin, pin_message = _require_verified_pin(request, employee_id)
    if not has_verified_pin:
        return JsonResponse({"success": False, "message": pin_message}, status=200)

    try:
        employee = Employee.objects.get(id=employee_id, is_active=True)
    except Employee.DoesNotExist:
        return JsonResponse(
            {"success": False, "message": "Employee not found"},
            status=200,
        )

    from horilla_documents.forms import EmployeeDocumentRequestForm

    form = EmployeeDocumentRequestForm(request.POST, request.FILES)
    if not form.is_valid():
        return JsonResponse(
            {
                "success": False,
                "message": _portal_form_error_message(form),
            },
            status=200,
        )

    try:
        document_request = form.save(commit=False)
        document_request.employee_id = employee
        document_request.save()
    except ValidationError as error:
        return JsonResponse(
            {
                "success": False,
                "message": " ".join(_portal_validation_messages(error)),
            },
            status=200,
        )
    except Exception as error:
        logger.error(
            "Portal document request creation failed: %s",
            error,
            exc_info=True,
        )
        return JsonResponse(
            {"success": False, "message": f"Document request failed: {error}"},
            status=200,
        )

    try:
        from notifications.signals import notify

        admins = User.objects.filter(is_superuser=True)
        if admins.exists():
            notify.send(
                employee,
                recipient=list(admins),
                verb=f"{employee} submitted a document request: {document_request.title}",
                redirect=reverse("employee-document-request-view"),
                icon="chatbox-ellipses",
            )
    except Exception:
        logger.debug("Portal document request notification failed.", exc_info=True)

    return JsonResponse(
        {
            "success": True,
            "message": "Document request submitted successfully.",
            "request": _portal_document_request_payload(document_request),
        },
        status=200,
    )


def attendance_request_create(request):
    """
    Create a manual attendance correction request from the public attendance portal.
    """
    if not _ip_is_allowed(request):
        return JsonResponse(
            {"success": False, "message": "Access denied: your network is not allowed."},
            status=403,
        )

    employee_id = request.POST.get("employee_id", "").strip()
    if not employee_id:
        return JsonResponse(
            {"success": False, "message": "Employee ID required"},
            status=200,
        )

    has_verified_pin, pin_message = _require_verified_pin(request, employee_id)
    if not has_verified_pin:
        return JsonResponse({"success": False, "message": pin_message}, status=200)

    try:
        employee = Employee.objects.get(id=employee_id, is_active=True)
    except Employee.DoesNotExist:
        return JsonResponse(
            {"success": False, "message": "Employee not found"},
            status=200,
        )

    attendance_date = _portal_parse_date(request.POST.get("attendance_date"))
    clock_in_date = _portal_parse_date(request.POST.get("attendance_clock_in_date"))
    clock_in_time_str = (request.POST.get("attendance_clock_in") or "").strip()
    clock_out_date = _portal_parse_date(request.POST.get("attendance_clock_out_date"))
    clock_out_time_str = (request.POST.get("attendance_clock_out") or "").strip()
    reason = (request.POST.get("request_description") or "").strip()

    if not attendance_date or not clock_in_date or not clock_in_time_str:
        return JsonResponse(
            {
                "success": False,
                "message": "Attendance date, clock-in date, and clock-in time are required.",
            },
            status=200,
        )

    if not reason:
        return JsonResponse(
            {"success": False, "message": "A reason for the attendance request is required."},
            status=200,
        )

    try:
        clock_in_time = time.fromisoformat(clock_in_time_str)
    except ValueError:
        return JsonResponse(
            {"success": False, "message": "Invalid clock-in time format."},
            status=200,
        )

    clock_out_time = None
    if clock_out_time_str:
        try:
            clock_out_time = time.fromisoformat(clock_out_time_str)
        except ValueError:
            return JsonResponse(
                {"success": False, "message": "Invalid clock-out time format."},
                status=200,
            )

    work_info = getattr(employee, "employee_work_info", None)
    shift = getattr(work_info, "shift_id", None)
    work_type = getattr(work_info, "work_type_id", None)

    clock_in_date = clock_in_date or attendance_date
    clock_out_date_final = clock_out_date if clock_out_time else None

    try:
        attendance = Attendance(
            employee_id=employee,
            attendance_date=attendance_date,
            attendance_clock_in_date=clock_in_date,
            attendance_clock_in=clock_in_time,
            attendance_clock_out_date=clock_out_date_final,
            attendance_clock_out=clock_out_time,
            request_description=reason,
            is_validate_request=True,
            request_type="create_request",
            shift_id=shift,
            work_type_id=work_type,
        )
        attendance.save()
    except ValidationError as error:
        return JsonResponse(
            {
                "success": False,
                "message": " ".join(_portal_validation_messages(error)),
            },
            status=200,
        )
    except Exception as error:
        logger.error(
            "Portal attendance request creation failed: %s", error, exc_info=True
        )
        return JsonResponse(
            {"success": False, "message": f"Attendance request failed: {error}"},
            status=200,
        )

    proof_image = request.FILES.get("proof_image")
    if proof_image:
        try:
            proof_file = AttendanceRequestFile(file=proof_image)
            proof_file.save()
            comment = AttendanceRequestComment(
                request_id=attendance,
                employee_id=employee,
                comment=reason,
            )
            comment.save()
            comment.files.add(proof_file)
        except Exception:
            logger.debug("Portal attendance request proof save failed.", exc_info=True)

    try:
        from notifications.signals import notify

        admins = User.objects.filter(is_superuser=True)
        if admins.exists():
            notify.send(
                employee,
                recipient=list(admins),
                verb=f"{employee} submitted an attendance request for {attendance_date}",
                redirect=reverse("request-attendance-view"),
                icon="time",
            )
    except Exception:
        logger.debug("Portal attendance request notification failed.", exc_info=True)

    return JsonResponse(
        {
            "success": True,
            "message": "Attendance request submitted successfully.",
        },
        status=200,
    )


def _portal_helpdesk_raised_on_options():
    from base.models import Department, JobPosition

    employees = Employee.objects.filter(is_active=True).order_by(
        "employee_first_name",
        "employee_last_name",
        "employee_no",
    )
    return {
        "department": [
            {"id": department.id, "name": department.department}
            for department in Department.objects.all().order_by("department")
        ],
        "job_position": [
            {"id": job_position.id, "name": job_position.job_position}
            for job_position in JobPosition.objects.all().order_by("job_position")
        ],
        "individual": [
            {
                "id": employee.id,
                "name": employee.get_full_name() or employee.employee_no or str(employee),
            }
            for employee in employees
        ],
    }


def _portal_helpdesk_choice_payload(choices):
    return [{"value": value, "label": str(label)} for value, label in choices]


def _portal_helpdesk_employee_response(employee, tickets, ticket_types):
    from helpdesk.models import MANAGER_TYPES, PRIORITY

    return {
        "success": True,
        "employee": {
            "name": employee.get_full_name(),
            "employee_no": employee.employee_no or "",
            "avatar": employee.get_avatar(),
        },
        "ticket_types": [
            _portal_ticket_type_payload(ticket_type) for ticket_type in ticket_types
        ],
        "priority_options": _portal_helpdesk_choice_payload(PRIORITY),
        "assigning_type_options": _portal_helpdesk_choice_payload(MANAGER_TYPES),
        "raised_on_options": _portal_helpdesk_raised_on_options(),
        "recent_requests": [_portal_ticket_payload(ticket) for ticket in tickets],
    }


@csrf_exempt
@require_http_methods(["POST"])
def helpdesk_data(request):
    """
    Return helpdesk ticket choices and recent tickets for the selected portal employee.
    """
    if not _ip_is_allowed(request):
        return JsonResponse(
            {"success": False, "message": "Access denied: your network is not allowed."},
            status=403,
        )

    if not apps.is_installed("helpdesk"):
        return JsonResponse(
            {"success": False, "message": "Helpdesk is not available."},
            status=200,
        )

    employee_id = request.POST.get("employee_id", "").strip()
    if not employee_id:
        return JsonResponse(
            {"success": False, "message": "Employee ID required"},
            status=200,
        )

    has_verified_pin, pin_message = _require_verified_pin(request, employee_id)
    if not has_verified_pin:
        return JsonResponse({"success": False, "message": pin_message}, status=200)

    try:
        employee = Employee.objects.get(id=employee_id, is_active=True)
    except Employee.DoesNotExist:
        return JsonResponse(
            {"success": False, "message": "Employee not found"},
            status=200,
        )

    from helpdesk.models import Ticket, TicketType

    tickets_qs = (
        Ticket.objects.entire()
        .select_related("ticket_type")
        .prefetch_related("ticket_attachment")
        .filter(employee_id=employee)
        .order_by("-created_at", "-created_date", "-id")
    )
    filter_start = _portal_parse_date(request.POST.get("start_date"))
    filter_end = _portal_parse_date(request.POST.get("end_date"))
    if filter_start:
        tickets_qs = tickets_qs.filter(created_date__gte=filter_start)
    if filter_end:
        tickets_qs = tickets_qs.filter(created_date__lte=filter_end)

    tickets = tickets_qs if (filter_start or filter_end) else tickets_qs[:8]
    ticket_types = TicketType.objects.entire().order_by("title")

    return JsonResponse(
        _portal_helpdesk_employee_response(employee, tickets, ticket_types),
        status=200,
    )


def _portal_helpdesk_raised_on_exists(assigning_type, raised_on):
    from base.models import Department, JobPosition

    if assigning_type == "department":
        return Department.objects.filter(id=raised_on).exists()
    if assigning_type == "job_position":
        return JobPosition.objects.filter(id=raised_on).exists()
    if assigning_type == "individual":
        return Employee.objects.filter(id=raised_on, is_active=True).exists()
    return False


@csrf_exempt
@require_http_methods(["POST"])
def helpdesk_ticket_create(request):
    """
    Create a helpdesk ticket from the public attendance portal.
    """
    if not _ip_is_allowed(request):
        return JsonResponse(
            {"success": False, "message": "Access denied: your network is not allowed."},
            status=403,
        )

    if not apps.is_installed("helpdesk"):
        return JsonResponse(
            {"success": False, "message": "Helpdesk is not available."},
            status=200,
        )

    employee_id = request.POST.get("employee_id", "").strip()
    if not employee_id:
        return JsonResponse(
            {"success": False, "message": "Employee ID required"},
            status=200,
        )

    has_verified_pin, pin_message = _require_verified_pin(request, employee_id)
    if not has_verified_pin:
        return JsonResponse({"success": False, "message": pin_message}, status=200)

    try:
        employee = Employee.objects.get(id=employee_id, is_active=True)
    except Employee.DoesNotExist:
        return JsonResponse(
            {"success": False, "message": "Employee not found"},
            status=200,
        )

    from helpdesk.models import Attachment, MANAGER_TYPES, PRIORITY, Ticket, TicketType

    title = (request.POST.get("title") or "").strip()
    description = (request.POST.get("description") or "").strip()
    ticket_type_id = (request.POST.get("ticket_type") or "").strip()
    priority = (request.POST.get("priority") or "low").strip()
    assigning_type = (request.POST.get("assigning_type") or "").strip()
    raised_on = (request.POST.get("raised_on") or "").strip()
    deadline = _portal_parse_date(request.POST.get("deadline"))

    manager_type_values = {value for value, _label in MANAGER_TYPES}
    priority_values = {value for value, _label in PRIORITY}

    if not title or not description or not ticket_type_id or not assigning_type or not raised_on:
        return JsonResponse(
            {
                "success": False,
                "message": "Title, description, ticket type, assigning type, and forward to are required.",
            },
            status=200,
        )

    if priority not in priority_values:
        return JsonResponse(
            {"success": False, "message": "Selected priority is invalid."},
            status=200,
        )

    if assigning_type not in manager_type_values:
        return JsonResponse(
            {"success": False, "message": "Selected assigning type is invalid."},
            status=200,
        )

    if not _portal_helpdesk_raised_on_exists(assigning_type, raised_on):
        return JsonResponse(
            {"success": False, "message": "Selected forward-to option was not found."},
            status=200,
        )

    if deadline and deadline < timezone.localdate():
        return JsonResponse(
            {"success": False, "message": "Deadline should be greater than today."},
            status=200,
        )

    try:
        ticket_type = TicketType.objects.entire().get(id=ticket_type_id)
    except (TicketType.DoesNotExist, ValueError):
        return JsonResponse(
            {"success": False, "message": "Selected ticket type was not found."},
            status=200,
        )

    files = request.FILES.getlist("attachment")
    blocked_exts = sorted(
        {
            os.path.splitext(file.name)[1].lower()
            for file in files
            if os.path.splitext(file.name)[1].lower()
            in PORTAL_HELPDESK_BLOCKED_EXTENSIONS
        }
    )
    if blocked_exts:
        return JsonResponse(
            {
                "success": False,
                "message": _("File type(s) %(ext)s are not allowed.")
                % {"ext": ", ".join(blocked_exts)},
            },
            status=200,
        )

    ticket = Ticket(
        title=title,
        employee_id=employee,
        description=description,
        ticket_type=ticket_type,
        priority=priority,
        assigning_type=assigning_type,
        raised_on=raised_on,
        deadline=deadline,
        status="new",
    )

    try:
        ticket.full_clean()
        ticket.save()
        for file in files:
            Attachment(file=file, ticket=ticket).save()
    except ValidationError as error:
        return JsonResponse(
            {
                "success": False,
                "message": " ".join(_portal_validation_messages(error)),
            },
            status=200,
        )
    except Exception as error:
        logger.error("Portal helpdesk ticket creation failed: %s", error, exc_info=True)
        return JsonResponse(
            {"success": False, "message": f"Helpdesk request failed: {error}"},
            status=200,
        )

    try:
        from notifications.signals import notify

        recipients = list(User.objects.filter(is_superuser=True))
        if assigning_type == "individual":
            raised_employee = Employee.objects.filter(id=raised_on).first()
            if raised_employee and raised_employee.employee_user_id:
                recipients.append(raised_employee.employee_user_id)
        if employee.employee_user_id:
            recipients.append(employee.employee_user_id)
        if recipients:
            notify.send(
                employee,
                recipient=recipients,
                verb=f"{employee} submitted a helpdesk ticket: {ticket.title}",
                redirect=reverse("ticket-detail", kwargs={"ticket_id": ticket.id}),
                icon="infinite",
            )
    except Exception:
        logger.debug("Portal helpdesk notification failed.", exc_info=True)

    return JsonResponse(
        {
            "success": True,
            "message": "Helpdesk ticket submitted successfully.",
            "request": _portal_ticket_payload(ticket),
        },
        status=200,
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
        face_detected (str): "true" when the client detected one visible face

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

        latitude = request.POST.get("latitude")
        longitude = request.POST.get("longitude")

        # Find employee
        try:
            employee = Employee.objects.get(id=employee_id, is_active=True)
        except Employee.DoesNotExist:
            logger.warning(f"Employee not found: {employee_id}")
            return JsonResponse(
                {"success": False, "message": "Employee not found"}, status=200
            )

        _maybe_auto_checkout_employee(employee)

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
            return _portal_geo_error_response(geo_error)

        # The portal requires a normal employee shift as its base schedule,
        # then resolves the employee-specific portal schedule below.
        if not work_info.shift_id:
            logger.warning(f"No shift configured for employee: {employee_id}")
            return JsonResponse(
                {"success": False, "message": "Employee shift not configured"}, status=200
            )

        # Get current date and time from NTP (tamper-proof)
        datetime_now = get_real_now()
        attendance_context = _portal_current_attendance_context(
            work_info,
            datetime_now,
            require_shift=True,
            employee=employee,
            persist=True,
        )
        if attendance_context["error"]:
            logger.error(
                "Clock in attendance date resolution failed for employee %s: %s",
                employee_id,
                attendance_context["error"],
            )
            return JsonResponse(
                {"success": False, "message": attendance_context["error"]},
                status=200,
            )
        date_today = attendance_context["date_today"]
        day = attendance_context["day"]
        now_str = attendance_context["now_str"]
        minimum_hour = attendance_context["minimum_hour"]
        start_time_sec = attendance_context["start_time_sec"]
        end_time_sec = attendance_context["end_time_sec"]
        attendance_date = attendance_context["attendance_date"]
        shift = attendance_context["shift"]

        multi_punch_enabled = _portal_multi_punch_enabled(employee)
        if multi_punch_enabled:
            punch_type = request.POST.get("punch_type", "").strip()
            multi_punch_state = _portal_multi_punch_state(
                employee,
                attendance_date,
                datetime_now,
            )
            action_error = _portal_multi_punch_action_error(
                multi_punch_state,
                punch_type,
                {PORTAL_MULTI_MORNING_IN, PORTAL_MULTI_AFTERNOON_IN},
            )
            if action_error:
                return JsonResponse(
                    {"success": False, "message": action_error, **multi_punch_state},
                    status=200,
                )

        open_activity = AttendanceActivity.objects.filter(
            employee_id=employee, clock_out__isnull=True
        ).order_by("attendance_date", "id").last()
        if open_activity:
            active_activity_type = _portal_activity_type(open_activity)
            if active_activity_type != PORTAL_WORK_ACTIVITY:
                label = _activity_label(active_activity_type).lower()
                return JsonResponse(
                    {
                        "success": False,
                        "message": f"Please end your {label} before clocking in.",
                    },
                    status=200,
                )
            open_activity.clock_out = datetime_now
            open_activity.clock_out_date = date_today
            open_activity.out_datetime = datetime_now
            open_activity.save()
            _update_attendance_worked_hours(employee, open_activity.attendance_date)

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

        activity.activity_type = PORTAL_WORK_ACTIVITY

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
def public_activity_transition(request):
    """
    Start or end a non-work attendance activity from the public portal.
    Break/lunch transitions require the same PIN, GPS, selfie, geofence, and
    face-presence checks as clock-in/out, but only work activities contribute
    to worked hours.
    """
    if not _ip_is_allowed(request):
        return JsonResponse(
            {"success": False, "message": "Access denied: your network is not allowed."},
            status=403,
        )

    try:
        employee_id = request.POST.get("employee_id")
        activity_type = request.POST.get("activity_type", "").strip().lower()
        transition = request.POST.get("transition", "").strip().lower()

        if not employee_id:
            return JsonResponse(
                {"success": False, "message": "Employee ID required"}, status=200
            )

        if activity_type not in PORTAL_NON_WORK_ACTIVITY_TYPES:
            return JsonResponse(
                {"success": False, "message": "Invalid activity type"}, status=200
            )

        if transition not in {"start", "end"}:
            return JsonResponse(
                {"success": False, "message": "Invalid activity transition"}, status=200
            )

        has_verified_pin, pin_message = _require_verified_pin(request, employee_id)
        if not has_verified_pin:
            return JsonResponse({"success": False, "message": pin_message}, status=200)

        latitude = request.POST.get("latitude")
        longitude = request.POST.get("longitude")

        try:
            employee = Employee.objects.get(id=employee_id, is_active=True)
        except Employee.DoesNotExist:
            return JsonResponse(
                {"success": False, "message": "Employee not found"}, status=200
            )

        _maybe_auto_checkout_employee(employee)

        if _portal_multi_punch_enabled(employee):
            return JsonResponse(
                {
                    "success": False,
                    "message": "Break and lunch are disabled for multiple clock in/out portal mode.",
                },
                status=200,
            )

        work_info = getattr(employee, "employee_work_info", None)
        geo_error = _geofence_check(employee, work_info, latitude, longitude)
        if geo_error:
            return _portal_geo_error_response(geo_error)

        datetime_now = get_real_now()
        date_today = datetime_now.date()
        label = _activity_label(activity_type)
        break_policy = _portal_break_policy(employee)
        break_overage_minutes = 0
        lunch_overage_minutes = 0

        with transaction.atomic():
            open_activity = (
                AttendanceActivity.objects.select_for_update()
                .filter(employee_id=employee, clock_out__isnull=True)
                .order_by("attendance_date", "id")
                .last()
            )

            if not open_activity:
                return JsonResponse(
                    {
                        "success": False,
                        "message": f"{employee.get_full_name()} must clock in first.",
                    },
                    status=200,
                )

            active_type = _portal_activity_type(open_activity)
            attendance_date = open_activity.attendance_date
            attendance = Attendance.objects.filter(
                employee_id=employee,
                attendance_date=attendance_date,
            ).first()

            if not attendance:
                return JsonResponse(
                    {"success": False, "message": "Active attendance not found"},
                    status=200,
                )

            if transition == "start":
                if active_type != PORTAL_WORK_ACTIVITY:
                    active_label = _activity_label(active_type).lower()
                    return JsonResponse(
                        {
                            "success": False,
                            "message": f"Please end your {active_label} first.",
                        },
                        status=200,
                    )

                if activity_type == PORTAL_BREAK_ACTIVITY:
                    current_break_metadata = _portal_break_metadata(
                        employee,
                        attendance_date,
                        break_policy,
                    )
                    if current_break_metadata["break_limit_reached"]:
                        return JsonResponse(
                            {
                                "success": False,
                                "message": "Break limit reached for this attendance day.",
                                **current_break_metadata,
                            },
                            status=200,
                        )

                if activity_type == PORTAL_LUNCH_ACTIVITY and _lunch_taken(
                    employee, attendance_date
                ):
                    return JsonResponse(
                        {
                            "success": False,
                            "message": "Lunch has already been recorded for this attendance day.",
                        },
                        status=200,
                    )

                open_activity.clock_out = datetime_now
                open_activity.clock_out_date = date_today
                open_activity.out_datetime = datetime_now
                open_activity.save()

                activity = AttendanceActivity.objects.create(
                    employee_id=employee,
                    attendance_date=attendance_date,
                    clock_in_date=date_today,
                    shift_day=open_activity.shift_day,
                    clock_in=datetime_now,
                    in_datetime=datetime_now,
                    activity_type=activity_type,
                )
                _save_activity_location(activity, request, latitude, longitude, "in")
                activity.save()
                attendance = _update_attendance_worked_hours(employee, attendance_date)
                message = f"{employee.get_full_name()} started {label.lower()}."
                action_title = f"{label} Started"
                active_activity_type = activity_type
                active_activity_started_at = _activity_datetime_iso(
                    activity, "clock_in_date", "clock_in", "in_datetime"
                )

            else:
                if active_type != activity_type:
                    return JsonResponse(
                        {
                            "success": False,
                            "message": f"No active {label.lower()} found.",
                        },
                        status=200,
                    )

                if activity_type == PORTAL_BREAK_ACTIVITY:
                    duration_seconds = _activity_duration_seconds(
                        open_activity,
                        datetime_now,
                    )
                    allowed_seconds = break_policy["break_minutes_allowed"] * 60
                    overage_seconds = max(duration_seconds - allowed_seconds, 0)
                    if overage_seconds:
                        break_overage_minutes = (overage_seconds + 59) // 60
                elif activity_type == PORTAL_LUNCH_ACTIVITY:
                    duration_seconds = _activity_duration_seconds(
                        open_activity,
                        datetime_now,
                    )
                    allowed_seconds = break_policy["lunch_minutes_allowed"] * 60
                    overage_seconds = max(duration_seconds - allowed_seconds, 0)
                    if overage_seconds:
                        lunch_overage_minutes = (overage_seconds + 59) // 60

                open_activity.clock_out = datetime_now
                open_activity.clock_out_date = date_today
                open_activity.out_datetime = datetime_now
                _save_activity_location(open_activity, request, latitude, longitude, "out")
                open_activity.save()

                new_work_activity = AttendanceActivity.objects.create(
                    employee_id=employee,
                    attendance_date=attendance_date,
                    clock_in_date=date_today,
                    shift_day=open_activity.shift_day,
                    clock_in=datetime_now,
                    in_datetime=datetime_now,
                    activity_type=PORTAL_WORK_ACTIVITY,
                )
                attendance = _update_attendance_worked_hours(employee, attendance_date)
                message = f"{employee.get_full_name()} ended {label.lower()}."
                action_title = f"{label} Ended"
                if activity_type == PORTAL_BREAK_ACTIVITY and break_overage_minutes:
                    message = (
                        f"{employee.get_full_name()} ended {label.lower()}. "
                        f"Break exceeded the allowed time by {break_overage_minutes} minute(s)."
                    )
                elif activity_type == PORTAL_LUNCH_ACTIVITY and lunch_overage_minutes:
                    message = (
                        f"{employee.get_full_name()} ended {label.lower()}. "
                        f"Lunch exceeded the allowed time by {lunch_overage_minutes} minute(s)."
                    )
                active_activity_type = PORTAL_WORK_ACTIVITY
                active_activity_started_at = _activity_datetime_iso(
                    new_work_activity, "clock_in_date", "clock_in", "in_datetime"
                )

        _clear_pin_verification(request)
        response_data = {
            "success": True,
            "message": message,
            "employee_name": employee.get_full_name(),
            "action_title": action_title,
            "activity_type": activity_type,
            "transition": transition,
            "activity_time": datetime_now.strftime("%I:%M %p"),
            "worked_hours": attendance.attendance_worked_hour if attendance else "00:00",
            "active_activity_type": active_activity_type,
            "active_activity_started_at": active_activity_started_at,
            "lunch_taken": _lunch_taken(employee, attendance_date),
            **_portal_break_metadata(employee, attendance_date, break_policy),
            **_portal_activity_duration_metadata(employee, attendance_date, datetime_now),
            **_portal_worked_duration_metadata(employee, attendance_date, datetime_now),
        }
        if break_overage_minutes:
            response_data["break_overage_minutes"] = break_overage_minutes
        if lunch_overage_minutes:
            response_data["lunch_overage_minutes"] = lunch_overage_minutes
        return JsonResponse(response_data, status=200)

    except Exception as e:
        logger.error(f"Public activity transition error: {str(e)}", exc_info=True)
        return JsonResponse(
            {"success": False, "message": f"Error: {str(e)}"}, status=200
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
        face_detected (str): "true" when the client detected one visible face

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

        latitude = request.POST.get("latitude")
        longitude = request.POST.get("longitude")

        # Find employee
        try:
            employee = Employee.objects.get(id=employee_id, is_active=True)
        except Employee.DoesNotExist:
            logger.warning(f"Clock out - Employee not found: {employee_id}")
            return JsonResponse(
                {"success": False, "message": "Employee not found"}, status=200
            )

        _maybe_auto_checkout_employee(employee)

        # Geofence check
        work_info_out = getattr(employee, "employee_work_info", None)
        geo_error = _geofence_check(employee, work_info_out, latitude, longitude)
        if geo_error:
            return _portal_geo_error_response(geo_error)

        # Get current date and time from NTP (tamper-proof)
        datetime_now = get_real_now()
        date_today = datetime_now.date()
        now_str = datetime_now.strftime("%H:%M")
        multi_punch_enabled = _portal_multi_punch_enabled(employee)

        # Check if employee is clocked in
        open_activity = AttendanceActivity.objects.filter(
            employee_id=employee, clock_out__isnull=True
        ).order_by("attendance_date", "id").last()

        multi_punch_state = None
        multi_punch_context = None
        if multi_punch_enabled:
            punch_type = request.POST.get("punch_type", "").strip()
            if open_activity:
                multi_punch_attendance_date = open_activity.attendance_date
            else:
                multi_punch_context = _portal_current_attendance_context(
                    work_info_out,
                    datetime_now,
                    require_shift=True,
                    employee=employee,
                    persist=True,
                )
                if multi_punch_context["error"]:
                    return JsonResponse(
                        {"success": False, "message": multi_punch_context["error"]},
                        status=200,
                    )
                multi_punch_attendance_date = multi_punch_context["attendance_date"]

            multi_punch_state = _portal_multi_punch_state(
                employee,
                multi_punch_attendance_date,
                datetime_now,
            )
            action_error = _portal_multi_punch_action_error(
                multi_punch_state,
                punch_type,
                {PORTAL_MULTI_MORNING_OUT, PORTAL_MULTI_AFTERNOON_OUT},
            )
            if action_error:
                return JsonResponse(
                    {"success": False, "message": action_error, **multi_punch_state},
                    status=200,
                )

        if not open_activity:
            if multi_punch_enabled:
                # A missed Afternoon In leaves no open segment. Start a
                # temporary afternoon segment so the fallback Afternoon Out
                # can still complete the attendance record.
                try:
                    clock_in_attendance_and_activity(
                        employee=employee,
                        date_today=multi_punch_context["date_today"],
                        attendance_date=multi_punch_context["attendance_date"],
                        day=multi_punch_context["day"],
                        now=multi_punch_context["now_str"],
                        shift=multi_punch_context["shift"],
                        minimum_hour=multi_punch_context["minimum_hour"],
                        start_time=multi_punch_context["start_time_sec"],
                        end_time=multi_punch_context["end_time_sec"],
                        in_datetime=datetime_now,
                    )
                except Exception as e:
                    logger.error(
                        "Fallback afternoon clock-in during clock-out failed for %s: %s",
                        employee.id,
                        e,
                        exc_info=True,
                    )
                    return JsonResponse(
                        {"success": False, "message": f"Clock out failed: {str(e)}"},
                        status=200,
                    )

                open_activity = AttendanceActivity.objects.filter(
                    employee_id=employee, clock_out__isnull=True
                ).order_by("attendance_date", "id").last()
                if not open_activity:
                    return JsonResponse(
                        {"success": False, "message": "Failed to create attendance record"},
                        status=200,
                    )

            # Auto clock-in with the current time so clock-in == clock-out
            if not multi_punch_enabled:
                work_info_ci = getattr(employee, "employee_work_info", None)
                datetime_now_ci = get_real_now()
                auto_clock_in_context = _portal_current_attendance_context(
                    work_info_ci,
                    datetime_now_ci,
                    require_shift=True,
                    employee=employee,
                    persist=True,
                )
                if auto_clock_in_context["error"]:
                    return JsonResponse(
                        {"success": False, "message": auto_clock_in_context["error"]},
                        status=200,
                    )
                try:
                    clock_in_attendance_and_activity(
                        employee=employee,
                        date_today=auto_clock_in_context["date_today"],
                        attendance_date=auto_clock_in_context["attendance_date"],
                        day=auto_clock_in_context["day"],
                        now=auto_clock_in_context["now_str"],
                        shift=auto_clock_in_context["shift"],
                        minimum_hour=auto_clock_in_context["minimum_hour"],
                        start_time=auto_clock_in_context["start_time_sec"],
                        end_time=auto_clock_in_context["end_time_sec"],
                        in_datetime=datetime_now_ci,
                    )
                except Exception as e:
                    logger.error(f"Auto clock-in during clock-out failed for {employee.id}: {e}", exc_info=True)
                    return JsonResponse(
                        {"success": False, "message": f"Clock out failed: {str(e)}"}, status=200
                    )
                open_activity = AttendanceActivity.objects.filter(
                    employee_id=employee, clock_out__isnull=True
                ).order_by("attendance_date", "id").last()
                if not open_activity:
                    return JsonResponse(
                        {"success": False, "message": "Failed to create attendance record"}, status=200
                    )

        active_activity_type = _portal_activity_type(open_activity)
        if active_activity_type != PORTAL_WORK_ACTIVITY:
            label = _activity_label(active_activity_type).lower()
            return JsonResponse(
                {
                    "success": False,
                    "message": f"Please end your {label} before clocking out.",
                },
                status=200,
            )

        # Call the business logic function
        try:
            attendance = clock_out_attendance_and_activity(
                employee=employee,
                date_today=date_today,
                now=now_str,
                out_datetime=datetime_now,
                auto_validate=True,
                auto_approve_overtime=False,
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
