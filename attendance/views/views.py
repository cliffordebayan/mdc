"""
views.py

This module contains the view functions for handling HTTP requests and rendering
responses in your application.

Each view function corresponds to a specific URL route and performs the necessary
actions to handle the request, process data, and generate a response.

This module is part of the recruitment project and is intended to
provide the main entry points for interacting with the application's functionality.
"""

import logging
import uuid

from horilla.horilla_settings import (
    DYNAMIC_URL_PATTERNS,
    HORILLA_DATE_FORMATS,
    HORILLA_TIME_FORMATS,
)
from horilla.http import HorillaRedirect
from horilla.methods import remove_dynamic_url

logger = logging.getLogger(__name__)

import calendar
import contextlib
import io
import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs

import pandas as pd
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.core.validators import validate_ipv46_address
from django.db import transaction
from django.db.models import ProtectedError, Q
from django.forms import ValidationError
from django.http import (
    FileResponse,
    HttpResponse,
    HttpResponseBadRequest,
    JsonResponse,
    QueryDict,
)
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone as django_timezone
from django.utils.timezone import now
from django.utils.translation import gettext as __
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods
from xlsxwriter.utility import xl_col_to_name

from attendance.filters import (
    AttendanceActivityFilter,
    AttendanceActivityReGroup,
    AttendanceFilters,
    AttendanceOverTimeFilter,
    AttendanceOvertimeReGroup,
    AttendanceReGroup,
    LateComeEarlyOutFilter,
    LateComeEarlyOutReGroup,
)
from attendance.forms import (
    ATTENDANCE_PREMIUM_EXPORT_FIELDS,
    AttendanceActivityExportForm,
    AttendanceActivityUpdateForm,
    AttendanceExportForm,
    AttendanceForm,
    AttendanceOverTimeExportForm,
    AttendanceOverTimeForm,
    AttendanceRequestCommentForm,
    AttendanceUpdateForm,
    AttendanceValidationConditionForm,
    GraceTimeAssignForm,
    GraceTimeForm,
    LateComeEarlyOutExportForm,
    NewRequestForm,
)
from attendance.export_jobs import (
    create_export_job,
    file_path as export_job_file_path,
    read_job_status,
    start_export_job,
)
from attendance.methods.utils import (
    Request,
    WEEKDAY_SHIFT_FALLBACK_DAYS,
    attendance_day_checking,
    calculate_schedule_end_overtime,
    format_time,
    is_reportingmanger,
    monthly_leave_days,
    paginator_qry,
    parse_date,
    parse_datetime,
    parse_time,
    recalculate_attendance_for_shift,
    shift_schedule_with_weekday_fallback,
    sort_activity_dicts,
    strtime_seconds,
)
from attendance.models import (
    Attendance,
    AttendanceActivity,
    AttendanceGeneralSetting,
    AttendanceLateComeEarlyOut,
    AttendanceOverTime,
    AttendanceRequestComment,
    AttendanceRequestFile,
    AttendanceValidationCondition,
    BatchAttendance,
    GraceTime,
    WorkRecords,
)
from attendance.views.handle_attendance_errors import handle_attendance_errors
from attendance.views.process_attendance_data import process_attendance_data
from base.forms import AttendanceAllowedIPForm, TrackLateComeEarlyOutForm
from base.methods import (
    choosesubordinates,
    closest_numbers,
    eval_validate,
    export_data,
    filtersubordinates,
    filtersubordinatesemployeemodel,
    format_export_value,
    get_key_instances,
    get_pagination,
    sortby,
)
from base.models import (
    AttendanceAllowedIP,
    Branch,
    EmployeeShiftDay,
    EmployeeShiftSchedule,
    Holidays,
    PayrollGroup,
    TrackLateComeEarlyOut,
    WorkType,
)
from employee.filters import EmployeeFilter
from employee.models import Employee, EmployeeWorkInformation
from leave.models import LeaveRequest
from horilla.horilla_middlewares import _thread_locals
from horilla.decorators import (
    hx_request_required,
    install_required,
    login_required,
    manager_can_enter,
    permission_required,
)
from horilla.group_by import group_by_queryset
from notifications.signals import notify

ACTIVITY_IMPORT_HEADERS = [
    "Employee No",
    "Employee",
    "Branch",
    "Department",
    "Attendance Date",
    "Day",
    "In Date",
    "Check In",
    "Check In Image",
    "Out Date",
    "Check Out",
    "Check Out Image",
    "Shift",
    "Work Type",
    "Min Hour",
    "Work Hours",
    "Pending Hour",
    "Overtime",
    "Approved By",
    "Location",
    "Maps",
]

ACTIVITY_IMPORT_SAMPLE_ROW = {
    "Employee No": "EMP-001",
    "Employee": "John Doe",
    "Branch": "Main Branch",
    "Department": "Operations",
    "Attendance Date": "2026-04-01",
    "Day": "Tuesday",
    "In Date": "2026-04-01",
    "Check In": "08:00",
    "Check In Image": "checkin.jpg",
    "Out Date": "2026-04-01",
    "Check Out": "17:00",
    "Check Out Image": "checkout.jpg",
    "Shift": "Day Shift",
    "Work Type": "Office",
    "Min Hour": "08:00",
    "Work Hours": "09:00",
    "Pending Hour": "00:00",
    "Overtime": "01:00",
    "Approved By": "EMP-002",
    "Location": "Main Office",
    "Maps": "https://www.google.com/maps?q=14.5995,120.9842",
}


def _delete_blocked_message(protected_objects):
    model_verbose_names_set = {
        __(obj._meta.verbose_name.capitalize()) for obj in protected_objects
    }
    model_names_str = ", ".join(model_verbose_names_set)
    return _("Deletion blocked by related records: {}.").format(model_names_str)


def build_my_attendance_activity_meta(paginated_attendances):
    """
    Build activity-derived metadata for own-attendance daily rows.
    """
    activity_meta_by_attendance = {}
    if not paginated_attendances:
        return activity_meta_by_attendance

    attendance_rows = list(getattr(paginated_attendances, "object_list", []))
    if not attendance_rows:
        return activity_meta_by_attendance

    def _get_employee_id(row):
        employee_id = getattr(row, "employee_id_id", None)
        if employee_id:
            return employee_id
        employee = getattr(row, "employee_id", None)
        if isinstance(employee, int):
            return employee
        return getattr(employee, "id", None) or getattr(employee, "pk", None)

    def _get_attendance_date(row):
        return getattr(row, "attendance_date", None) or getattr(row, "clock_in_date", None)

    employee_ids = set()
    for row in attendance_rows:
        employee_id = _get_employee_id(row)
        if employee_id:
            employee_ids.add(employee_id)
    attendance_dates = {
        attendance_date
        for row in attendance_rows
        if (attendance_date := _get_attendance_date(row))
    }
    if not employee_ids or not attendance_dates:
        return activity_meta_by_attendance

    activities = (
        AttendanceActivity.objects.filter(
            employee_id_id__in=employee_ids,
            attendance_date__in=attendance_dates,
        )
        .order_by("clock_in_date", "clock_in", "id")
    )

    activities_by_key = defaultdict(list)
    for activity in activities:
        key = f"{_get_employee_id(activity)}|{_get_attendance_date(activity)}"
        activities_by_key[key].append(activity)

    for attendance in attendance_rows:
        key = f"{_get_employee_id(attendance)}|{_get_attendance_date(attendance)}"
        row_activities = activities_by_key.get(key, [])

        first_check_in_image_activity = next(
            (activity for activity in row_activities if activity.clock_in_selfie),
            None,
        )
        last_check_out_image_activity = next(
            (
                activity
                for activity in reversed(row_activities)
                if activity.clock_out_selfie
            ),
            None,
        )
        first_check_in_location_activity = next(
            (
                activity
                for activity in row_activities
                if activity.clock_in_gps_address
                or (activity.gps_address and activity.clock_out is None)
            ),
            None,
        )
        first_check_in_maps_activity = next(
            (
                activity
                for activity in row_activities
                if (
                    activity.clock_in_latitude is not None
                    and activity.clock_in_longitude is not None
                )
                or (
                    activity.clock_out is None
                    and activity.latitude is not None
                    and activity.longitude is not None
                )
            ),
            None,
        )
        last_check_out_location_activity = next(
            (
                activity
                for activity in reversed(row_activities)
                if activity.clock_out_gps_address
                or (activity.gps_address and activity.clock_out is not None)
            ),
            None,
        )
        last_check_out_maps_activity = next(
            (
                activity
                for activity in reversed(row_activities)
                if (
                    activity.clock_out_latitude is not None
                    and activity.clock_out_longitude is not None
                )
                or (
                    activity.clock_out is not None
                    and activity.latitude is not None
                    and activity.longitude is not None
                )
            ),
            None,
        )

        check_in_location = (
            first_check_in_location_activity.clock_in_gps_address
            if first_check_in_location_activity
            and first_check_in_location_activity.clock_in_gps_address
            else (
                first_check_in_location_activity.gps_address
                if first_check_in_location_activity
                else None
            )
        )
        check_out_location = (
            last_check_out_location_activity.clock_out_gps_address
            if last_check_out_location_activity
            and last_check_out_location_activity.clock_out_gps_address
            else (
                last_check_out_location_activity.gps_address
                if last_check_out_location_activity
                else None
            )
        )

        check_in_maps_url = None
        if first_check_in_maps_activity:
            if (
                first_check_in_maps_activity.clock_in_latitude is not None
                and first_check_in_maps_activity.clock_in_longitude is not None
            ):
                check_in_maps_url = (
                    "https://www.google.com/maps?q="
                    f"{first_check_in_maps_activity.clock_in_latitude},"
                    f"{first_check_in_maps_activity.clock_in_longitude}"
                )
            else:
                check_in_maps_url = (
                    "https://www.google.com/maps?q="
                    f"{first_check_in_maps_activity.latitude},"
                    f"{first_check_in_maps_activity.longitude}"
                )

        check_out_maps_url = None
        if last_check_out_maps_activity:
            if (
                last_check_out_maps_activity.clock_out_latitude is not None
                and last_check_out_maps_activity.clock_out_longitude is not None
            ):
                check_out_maps_url = (
                    "https://www.google.com/maps?q="
                    f"{last_check_out_maps_activity.clock_out_latitude},"
                    f"{last_check_out_maps_activity.clock_out_longitude}"
                )
            else:
                check_out_maps_url = (
                    "https://www.google.com/maps?q="
                    f"{last_check_out_maps_activity.latitude},"
                    f"{last_check_out_maps_activity.longitude}"
                )

        meta = {
            "check_in_image_url": (
                first_check_in_image_activity.clock_in_selfie.url
                if first_check_in_image_activity
                else None
            ),
            "check_out_image_url": (
                last_check_out_image_activity.clock_out_selfie.url
                if last_check_out_image_activity
                else None
            ),
            "check_in_location": check_in_location,
            "check_in_maps_url": check_in_maps_url,
            "check_out_location": check_out_location,
            "check_out_maps_url": check_out_maps_url,
            "location": check_out_location or check_in_location,
            "maps_url": check_out_maps_url or check_in_maps_url,
        }

        attendance.activity_meta = meta
        activity_meta_by_attendance[attendance.id] = meta

    return activity_meta_by_attendance


def _activity_type(activity):
    activity_type = getattr(activity, "activity_type", "work") or "work"
    return activity_type if activity_type in {"work", "break", "lunch"} else "work"


def _activity_in_datetime(activity):
    direct_datetime = getattr(activity, "in_datetime", None)
    if direct_datetime:
        return _naive_local_datetime(direct_datetime)
    if activity.clock_in_date and activity.clock_in:
        return datetime.combine(activity.clock_in_date, activity.clock_in)
    return datetime.min


def _activity_out_datetime(activity):
    direct_datetime = getattr(activity, "out_datetime", None)
    if direct_datetime:
        return _naive_local_datetime(direct_datetime)
    if activity.clock_out_date and activity.clock_out:
        return datetime.combine(activity.clock_out_date, activity.clock_out)
    return datetime.min


def _activity_display_in_datetime(activity):
    if activity.clock_in_date and activity.clock_in:
        return datetime.combine(activity.clock_in_date, activity.clock_in)
    return _activity_in_datetime(activity)


def _activity_display_out_datetime(activity):
    if activity.clock_out_date and activity.clock_out:
        return datetime.combine(activity.clock_out_date, activity.clock_out)
    return _activity_out_datetime(activity)


def _naive_local_datetime(value):
    if django_timezone.is_aware(value):
        return django_timezone.localtime(value).replace(tzinfo=None)
    return value


def _activity_map_url(activity, clock_event):
    if clock_event == "in":
        return activity.clock_in_maps_url
    return activity.clock_out_maps_url


def _activity_location(activity, clock_event):
    if clock_event == "in":
        if activity.clock_in_gps_address:
            return activity.clock_in_gps_address
        if activity.gps_address and not activity.clock_out:
            return activity.gps_address
    else:
        if activity.clock_out_gps_address:
            return activity.clock_out_gps_address
        if activity.gps_address and activity.clock_out:
            return activity.gps_address
    return ""


def _daily_activity_segment(activity):
    return SimpleNamespace(
        activity=activity,
        activity_type=_activity_type(activity),
        in_datetime=getattr(activity, "in_datetime", None),
        out_datetime=getattr(activity, "out_datetime", None),
        clock_in=activity.clock_in,
        clock_in_date=activity.clock_in_date,
        clock_out=activity.clock_out,
        clock_out_date=activity.clock_out_date,
        clock_in_selfie=activity.clock_in_selfie,
        clock_out_selfie=activity.clock_out_selfie,
        clock_in_location=_activity_location(activity, "in"),
        clock_out_location=_activity_location(activity, "out"),
        clock_in_map_url=_activity_map_url(activity, "in"),
        clock_out_map_url=_activity_map_url(activity, "out"),
    )


def _effective_work_out_segment(work_out, latest_unclosed):
    """
    Build the work_out display segment. When an employee clocked in after their
    last clock_out and never clocked out (e.g., forgot to clock out after overtime),
    use that later clock_in time as the effective clock_out for display.
    latest_unclosed is pre-validated to be more recent than work_out's clock_out.
    """
    if work_out is None:
        return None
    seg = _daily_activity_segment(work_out)
    if latest_unclosed and latest_unclosed.clock_in_date and latest_unclosed.clock_in:
        seg.clock_out = latest_unclosed.clock_in
        seg.clock_out_date = latest_unclosed.clock_in_date
    return seg


def _activity_duration_seconds(activity):
    if not activity or not activity.clock_out:
        return 0

    if activity.in_datetime and activity.out_datetime:
        start = activity.in_datetime
        end = activity.out_datetime
    elif activity.clock_in_date and activity.clock_in and activity.clock_out_date:
        start = datetime.combine(activity.clock_in_date, activity.clock_in)
        end = datetime.combine(activity.clock_out_date, activity.clock_out)
    else:
        return 0
    if not start or not end or end <= start:
        return 0
    return int((end - start).total_seconds())


def _activity_total_hours(activities):
    return format_time(sum(_activity_duration_seconds(activity) for activity in activities))


NIGHT_DIFFERENTIAL_START = time(22, 0)
NIGHT_DIFFERENTIAL_END = time(6, 0)
ATTENDANCE_PREMIUM_EXPORT_FIELD_NAMES = {
    field_name for field_name, _ in ATTENDANCE_PREMIUM_EXPORT_FIELDS
}

ATTENDANCE_TAB_VALIDATE = "validate"
ATTENDANCE_TAB_OVERTIME = "overtime"
ATTENDANCE_TAB_VALIDATED = "validated"
ATTENDANCE_DEFAULT_TAB = ATTENDANCE_TAB_VALIDATE

ATTENDANCE_TAB_CONFIG = {
    ATTENDANCE_TAB_VALIDATE: {
        "key": ATTENDANCE_TAB_VALIDATE,
        "target": "#tab_1",
        "panel_id": "tab_1",
        "page_param": "vpage",
        "context_name": "validate_attendances",
        "rows_context_name": "validate_attendance_rows",
        "ids_context_name": "validate_attendances_ids",
        "checkbox_header_class": "validate",
        "checkbox_class": "validate-row",
        "confirmation_type": "validate",
        "detail_query": "validate=true",
        "table_id": "validate-attendance-table",
        "table_name": "validate_attendances_tab_v2",
        "field_container_id": "fieldContainerTableValidate",
        "empty_message": _("No attendance to validate."),
    },
    ATTENDANCE_TAB_OVERTIME: {
        "key": ATTENDANCE_TAB_OVERTIME,
        "target": "#tab_3",
        "panel_id": "tab_3",
        "page_param": "opage",
        "context_name": "overtime_attendances",
        "rows_context_name": "overtime_attendance_rows",
        "ids_context_name": "ot_attendances_ids",
        "checkbox_header_class": "ot-attendances",
        "checkbox_class": "ot-attendance-row",
        "confirmation_type": "overtime",
        "detail_query": "ot=true",
        "table_id": "ot-attendance-table",
        "table_name": "ot_attendances_tab_v2",
        "field_container_id": "fieldContainerTableOverTime",
        "empty_message": _("No validated attendance to show."),
    },
    ATTENDANCE_TAB_VALIDATED: {
        "key": ATTENDANCE_TAB_VALIDATED,
        "target": "#tab_2",
        "panel_id": "tab_2",
        "page_param": "page",
        "context_name": "attendances",
        "rows_context_name": "attendance_rows",
        "ids_context_name": "attendances_ids",
        "checkbox_header_class": "all-attendances",
        "checkbox_class": "all-attendance-row",
        "confirmation_type": "",
        "detail_query": "",
        "table_id": "validated-attendance-table",
        "table_name": "validated_attendances_tab_v2",
        "field_container_id": "fieldContainerTable",
        "empty_message": _("No search result found!"),
    },
}

ATTENDANCE_RELATED_FIELDS = (
    "employee_id",
    "employee_id__employee_work_info",
    "employee_id__employee_work_info__business_unit_id",
    "employee_id__employee_work_info__branch_id",
    "employee_id__employee_work_info__department_id",
    "employee_id__employee_work_info__payroll_group_id",
    "employee_id__employee_work_info__shift_id",
    "employee_id__employee_work_info__work_type_id",
    "shift_id",
    "work_type_id",
    "attendance_day",
)

ATTENDANCE_ACTIVITY_RELATED_FIELDS = (
    "employee_id",
    "employee_id__employee_work_info",
    "employee_id__employee_work_info__business_unit_id",
    "employee_id__employee_work_info__branch_id",
    "employee_id__employee_work_info__department_id",
    "employee_id__employee_work_info__payroll_group_id",
    "employee_id__employee_work_info__shift_id",
    "employee_id__employee_work_info__work_type_id",
    "shift_day",
)


def attendance_active_tab(tab):
    if tab in ATTENDANCE_TAB_CONFIG:
        return tab
    return ATTENDANCE_DEFAULT_TAB


def attendance_tab_querystring(request, active_tab):
    data = request.GET.copy()
    data["tab"] = active_tab
    return data.urlencode()


def attendance_preload_queryset(queryset):
    if not hasattr(queryset, "select_related"):
        return queryset
    return queryset.select_related(*ATTENDANCE_RELATED_FIELDS)


def _premium_empty_buckets():
    return {field_name: 0 for field_name in ATTENDANCE_PREMIUM_EXPORT_FIELD_NAMES}


def _premium_bucket_time(seconds):
    return format_time(max(0, int(seconds or 0)))


def _segment_datetime(segment, row_date, date_attr, time_attr, direct_attr):
    direct_value = getattr(segment, direct_attr, None)
    direct_datetime = _naive_local_datetime(direct_value) if direct_value else None
    if direct_datetime:
        return direct_datetime
    clock_time = getattr(segment, time_attr, None)
    if not clock_time:
        return None
    clock_date = getattr(segment, date_attr, None) or row_date
    if not clock_date:
        return None
    return datetime.combine(clock_date, clock_time)


def _work_segment_interval(segment, row_date):
    start = _segment_datetime(
        segment, row_date, "clock_in_date", "clock_in", "in_datetime"
    )
    end = _segment_datetime(
        segment, row_date, "clock_out_date", "clock_out", "out_datetime"
    )
    if not start or not end:
        return None
    if end <= start and getattr(segment, "clock_out_date", None) is None:
        end += timedelta(days=1)
    if end <= start:
        return None
    return start, end


def _row_schedule_end_datetime(row):
    schedule = getattr(row, "schedule", None)
    attendance_date = getattr(row, "attendance_date", None)
    if not schedule or not attendance_date or not getattr(schedule, "end_time", None):
        return None
    start_time = getattr(schedule, "start_time", None)
    end_time = schedule.end_time
    is_night_shift = bool(getattr(schedule, "is_night_shift", False))
    if start_time and start_time > end_time:
        is_night_shift = True
    end_date = attendance_date + timedelta(days=1) if is_night_shift else attendance_date
    return datetime.combine(end_date, end_time)


def _interval_overlap_seconds(start, end, window_start, window_end):
    latest_start = max(start, window_start)
    earliest_end = min(end, window_end)
    if earliest_end <= latest_start:
        return 0
    return int((earliest_end - latest_start).total_seconds())


def _night_differential_seconds(start, end):
    total = 0
    current = start.date() - timedelta(days=1)
    while current <= end.date():
        window_start = datetime.combine(current, NIGHT_DIFFERENTIAL_START)
        window_end = datetime.combine(current + timedelta(days=1), NIGHT_DIFFERENTIAL_END)
        total += _interval_overlap_seconds(start, end, window_start, window_end)
        current += timedelta(days=1)
    return total


def _premium_row_category(row):
    holiday_type = getattr(row, "holiday_type", None)
    if holiday_type not in {"regular", "special"}:
        holiday_type = None
    return holiday_type, bool(getattr(row, "is_rest_day", False))


def _add_premium_bucket(buckets, field_name, seconds):
    if seconds > 0:
        buckets[field_name] += seconds


def _add_premium_interval_buckets(buckets, holiday_type, is_rest_day, seconds, nd_seconds, overtime):
    if seconds <= 0:
        return

    if holiday_type == "regular" and is_rest_day:
        _add_premium_bucket(
            buckets,
            "daily_ot_regular_holiday_rest_day" if overtime else "daily_rest_day_regular_holiday",
            seconds,
        )
        _add_premium_bucket(
            buckets,
            (
                "daily_night_differential_rest_day_regular_holiday_overtime"
                if overtime
                else "daily_night_differential_rest_day_regular_holiday"
            ),
            nd_seconds,
        )
        return

    if holiday_type == "special" and is_rest_day:
        _add_premium_bucket(
            buckets,
            "daily_ot_special_holiday_rest_day" if overtime else "daily_rest_day_special_holiday",
            seconds,
        )
        _add_premium_bucket(
            buckets,
            (
                "daily_night_differential_rest_day_special_holiday_overtime"
                if overtime
                else "daily_night_differential_rest_day_special_holiday"
            ),
            nd_seconds,
        )
        return

    if holiday_type == "regular":
        _add_premium_bucket(
            buckets,
            "daily_ot_worked_regular_holiday" if overtime else "daily_worked_regular_holiday",
            seconds,
        )
        _add_premium_bucket(
            buckets,
            (
                "daily_night_differential_regular_holiday_overtime"
                if overtime
                else "daily_night_differential_regular_holiday"
            ),
            nd_seconds,
        )
        return

    if holiday_type == "special":
        _add_premium_bucket(
            buckets,
            "daily_ot_worked_special_holiday" if overtime else "daily_worked_special_holiday",
            seconds,
        )
        _add_premium_bucket(
            buckets,
            (
                "daily_night_differential_special_holiday_overtime"
                if overtime
                else "daily_night_differential_special_holiday"
            ),
            nd_seconds,
        )
        return

    if is_rest_day:
        _add_premium_bucket(
            buckets,
            "daily_ot_worked_rest_day" if overtime else "daily_worked_rest_day",
            seconds,
        )
        _add_premium_bucket(
            buckets,
            (
                "daily_night_differential_rest_day_overtime"
                if overtime
                else "daily_night_differential_rest_day"
            ),
            nd_seconds,
        )
        return

    _add_premium_bucket(
        buckets,
        "daily_night_differential_overtime" if overtime else "daily_night_differential",
        nd_seconds,
    )


def _attendance_activity_premium_buckets(row):
    cached = getattr(row, "_premium_export_buckets", None)
    if cached is not None:
        return cached

    buckets = _premium_empty_buckets()
    if not row or getattr(row, "is_leave_only", False):
        if row is not None:
            row._premium_export_buckets = buckets
        return buckets

    holiday_type, is_rest_day = _premium_row_category(row)
    shift_end = _row_schedule_end_datetime(row)

    for segment in getattr(row, "work_segments", []) or []:
        interval = _work_segment_interval(segment, getattr(row, "attendance_date", None))
        if not interval:
            continue
        start, end = interval
        split_points = [start, end]
        if shift_end and start < shift_end < end:
            split_points.insert(1, shift_end)

        for idx in range(len(split_points) - 1):
            part_start = split_points[idx]
            part_end = split_points[idx + 1]
            seconds = int((part_end - part_start).total_seconds())
            nd_seconds = _night_differential_seconds(part_start, part_end)
            overtime = bool(shift_end and part_start >= shift_end)
            _add_premium_interval_buckets(
                buckets, holiday_type, is_rest_day, seconds, nd_seconds, overtime
            )

    row._premium_export_buckets = buckets
    return buckets


def _attendance_row_hours(attendance):
    if not attendance:
        return SimpleNamespace(
            shift=None,
            work_type=None,
            min_hour="",
            work_hours="",
            pending_hour="",
            overtime="",
        )
    return SimpleNamespace(
        shift=attendance.shift_id,
        work_type=attendance.work_type_id,
        min_hour=attendance.minimum_hour,
        work_hours=attendance.attendance_worked_hour,
        pending_hour=attendance.hours_pending(),
        overtime=attendance.attendance_overtime,
    )


def _related_object_id(instance, field_name):
    direct_id = getattr(instance, f"{field_name}_id", None)
    if direct_id:
        return direct_id
    related_obj = getattr(instance, field_name, None)
    if isinstance(related_obj, int):
        return related_obj
    return getattr(related_obj, "id", None) or getattr(related_obj, "pk", None)


def _employee_work_info(employee):
    return getattr(employee, "employee_work_info", None)


def _row_shift(attendance, employee):
    shift = getattr(attendance, "shift_id", None) if attendance else None
    if shift:
        return shift
    work_info = _employee_work_info(employee)
    return getattr(work_info, "shift_id", None) if work_info else None


def _row_shift_id(attendance, employee):
    shift_id = _related_object_id(attendance, "shift_id") if attendance else None
    if shift_id:
        return shift_id
    work_info = _employee_work_info(employee)
    return _related_object_id(work_info, "shift_id") if work_info else None


def _row_shift_day(attendance, activity_shift_day, attendance_date=None):
    day = getattr(attendance, "attendance_day", None) if attendance else None
    if day:
        return day
    if activity_shift_day:
        return activity_shift_day
    if attendance_date:
        return attendance_date.strftime("%A").lower()
    return None


def _row_shift_day_id(attendance, activity_shift_day):
    day_id = _related_object_id(attendance, "attendance_day") if attendance else None
    if day_id:
        return day_id
    direct_id = getattr(activity_shift_day, "id", None) or getattr(
        activity_shift_day, "pk", None
    )
    return direct_id


def _row_shift_day_name(attendance, activity_shift_day, attendance_date):
    day = _row_shift_day(attendance, activity_shift_day, attendance_date)
    day_name = getattr(day, "day", None)
    if day_name:
        return str(day_name).lower()
    if isinstance(day, str):
        return day.lower()
    if attendance_date:
        return attendance_date.strftime("%A").lower()
    return None


SHIFT_SCHEDULE_DAY_ORDER = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
SHIFT_SCHEDULE_DAY_LABELS = {
    "monday": "Mon",
    "tuesday": "Tue",
    "wednesday": "Wed",
    "thursday": "Thu",
    "friday": "Fri",
    "saturday": "Sat",
    "sunday": "Sun",
}


def _normalize_shift_day_name(day_name):
    if not day_name:
        return None
    normalized = str(day_name).strip().lower()
    if not normalized:
        return None
    for full_day_name in SHIFT_SCHEDULE_DAY_ORDER:
        if normalized == full_day_name or normalized[:3] == full_day_name[:3]:
            return full_day_name
    return normalized


def _shift_schedule_days_label(day_names):
    ordered_days = [
        day_name
        for day_name in SHIFT_SCHEDULE_DAY_ORDER
        if day_name in set(day_names or [])
    ]
    return ", ".join(SHIFT_SCHEDULE_DAY_LABELS[day_name] for day_name in ordered_days)


def _shift_schedule_days_by_shift_id(shift_ids):
    shift_ids = {shift_id for shift_id in shift_ids if shift_id}
    if not shift_ids:
        return {}

    schedules = EmployeeShiftSchedule.objects.filter(shift_id_id__in=shift_ids)
    if hasattr(schedules, "select_related"):
        schedules = schedules.select_related("day")

    days_by_shift_id = {shift_id: set() for shift_id in shift_ids}
    for schedule in schedules:
        shift_id = getattr(schedule, "shift_id_id", None)
        day_name = getattr(getattr(schedule, "day", None), "day", None)
        day_name = _normalize_shift_day_name(day_name)
        if (
            shift_id
            and day_name
            and getattr(schedule, "start_time", None)
            and getattr(schedule, "end_time", None)
            and not getattr(schedule, "is_rest_day", False)
        ):
            days_by_shift_id.setdefault(shift_id, set()).add(day_name)

    return {
        shift_id: [
            day_name
            for day_name in SHIFT_SCHEDULE_DAY_ORDER
            if day_name in day_names
        ]
        for shift_id, day_names in days_by_shift_id.items()
    }


def _shift_day_is_rest_day(shift_schedule_days, shift_day_name):
    shift_day_name = _normalize_shift_day_name(shift_day_name)
    if not shift_schedule_days or not shift_day_name:
        return False
    return shift_day_name not in set(shift_schedule_days)


def _schedule_is_blank_rest_day(schedule):
    if not schedule:
        return False
    return not getattr(schedule, "start_time", None) or not getattr(
        schedule, "end_time", None
    )


def _schedule_is_explicit_rest_day(schedule):
    return bool(schedule and getattr(schedule, "is_rest_day", False))


def _row_attendance_date(attendance, activity):
    attendance_date = getattr(attendance, "attendance_date", None) if attendance else None
    if attendance_date:
        return attendance_date
    return getattr(activity, "attendance_date", None) if activity else None


def _activity_late_early_duration(work_in, work_out, attendance, schedule, report_type):
    if not schedule:
        return ""

    if report_type == "late_come":
        if not work_in or not schedule.start_time:
            return ""
        clock_in_dt = _activity_display_in_datetime(work_in)
        if clock_in_dt == datetime.min:
            return ""
        start_date = _row_attendance_date(attendance, work_in) or clock_in_dt.date()
        if (
            schedule.is_night_shift
            and clock_in_dt.date() <= start_date
            and clock_in_dt.time() < schedule.start_time
        ):
            clock_in_dt = clock_in_dt + timedelta(days=1)
        schedule_dt = datetime.combine(start_date, schedule.start_time)
        diff = clock_in_dt - schedule_dt
    elif report_type == "early_out":
        if not work_out or not schedule.end_time:
            return ""
        clock_out_dt = _activity_display_out_datetime(work_out)
        if clock_out_dt == datetime.min:
            return ""
        start_date = _row_attendance_date(attendance, work_out) or clock_out_dt.date()
        if (
            schedule.is_night_shift
            and clock_out_dt.date() <= start_date
            and clock_out_dt.time() <= schedule.end_time
        ):
            clock_out_dt = clock_out_dt + timedelta(days=1)
        end_date = start_date + timedelta(days=1) if schedule.is_night_shift else clock_out_dt.date()
        schedule_dt = datetime.combine(end_date, schedule.end_time)
        diff = schedule_dt - clock_out_dt
    else:
        return ""

    seconds = int(diff.total_seconds())
    if seconds <= 0:
        return ""
    return format_time(seconds)


def _employee_shift_schedules(schedule_keys):
    if not schedule_keys:
        return {}
    shift_ids = {shift_id for shift_id, _, _ in schedule_keys if shift_id}
    attendance_day_ids = {
        attendance_day_id
        for _, attendance_day_id, _ in schedule_keys
        if attendance_day_id
    }
    attendance_day_names = {
        attendance_day_name
        for _, _, attendance_day_name in schedule_keys
        if attendance_day_name
    }
    if not shift_ids or (not attendance_day_ids and not attendance_day_names):
        return {}

    schedules = EmployeeShiftSchedule.objects.filter(shift_id_id__in=shift_ids)
    if hasattr(schedules, "filter"):
        if attendance_day_ids and attendance_day_names:
            schedules = schedules.filter(
                Q(day_id__in=attendance_day_ids)
                | Q(day__day__in=attendance_day_names)
                | Q(day__day__in=WEEKDAY_SHIFT_FALLBACK_DAYS)
            )
        elif attendance_day_ids:
            schedules = schedules.filter(
                Q(day_id__in=attendance_day_ids)
                | Q(day__day__in=WEEKDAY_SHIFT_FALLBACK_DAYS)
            )
        else:
            schedules = schedules.filter(
                Q(day__day__in=attendance_day_names)
                | Q(day__day__in=WEEKDAY_SHIFT_FALLBACK_DAYS)
            )
    else:
        schedules = [
            schedule
            for schedule in schedules
            if getattr(schedule, "day_id", None) in attendance_day_ids
            or str(getattr(getattr(schedule, "day", None), "day", "")).lower()
            in attendance_day_names
            or str(getattr(getattr(schedule, "day", None), "day", "")).lower()
            in WEEKDAY_SHIFT_FALLBACK_DAYS
        ]

    schedule_by_key = {}
    for schedule in schedules:
        shift_id = schedule.shift_id_id
        day_id = getattr(schedule, "day_id", None)
        day_name = getattr(getattr(schedule, "day", None), "day", None)
        day_name = str(day_name).lower() if day_name else None
        if day_id:
            schedule_by_key[(shift_id, day_id, None)] = schedule
        if day_name:
            schedule_by_key[(shift_id, None, day_name)] = schedule
            if day_name in WEEKDAY_SHIFT_FALLBACK_DAYS:
                schedule_by_key.setdefault(
                    (shift_id, None, "__weekday_fallback__"), schedule
                )
    return schedule_by_key


def _leave_days_for_date(lr, date_val):
    end = lr.end_date or lr.start_date
    if date_val == lr.start_date:
        breakdown = lr.start_date_breakdown
    elif date_val == end:
        breakdown = lr.end_date_breakdown
    else:
        return 1.0
    return 0.5 if breakdown in ("first_half", "second_half") else 1.0


def _leave_type_for_row(leave_by_key, emp_id, date_val):
    if date_val and date_val > date.today():
        return None
    data = leave_by_key.get((emp_id, date_val))
    return data[0] if data else None


def _leave_days_for_row(leave_by_key, emp_id, date_val):
    if date_val and date_val > date.today():
        return None
    data = leave_by_key.get((emp_id, date_val))
    return data[1] if data else None


def build_daily_activity_rows(
    attendance_activities,
    date_from=None,
    date_to=None,
    attendance_by_key=None,
):
    """
    Convert activity records into one row per employee and attendance date.
    Break/lunch rows keep all underlying activity ids for bulk actions.
    Leave-only rows are added for approved leaves on current/past dates
    where there are no activity records.
    """

    activities = list(
        attendance_activities.select_related(
            *ATTENDANCE_ACTIVITY_RELATED_FIELDS,
        )
    )
    grouped = {}
    employee_ids = set()
    attendance_dates = set()

    for activity in activities:
        key = (activity.employee_id_id, activity.attendance_date)
        if key not in grouped:
            grouped[key] = {
                "employee": activity.employee_id,
                "attendance_date": activity.attendance_date,
                "shift_day": activity.shift_day,
                "activities": [],
                "activity_ids": [],
            }
        grouped[key]["activities"].append(activity)
        grouped[key]["activity_ids"].append(activity.id)
        employee_ids.add(activity.employee_id_id)
        if activity.attendance_date:
            attendance_dates.add(activity.attendance_date)

    if attendance_by_key is None:
        attendances = attendance_preload_queryset(
            Attendance.objects.filter(
                employee_id_id__in=employee_ids,
                attendance_date__in=attendance_dates,
            )
        )
        attendance_by_key = {
            (attendance.employee_id_id, attendance.attendance_date): attendance
            for attendance in attendances
        }

    leave_by_key = {}
    holiday_by_date = {}
    holiday_type_by_date = {}
    all_check_dates = set(attendance_dates)
    if date_from and date_to:
        current = date_from
        while current <= date_to:
            all_check_dates.add(current)
            current += timedelta(days=1)

    if employee_ids:
        today_date = date.today()
        # Only fetch leaves that have started on or before today — future leaves are excluded.
        # The inner `current <= today_date` cap handles leaves that span into the future.
        leave_requests = LeaveRequest.objects.filter(
            employee_id_id__in=employee_ids,
            status="approved",
            start_date__lte=today_date,
        ).select_related("leave_type_id")

        for lr in leave_requests:
            end = lr.end_date or lr.start_date
            current = lr.start_date
            while current <= end:
                if current <= today_date:
                    all_check_dates.add(current)
                    if (lr.employee_id_id, current) not in leave_by_key:
                        leave_by_key[(lr.employee_id_id, current)] = (
                            lr.leave_type_id.name,
                            _leave_days_for_date(lr, current),
                        )
                current += timedelta(days=1)

    if employee_ids and attendance_dates:
        min_date = date_from if date_from else min(attendance_dates)
        max_date = date_to if date_to else max(all_check_dates or attendance_dates)
        holidays = Holidays.objects.filter(
            start_date__lte=max_date,
            end_date__gte=min_date,
        )
        for holiday in holidays:
            current = holiday.start_date
            end = holiday.end_date or holiday.start_date
            while current <= end:
                if current in all_check_dates and current not in holiday_by_date:
                    holiday_by_date[current] = holiday.name
                    holiday_type_by_date[current] = getattr(
                        holiday, "holiday_type", "unclassified"
                    )
                current += timedelta(days=1)
    row_contexts = []
    schedule_keys = set()
    shift_ids = set()
    for (employee_id, attendance_date), row_data in grouped.items():
        row_activities = sorted(row_data["activities"], key=_activity_in_datetime)
        work_activities = [
            activity for activity in row_activities if _activity_type(activity) == "work"
        ]
        break_activities = [
            activity for activity in row_activities if _activity_type(activity) == "break"
        ]
        lunch_activities = [
            activity for activity in row_activities if _activity_type(activity) == "lunch"
        ]
        work_in = work_activities[0] if work_activities else None
        closed_work = [activity for activity in work_activities if activity.clock_out]
        work_out = (
            sorted(closed_work, key=_activity_out_datetime)[-1]
            if closed_work
            else None
        )
        latest_work_activity = (
            sorted(work_activities, key=_activity_in_datetime)[-1]
            if work_activities
            else None
        )
        early_out_work_out = (
            work_out
            if latest_work_activity and latest_work_activity.clock_out
            else None
        )
        # Find the latest unclosed clock-in that is more recent than the last clock-out.
        # This handles the case where an employee clocked in after their last clock-out
        # (e.g., returned for overtime) and forgot to clock out.
        unclosed_work = [a for a in work_activities if not a.clock_out]
        latest_unclosed = None
        if unclosed_work and work_out and work_out.clock_out_date and work_out.clock_out:
            last_out_dt = datetime.combine(work_out.clock_out_date, work_out.clock_out)
            for act in unclosed_work:
                if act.clock_in_date and act.clock_in:
                    act_in_dt = datetime.combine(act.clock_in_date, act.clock_in)
                    if act_in_dt > last_out_dt:
                        if latest_unclosed is None or act_in_dt > datetime.combine(
                            latest_unclosed.clock_in_date, latest_unclosed.clock_in
                        ):
                            latest_unclosed = act
        attendance = attendance_by_key.get((employee_id, attendance_date))
        hours = _attendance_row_hours(attendance)
        shift = _row_shift(attendance, row_data["employee"])
        shift_day = _row_shift_day(
            attendance, row_data["shift_day"], attendance_date
        )
        schedule_key = (
            _row_shift_id(attendance, row_data["employee"]),
            _row_shift_day_id(attendance, row_data["shift_day"]),
            _row_shift_day_name(attendance, row_data["shift_day"], attendance_date),
        )
        if schedule_key[0] and (schedule_key[1] or schedule_key[2]):
            schedule_keys.add(schedule_key)
        if schedule_key[0]:
            shift_ids.add(schedule_key[0])
        row_contexts.append(
            {
                "employee_id": employee_id,
                "attendance_date": attendance_date,
                "row_data": row_data,
                "work_activities": work_activities,
                "break_activities": break_activities,
                "lunch_activities": lunch_activities,
                "work_in": work_in,
                "work_out": work_out,
                "latest_unclosed_work_activity": latest_unclosed,
                "first_work_clock_in": work_in,
                "last_work_clock_out": early_out_work_out,
                "attendance": attendance,
                "hours": hours,
                "shift": shift,
                "shift_day": shift_day,
                "schedule_key": schedule_key,
            }
        )

    schedule_by_key = _employee_shift_schedules(schedule_keys)
    schedule_days_by_shift_id = _shift_schedule_days_by_shift_id(shift_ids)

    rows = []
    for row_context in row_contexts:
        employee_id = row_context["employee_id"]
        attendance_date = row_context["attendance_date"]
        row_data = row_context["row_data"]
        work_activities = row_context["work_activities"]
        break_activities = row_context["break_activities"]
        lunch_activities = row_context["lunch_activities"]
        work_in = row_context["work_in"]
        work_out = row_context["work_out"]
        first_work_clock_in = row_context["first_work_clock_in"]
        last_work_clock_out = row_context["last_work_clock_out"]
        attendance = row_context["attendance"]
        hours = row_context["hours"]
        schedule_key = row_context["schedule_key"]
        exact_schedule = schedule_by_key.get(
            (schedule_key[0], schedule_key[1], None)
        ) or schedule_by_key.get((schedule_key[0], None, schedule_key[2]))
        fallback_schedule = schedule_by_key.get(
            (schedule_key[0], None, "__weekday_fallback__")
        )
        schedule = (
            fallback_schedule
            if _schedule_is_blank_rest_day(exact_schedule) and fallback_schedule
            else exact_schedule or fallback_schedule
        )
        shift_schedule_days = schedule_days_by_shift_id.get(schedule_key[0], [])
        shift_schedule = _shift_schedule_days_label(shift_schedule_days)
        is_rest_day = (
            _schedule_is_explicit_rest_day(exact_schedule)
            or _schedule_is_blank_rest_day(exact_schedule)
            or _shift_day_is_rest_day(shift_schedule_days, schedule_key[2])
        )
        late_early_durations = {
            report_type: _activity_late_early_duration(
                first_work_clock_in,
                last_work_clock_out,
                attendance,
                schedule,
                report_type,
            )
            for report_type in ("late_come", "early_out")
        }
        activity_ids = sorted(set(row_data["activity_ids"]))
        detail_activity = work_in or (
            row_data["activities"][0] if row_data["activities"] else None
        )
        work_segments = [_daily_activity_segment(activity) for activity in work_activities]
        break_segments = [_daily_activity_segment(activity) for activity in break_activities]
        lunch_segments = [_daily_activity_segment(activity) for activity in lunch_activities]
        rows.append(
            SimpleNamespace(
                employee=row_data["employee"],
                attendance_date=attendance_date,
                attendance_date_iso=attendance_date.isoformat()
                if attendance_date
                else "",
                shift_day=row_context["shift_day"],
                attendance=attendance,
                activity_ids=activity_ids,
                activity_ids_json=json.dumps(activity_ids),
                detail_activity_id=getattr(detail_activity, "id", None),
                row_key=f"{employee_id}-{attendance_date.isoformat() if attendance_date else 'unknown'}",
                work_segments=work_segments,
                break_segments=break_segments,
                lunch_segments=lunch_segments,
                work_in=_daily_activity_segment(work_in) if work_in else None,
                work_out=_effective_work_out_segment(work_out, row_context.get("latest_unclosed_work_activity")),
                has_work_images=any(
                    segment.clock_in_selfie or segment.clock_out_selfie
                    for segment in work_segments
                ),
                shift=row_context["shift"],
                shift_schedule=shift_schedule,
                shift_schedule_days=shift_schedule_days,
                has_shift_schedule=bool(exact_schedule),
                schedule=schedule,
                work_type=hours.work_type,
                min_hour=hours.min_hour,
                late_come_duration=late_early_durations["late_come"],
                early_out_duration=late_early_durations["early_out"],
                work_hours=hours.work_hours,
                break_hours=_activity_total_hours(break_activities),
                lunch_hours=_activity_total_hours(lunch_activities),
                pending_hour=hours.pending_hour,
                overtime=calculate_schedule_end_overtime(
                    schedule,
                    work_activities,
                    attendance_date,
                ),
                leave=_leave_type_for_row(
                    leave_by_key, employee_id, attendance_date
                ),
                leave_type=_leave_type_for_row(
                    leave_by_key, employee_id, attendance_date
                ),
                leave_days=_leave_days_for_row(
                    leave_by_key, employee_id, attendance_date
                ),
                is_leave_only=False,
                holiday=holiday_by_date.get(attendance_date),
                holiday_type=holiday_type_by_date.get(attendance_date),
                is_rest_day=is_rest_day,
            )
        )

    # Add leave-only rows for approved leave days with no attendance activity
    today_date = date.today()
    activity_keys = {(row.employee.id, row.attendance_date) for row in rows}
    employee_by_id = {a.employee_id_id: a.employee_id for a in activities}

    for emp_id in employee_ids:
        for date_val in all_check_dates:
            if date_val > today_date:
                continue
            if (emp_id, date_val) in activity_keys:
                continue
            leave_data = leave_by_key.get((emp_id, date_val))
            if not leave_data:
                continue
            employee = employee_by_id.get(emp_id)
            if not employee:
                continue
            leave_type_val, leave_days_val = leave_data
            employee_shift_id = _row_shift_id(None, employee)
            leave_shift_schedule_days = schedule_days_by_shift_id.get(
                employee_shift_id, []
            )
            rows.append(
                SimpleNamespace(
                    employee=employee,
                    attendance_date=date_val,
                    attendance_date_iso=date_val.isoformat(),
                    shift_day=None,
                    attendance=None,
                    activity_ids=[],
                    activity_ids_json="[]",
                    detail_activity_id=None,
                    row_key=f"leave-{emp_id}-{date_val.isoformat()}",
                    work_segments=[],
                    break_segments=[],
                    lunch_segments=[],
                    work_in=None,
                    work_out=None,
                    has_work_images=False,
                    shift=_row_shift(None, employee),
                    shift_schedule=_shift_schedule_days_label(
                        leave_shift_schedule_days
                    ),
                    shift_schedule_days=leave_shift_schedule_days,
                    has_shift_schedule=False,
                    schedule=None,
                    work_type=None,
                    min_hour=None,
                    late_come_duration=None,
                    early_out_duration=None,
                    work_hours=None,
                    break_hours=None,
                    lunch_hours=None,
                    pending_hour=None,
                    overtime=None,
                    leave=leave_type_val,
                    leave_type=leave_type_val,
                    leave_days=leave_days_val,
                    is_leave_only=True,
                    holiday=holiday_by_date.get(date_val),
                    holiday_type=holiday_type_by_date.get(date_val),
                    is_rest_day=False,
                )
            )

    rows.sort(
        key=lambda r: r.attendance_date or date.min,
        reverse=True,
    )
    return rows


def _empty_daily_attendance_row(attendance):
    attendance_date = getattr(attendance, "attendance_date", None)
    employee = getattr(attendance, "employee_id", None)
    fallback_work_in = None
    fallback_work_out = None
    if getattr(attendance, "attendance_clock_in", None):
        fallback_work_in = SimpleNamespace(clock_in=attendance.attendance_clock_in)
    if getattr(attendance, "attendance_clock_out", None):
        fallback_work_out = SimpleNamespace(clock_out=attendance.attendance_clock_out)
    hours = _attendance_row_hours(attendance)
    return SimpleNamespace(
        employee=employee,
        attendance_date=attendance_date,
        attendance_date_iso=attendance_date.isoformat() if attendance_date else "",
        shift_day=getattr(attendance, "attendance_day", None),
        attendance=attendance,
        activity_ids=[],
        activity_ids_json="[]",
        detail_activity_id=None,
        row_key=f"attendance-{attendance.id}",
        work_segments=[],
        break_segments=[],
        lunch_segments=[],
        work_in=fallback_work_in,
        work_out=fallback_work_out,
        has_work_images=False,
        shift=hours.shift,
        shift_schedule="",
        shift_schedule_days=[],
        has_shift_schedule=False,
        work_type=hours.work_type,
        min_hour=hours.min_hour,
        late_come_duration="",
        early_out_duration="",
        work_hours=hours.work_hours,
        break_hours="",
        lunch_hours="",
        pending_hour=hours.pending_hour,
        overtime=hours.overtime,
        leave=None,
        leave_type=None,
        leave_days=None,
        is_leave_only=False,
        holiday=None,
        holiday_type=None,
        is_rest_day=False,
    )


def _employee_avatar_url(employee):
    profile = getattr(employee, "employee_profile", None)
    if profile:
        try:
            return profile.url
        except ValueError:
            pass
    return static("images/ui/default_avatar.jpg")


def _attach_attendance_row_defaults(row):
    employee = getattr(row, "employee", None)
    if employee is not None and not getattr(row, "employee_avatar_url", None):
        row.employee_avatar_url = _employee_avatar_url(employee)
    return row


def build_daily_attendance_rows(attendances):
    """
    Build activity-table shaped rows for Attendance querysets/pages.
    Attendance rows without activity records are still shown with attendance data.
    """
    attendance_rows = list(getattr(attendances, "object_list", attendances or []))
    if not attendance_rows:
        return []

    employee_ids = {
        attendance.employee_id_id
        for attendance in attendance_rows
        if getattr(attendance, "employee_id_id", None)
    }
    attendance_dates = {
        attendance.attendance_date
        for attendance in attendance_rows
        if getattr(attendance, "attendance_date", None)
    }
    activity_rows_by_key = {}
    attendance_by_key = {
        (attendance.employee_id_id, attendance.attendance_date): attendance
        for attendance in attendance_rows
    }
    if employee_ids and attendance_dates:
        activities = AttendanceActivity.objects.filter(
            employee_id_id__in=employee_ids,
            attendance_date__in=attendance_dates,
        ).order_by("clock_in_date", "clock_in", "id")
        for row in build_daily_activity_rows(
            activities,
            attendance_by_key=attendance_by_key,
        ):
            employee_id = getattr(getattr(row, "employee", None), "id", None)
            activity_rows_by_key[(employee_id, row.attendance_date)] = row

    rows = []
    for attendance in attendance_rows:
        key = (attendance.employee_id_id, attendance.attendance_date)
        row = activity_rows_by_key.get(key) or _empty_daily_attendance_row(attendance)
        row.attendance = attendance
        row.row_key = f"attendance-{attendance.id}"
        rows.append(_attach_attendance_row_defaults(row))
    return rows


def _attendance_group_ids(grouped_attendances):
    ids = []
    for entry in getattr(grouped_attendances, "object_list", grouped_attendances or []):
        page = entry.get("list")
        for instance in getattr(page, "object_list", page or []):
            ids.append(instance.id)
    return ids


def _attach_daily_rows_to_grouped_attendances(grouped_attendances):
    for group in getattr(grouped_attendances, "object_list", grouped_attendances or []):
        group["rows"] = build_daily_attendance_rows(group.get("list", []))


def _attendance_tab_queryset(tab, queryset):
    if tab == ATTENDANCE_TAB_OVERTIME:
        return queryset.filter(
            overtime_second__gt=0,
            attendance_validated=True,
        )
    if tab == ATTENDANCE_TAB_VALIDATED:
        return queryset.filter(attendance_validated=True)
    return queryset.filter(attendance_validated=False)


def build_attendance_tab_context(request, active_tab=None, include_filter=False):
    active_tab = attendance_active_tab(active_tab or request.GET.get("tab"))
    config = ATTENDANCE_TAB_CONFIG[active_tab]
    previous_data = attendance_tab_querystring(request, active_tab)
    page_param = config["page_param"]
    field = request.GET.get("field")
    month_name = ""

    condition = AttendanceValidationCondition.objects.first()
    minot = strtime_seconds("00:00")
    if condition is not None and condition.minimum_overtime_to_approve is not None:
        minot = strtime_seconds(condition.minimum_overtime_to_approve)

    queryset = Attendance.objects.filter(employee_id__is_active=True)
    if request.GET.get("sortby"):
        queryset = sortby(request, queryset, "sortby")
    queryset = _attendance_tab_queryset(active_tab, queryset)

    filter_obj = AttendanceFilters(request.GET, queryset=queryset)
    queryset = filtersubordinates(
        request,
        filter_obj.qs,
        "attendance.view_attendance",
    )
    queryset = attendance_preload_queryset(queryset)

    data_dict = parse_qs(previous_data)
    get_key_instances(Attendance, data_dict)
    for key in [
        key
        for key, value in data_dict.items()
        if value == ["unknown"] or key in (config["page_param"], "tab")
    ]:
        data_dict.pop(key)

    is_grouped = bool(field)
    active_rows = []
    if is_grouped:
        active_page = group_by_queryset(
            queryset,
            field,
            request.GET.get(page_param),
            page_param,
        )
        _attach_daily_rows_to_grouped_attendances(active_page)
        active_id_list = _attendance_group_ids(active_page)
        active_ids = json.dumps(active_id_list)
        active_has_results = bool(active_id_list)
        template = "attendance/attendance/group_by.html"
    else:
        active_page = paginator_qry(queryset, request.GET.get(page_param))
        active_rows = build_daily_attendance_rows(active_page)
        active_ids = json.dumps(
            [instance.id for instance in active_page.object_list]
        )
        active_has_results = bool(active_rows)
        template = "attendance/attendance/tab_content.html"

    context = {
        "active_tab": config,
        "active_tab_key": active_tab,
        "active_page": active_page,
        "active_rows": active_rows,
        "active_ids": active_ids,
        "active_has_results": active_has_results,
        "is_grouped": is_grouped,
        "pd": previous_data,
        "field": field,
        "filter_dict": data_dict,
        "month_name": month_name,
        "minot": minot,
        "tab_template": template,
    }
    context[config["context_name"]] = active_page
    context[config["rows_context_name"]] = active_rows
    context[config["ids_context_name"]] = active_ids
    if include_filter:
        context["f"] = filter_obj
        context["gp_fields"] = AttendanceReGroup.fields
    return context


ATTENDANCE_ACTIVITY_DAILY_EXPORT_FIELDS = {
    "daily_clock_in",
    "daily_clock_out",
    "daily_break_in",
    "daily_break_out",
    "daily_lunch_in",
    "daily_lunch_out",
    "daily_clock_in_location",
    "daily_clock_out_location",
    "daily_break_in_location",
    "daily_break_out_location",
    "daily_lunch_in_location",
    "daily_lunch_out_location",
    "daily_clock_image",
    "daily_break_image",
    "daily_lunch_image",
    "daily_shift",
    "daily_shift_start",
    "daily_shift_end",
    "daily_rest_day",
    "daily_shift_day",
    "daily_late_come",
    "daily_early_out",
    "daily_work_hours",
    "daily_basic_hours",
    "daily_break_hours",
    "daily_lunch_hours",
    "daily_overtime",
    "daily_leave",
    "daily_leave_type",
    "daily_leave_days",
    "daily_holiday",
    "daily_holiday_type",
    *ATTENDANCE_PREMIUM_EXPORT_FIELD_NAMES,
}

INTERNAL_REST_DAY_EXPORT_FIELD = "daily_rest_day"
INTERNAL_REST_DAY_EXPORT_HEADER = "__Rest Day"
INTERNAL_HOLIDAY_TYPE_EXPORT_FIELD = "daily_holiday_type"
INTERNAL_HOLIDAY_TYPE_EXPORT_HEADER = "__Holiday Type"
INTERNAL_ZERO_HOURS_EXPORT_FIELDS = {
    "daily_work_hours",
    "daily_basic_hours",
    "daily_overtime",
}
INTERNAL_EXPORT_HEADERS = {
    INTERNAL_REST_DAY_EXPORT_HEADER,
    INTERNAL_HOLIDAY_TYPE_EXPORT_HEADER,
}

ATTENDANCE_FORMULA_DAILY_EXPORT_FIELDS = {
    "daily_late_come",
    "daily_early_out",
    "daily_work_hours",
    "daily_basic_hours",
    "daily_break_hours",
    "daily_lunch_hours",
    "daily_overtime",
    *ATTENDANCE_PREMIUM_EXPORT_FIELD_NAMES,
}


ATTENDANCE_ACTIVITY_EXPORT_VALUE_MAP = {
    "late_come": _("Late Come"),
    "early_out": _("Early Out"),
}


def _attendance_activity_export_formatter(employee):
    time_format = "HH:mm"
    date_format = "MMM. D, YYYY"
    if not getattr(employee, "pk", None):
        return None

    work_info = (
        EmployeeWorkInformation.objects.filter(employee_id=employee)
        .select_related("company_id")
        .first()
    )
    if work_info and work_info.company_id:
        time_format = work_info.company_id.time_format or time_format
        date_format = work_info.company_id.date_format or date_format

    def formatter(value):
        if isinstance(value, time):
            check_time = datetime.strptime(
                str(value).split(".")[0], "%H:%M:%S"
            ).time()
            return check_time.strftime(HORILLA_TIME_FORMATS.get(time_format, "%H:%M"))
        if type(value) == date:
            check_date = datetime.strptime(str(value), "%Y-%m-%d").date()
            return check_date.strftime(
                HORILLA_DATE_FORMATS.get(date_format, "%b. %d, %Y")
            )
        if isinstance(value, datetime):
            return str(value)
        return value

    return formatter


def _attendance_activity_export_image_url(image):
    if not image:
        return ""
    with contextlib.suppress(Exception):
        return image.url
    return str(image)


def _attendance_activity_export_image_pair(segment):
    if not segment:
        return ""

    parts = []
    clock_in_url = _attendance_activity_export_image_url(segment.clock_in_selfie)
    clock_out_url = _attendance_activity_export_image_url(segment.clock_out_selfie)
    if clock_in_url:
        parts.append(f"In {clock_in_url}")
    if clock_out_url:
        parts.append(f"Out {clock_out_url}")
    return " / ".join(parts)


def _attendance_activity_export_images(segments):
    return "; ".join(
        image_pair
        for segment in segments
        if (image_pair := _attendance_activity_export_image_pair(segment))
    )


def _format_attendance_activity_export_value(value, employee, formatter=None):
    if callable(value):
        with contextlib.suppress(TypeError):
            value = value()
    if value is True:
        value = _("Yes")
    elif value is False:
        value = _("No")
    if value in ATTENDANCE_ACTIVITY_EXPORT_VALUE_MAP:
        value = ATTENDANCE_ACTIVITY_EXPORT_VALUE_MAP[value]
    if value is None or value == "None":
        return ""
    value = formatter(value) if formatter else format_export_value(value, employee)
    return "" if value is None else value


def _drop_time_seconds(t):
    """Return a time with seconds/microseconds zeroed so export formats omit them."""
    if isinstance(t, time):
        return t.replace(second=0, microsecond=0)
    return t


def _strip_time_seconds_str(val):
    """Remove :SS from a formatted time string, or format a time object as HH:MM."""
    if isinstance(val, time):
        return val.strftime("%H:%M")
    if not isinstance(val, str):
        return val
    parts = val.split(":")
    if len(parts) < 3:
        return val
    return f"{parts[0]}:{parts[1]}"


def _attendance_activity_export_times(segments, field_name, employee, formatter=None):
    return "; ".join(
        str(_strip_time_seconds_str(formatted_time))
        for segment in segments
        if (
            formatted_time := _format_attendance_activity_export_value(
                _drop_time_seconds(getattr(segment, field_name, None)),
                employee,
                formatter,
            )
        )
    )


def _attendance_activity_export_locations(segments, field_name):
    return "; ".join(
        str(location)
        for segment in segments
        if (location := getattr(segment, field_name, None))
    )


def _holiday_type_export_label(holiday_type):
    if not holiday_type:
        return ""
    labels = dict(Holidays.HOLIDAY_TYPE_CHOICES)
    return str(labels.get(holiday_type, ""))


def _row_is_leave_export_row(row):
    return bool(
        getattr(row, "is_leave_only", False)
        or getattr(row, "leave", None)
        or getattr(row, "leave_type", None)
        or getattr(row, "leave_days", None)
    )


def _attendance_activity_rest_day_export_value(row):
    if _row_is_leave_export_row(row):
        return ""
    return "Yes" if getattr(row, "is_rest_day", False) else ""


def _attendance_activity_daily_export_value(row, field_name, employee, formatter=None):
    if not row:
        return ""

    if (
        field_name in ATTENDANCE_FORMULA_DAILY_EXPORT_FIELDS
        and _row_is_leave_export_row(row)
    ):
        return ""

    if field_name == "daily_clock_in":
        return _strip_time_seconds_str(_format_attendance_activity_export_value(
            _drop_time_seconds(row.work_in.clock_in if row.work_in else None),
            employee,
            formatter,
        ))
    if field_name == "daily_clock_out":
        return _strip_time_seconds_str(_format_attendance_activity_export_value(
            _drop_time_seconds(row.work_out.clock_out if row.work_out else None),
            employee,
            formatter,
        ))
    if field_name == "daily_break_in":
        return _attendance_activity_export_times(
            row.break_segments, "clock_in", employee, formatter
        )
    if field_name == "daily_break_out":
        return _attendance_activity_export_times(
            row.break_segments, "clock_out", employee, formatter
        )
    if field_name == "daily_lunch_in":
        return _attendance_activity_export_times(
            row.lunch_segments, "clock_in", employee, formatter
        )
    if field_name == "daily_lunch_out":
        return _attendance_activity_export_times(
            row.lunch_segments, "clock_out", employee, formatter
        )
    if field_name == "daily_clock_in_location":
        return row.work_in.clock_in_location if row.work_in else ""
    if field_name == "daily_clock_out_location":
        return row.work_out.clock_out_location if row.work_out else ""
    if field_name == "daily_break_in_location":
        return _attendance_activity_export_locations(
            row.break_segments, "clock_in_location"
        )
    if field_name == "daily_break_out_location":
        return _attendance_activity_export_locations(
            row.break_segments, "clock_out_location"
        )
    if field_name == "daily_lunch_in_location":
        return _attendance_activity_export_locations(
            row.lunch_segments, "clock_in_location"
        )
    if field_name == "daily_lunch_out_location":
        return _attendance_activity_export_locations(
            row.lunch_segments, "clock_out_location"
        )
    if field_name == "daily_clock_image":
        parts = []
        if row.work_in:
            clock_in_url = _attendance_activity_export_image_url(
                row.work_in.clock_in_selfie
            )
            if clock_in_url:
                parts.append(f"In {clock_in_url}")
        if row.work_out:
            clock_out_url = _attendance_activity_export_image_url(
                row.work_out.clock_out_selfie
            )
            if clock_out_url:
                parts.append(f"Out {clock_out_url}")
        return " / ".join(parts)
    if field_name == "daily_break_image":
        return _attendance_activity_export_images(row.break_segments)
    if field_name == "daily_lunch_image":
        return _attendance_activity_export_images(row.lunch_segments)
    if field_name == "daily_shift":
        return _format_attendance_activity_export_value(row.shift, employee)
    if field_name == "daily_shift_start":
        schedule = getattr(row, "schedule", None)
        if (
            not getattr(row, "has_shift_schedule", False)
            or not schedule
            or not schedule.start_time
        ):
            return ""
        return _strip_time_seconds_str(_format_attendance_activity_export_value(
            _drop_time_seconds(schedule.start_time), employee, formatter
        ))
    if field_name == "daily_shift_end":
        schedule = getattr(row, "schedule", None)
        if (
            not getattr(row, "has_shift_schedule", False)
            or not schedule
            or not schedule.end_time
        ):
            return ""
        return _strip_time_seconds_str(_format_attendance_activity_export_value(
            _drop_time_seconds(schedule.end_time), employee, formatter
        ))
    if field_name == "daily_rest_day":
        return _attendance_activity_rest_day_export_value(row)
    if field_name == "daily_shift_day":
        shift_day = row.shift_day
        if shift_day is None:
            return ""
        if isinstance(shift_day, str):
            return shift_day.title()
        day_name = getattr(shift_day, "day", None)
        return day_name.title() if day_name else ""
    if field_name == "daily_late_come":
        return _format_attendance_activity_export_value(
            row.late_come_duration, employee, formatter
        )
    if field_name == "daily_early_out":
        value = _format_attendance_activity_export_value(
            row.early_out_duration, employee, formatter
        )
        if not value and getattr(row, "is_leave_only", False):
            return ""
        return value or "00:00"
    if field_name == "daily_work_hours":
        return _format_attendance_activity_export_value(
            row.work_hours, employee, formatter
        )
    if field_name == "daily_basic_hours":
        return ""
    if field_name == "daily_break_hours":
        return _format_attendance_activity_export_value(
            row.break_hours, employee, formatter
        )
    if field_name == "daily_lunch_hours":
        return _format_attendance_activity_export_value(
            row.lunch_hours, employee, formatter
        )
    if field_name == "daily_overtime":
        return _format_attendance_activity_export_value(row.overtime, employee, formatter)
    if field_name == "daily_leave":
        return row.leave or ""
    if field_name == "daily_leave_type":
        return (getattr(row, "leave_type", None) or row.leave) or ""
    if field_name == "daily_leave_days":
        days = getattr(row, "leave_days", None)
        return str(days) if days is not None else ""
    if field_name == "daily_holiday":
        return row.holiday or ""
    if field_name == "daily_holiday_type":
        return _holiday_type_export_label(getattr(row, "holiday_type", None))
    if field_name in ATTENDANCE_PREMIUM_EXPORT_FIELD_NAMES:
        return _premium_bucket_time(
            _attendance_activity_premium_buckets(row).get(field_name, 0)
        )

    return ""


def _attendance_activity_export_row_value(row, field_name, employee, formatter=None):
    if field_name in ATTENDANCE_ACTIVITY_DAILY_EXPORT_FIELDS:
        return _attendance_activity_daily_export_value(
            row, field_name, employee, formatter
        )

    work_info = getattr(row.employee, "employee_work_info", None)
    row_values = {
        "employee_id": row.employee.get_full_name(),
        "employee_number": getattr(row.employee, "employee_no", None) or "",
        "employee_id__employee_work_info__business_unit_id": getattr(
            getattr(work_info, "business_unit_id", None), "code", None
        ),
        "employee_id__employee_work_info__branch_id": getattr(
            work_info, "branch_id", None
        ),
        "employee_id__employee_work_info__department_id": getattr(
            work_info, "department_id", None
        ),
        "employee_id__employee_work_info__payroll_group_id": getattr(
            work_info, "payroll_group_id", None
        ),
        "attendance_date": row.attendance_date,
    }
    value = row_values.get(field_name)
    return _format_attendance_activity_export_value(value, employee, formatter)


ATTENDANCE_ACTIVITY_EXPORT_RELATED_FIELDS = (
    "employee_id",
    "employee_id__employee_work_info",
    "employee_id__employee_work_info__business_unit_id",
    "employee_id__employee_work_info__branch_id",
    "employee_id__employee_work_info__department_id",
    "employee_id__employee_work_info__payroll_group_id",
    "employee_id__employee_work_info__shift_id",
    "employee_id__employee_work_info__work_type_id",
    "shift_day",
)


def _attendance_activity_export_queryset(queryset):
    queryset = queryset.select_related(*ATTENDANCE_ACTIVITY_EXPORT_RELATED_FIELDS)
    if hasattr(queryset, "order_by"):
        queryset = queryset.order_by(
            "employee_id", "attendance_date", "clock_in_date", "clock_in", "id"
        )
    return queryset


def _attendance_activity_export_daily_rows(activities):
    row_keys = {
        (activity.employee_id_id, activity.attendance_date)
        for activity in activities
        if activity.employee_id_id and activity.attendance_date
    }
    if not row_keys:
        return []

    employee_ids = {employee_id for employee_id, _ in row_keys}
    attendance_dates = {attendance_date for _, attendance_date in row_keys}
    daily_activities = _attendance_activity_export_queryset(
        AttendanceActivity.objects.filter(
            employee_id_id__in=employee_ids,
            attendance_date__in=attendance_dates,
        )
    )
    rows = build_daily_activity_rows(daily_activities)
    filtered_rows = [
        row for row in rows
        if (row.employee.id, row.attendance_date) in row_keys
        or getattr(row, "is_leave_only", False)
    ]
    filtered_rows.sort(
        key=lambda r: (
            getattr(r.employee, "employee_no", None) or "",
            r.attendance_date or date.min,
        )
    )
    return filtered_rows


def _payroll_group_empty_daily_row(
    employee, attendance_date, shift_schedule_days=None, schedule=None
):
    work_info = getattr(employee, "employee_work_info", None)
    shift_schedule_days = shift_schedule_days or []
    return SimpleNamespace(
        employee=employee,
        attendance_date=attendance_date,
        attendance_date_iso=attendance_date.isoformat() if attendance_date else "",
        shift_day=attendance_date.strftime("%A").lower() if attendance_date else None,
        attendance=None,
        activity_ids=[],
        activity_ids_json="[]",
        detail_activity_id=None,
        row_key=f"payroll-empty-{employee.id}-{attendance_date.isoformat()}",
        work_segments=[],
        break_segments=[],
        lunch_segments=[],
        work_in=None,
        work_out=None,
        has_work_images=False,
        shift=getattr(work_info, "shift_id", None),
        shift_schedule=_shift_schedule_days_label(shift_schedule_days),
        shift_schedule_days=shift_schedule_days,
        has_shift_schedule=bool(schedule),
        schedule=schedule,
        work_type=getattr(work_info, "work_type_id", None),
        min_hour="00:00",
        late_come_duration="00:00",
        early_out_duration="00:00",
        work_hours="00:00",
        break_hours="00:00",
        lunch_hours="00:00",
        pending_hour="00:00",
        overtime="00:00",
        leave=None,
        leave_type=None,
        leave_days=None,
        is_leave_only=False,
        holiday=None,
        holiday_type=None,
        is_rest_day=(
            _schedule_is_explicit_rest_day(schedule)
            or _schedule_is_blank_rest_day(schedule)
            or _shift_day_is_rest_day(
                shift_schedule_days,
                attendance_date.strftime("%A").lower() if attendance_date else None,
            )
        ),
    )


def _date_range_inclusive(date_from, date_to):
    current = date_from
    while current <= date_to:
        yield current
        current += timedelta(days=1)


def _attendance_activity_payroll_group_daily_rows(
    activities, employees, date_from, date_to
):
    daily_rows = _attendance_activity_export_daily_rows(activities)
    rows_by_key = {
        (getattr(row.employee, "id", None), row.attendance_date): row
        for row in daily_rows
    }
    employee_shift_ids = {
        _row_shift_id(None, employee)
        for employee in employees
        if _row_shift_id(None, employee)
    }
    schedule_days_by_shift_id = _shift_schedule_days_by_shift_id(employee_shift_ids)
    payroll_schedule_keys = {
        (
            _row_shift_id(None, employee),
            None,
            attendance_date.strftime("%A").lower(),
        )
        for employee in employees
        for attendance_date in _date_range_inclusive(date_from, date_to)
        if _row_shift_id(None, employee)
    }
    payroll_schedules_by_key = _employee_shift_schedules(payroll_schedule_keys)
    complete_rows = []
    for employee in employees:
        shift_id = _row_shift_id(None, employee)
        shift_schedule_days = schedule_days_by_shift_id.get(shift_id, [])
        for attendance_date in _date_range_inclusive(date_from, date_to):
            key = (employee.id, attendance_date)
            shift_day_name = attendance_date.strftime("%A").lower()
            exact_schedule = payroll_schedules_by_key.get(
                (shift_id, None, shift_day_name)
            )
            fallback_schedule = payroll_schedules_by_key.get(
                (shift_id, None, "__weekday_fallback__")
            )
            schedule = (
                fallback_schedule
                if _schedule_is_blank_rest_day(exact_schedule) and fallback_schedule
                else exact_schedule
            )
            complete_rows.append(
                rows_by_key.get(key)
                or _payroll_group_empty_daily_row(
                    employee, attendance_date, shift_schedule_days, schedule
                )
            )
    complete_rows.sort(
        key=lambda row: (
            getattr(row.employee, "employee_no", None) or "",
            row.attendance_date or date.min,
        )
    )
    return complete_rows


def _attendance_activity_export_columns(form, selected_fields):
    return [
        (field_name, verbose_name)
        for field_name, verbose_name in form.fields["selected_fields"].choices
        if field_name in selected_fields
    ]


def _attendance_activity_export_data_from_rows(
    daily_rows, selected_columns, employee, progress=None, progress_start=20, progress_end=70
):
    data_export = {verbose_name: [] for _, verbose_name in selected_columns}
    selected_field_names = {field_name for field_name, _verbose_name in selected_columns}
    include_rest_day_helper = (
        INTERNAL_REST_DAY_EXPORT_FIELD not in selected_field_names
    ) and any(
        field_name in ATTENDANCE_PREMIUM_EXPORT_FIELD_NAMES
        or field_name in INTERNAL_ZERO_HOURS_EXPORT_FIELDS
        for field_name, _verbose_name in selected_columns
    )
    include_holiday_type_helper = (
        INTERNAL_HOLIDAY_TYPE_EXPORT_FIELD not in selected_field_names
    ) and any(
        field_name in ATTENDANCE_PREMIUM_EXPORT_FIELD_NAMES
        or field_name in INTERNAL_ZERO_HOURS_EXPORT_FIELDS
        for field_name, _verbose_name in selected_columns
    )
    if include_rest_day_helper:
        data_export[INTERNAL_REST_DAY_EXPORT_HEADER] = []
    if include_holiday_type_helper:
        data_export[INTERNAL_HOLIDAY_TYPE_EXPORT_HEADER] = []
    formatter = _attendance_activity_export_formatter(employee)
    total_rows = len(daily_rows) or 1

    for index, row in enumerate(daily_rows, start=1):
        for field_name, verbose_name in selected_columns:
            data_export[verbose_name].append(
                _attendance_activity_export_row_value(row, field_name, employee, formatter)
            )
        if include_rest_day_helper:
            data_export[INTERNAL_REST_DAY_EXPORT_HEADER].append(
                _attendance_activity_rest_day_export_value(row)
            )
        if include_holiday_type_helper:
            data_export[INTERNAL_HOLIDAY_TYPE_EXPORT_HEADER].append(
                _holiday_type_export_label(getattr(row, "holiday_type", None))
            )
        if progress and (index == total_rows or index % 100 == 0):
            span = progress_end - progress_start
            progress(
                progress_start + int((index / total_rows) * span),
                _("Preparing rows"),
            )

    return data_export


def _attendance_activity_export_data(export_objects, selected_columns, employee, progress=None):
    activities = list(_attendance_activity_export_queryset(export_objects))
    if progress:
        progress(20, _("Building daily rows"))
    daily_rows = _attendance_activity_export_daily_rows(activities)
    return _attendance_activity_export_data_from_rows(
        daily_rows, selected_columns, employee, progress
    )


def _get_payroll_period_dates(start_day, end_day):
    today = date.today()
    day, year, month = today.day, today.year, today.month

    def safe_date(y, m, d):
        if d == 0:
            last_of_prev = date(y, m, 1) - timedelta(days=1)
            return last_of_prev
        return date(y, m, d)

    if day >= start_day and day < end_day:
        date_from = date(year, month, start_day)
        date_to = safe_date(year, month, end_day - 1)
    elif day >= end_day:
        date_from = date(year, month, end_day)
        next_month_first = (date(year, month, 1) + timedelta(days=32)).replace(day=1)
        date_to = safe_date(next_month_first.year, next_month_first.month, start_day - 1)
    else:
        prev_month_first = (date(year, month, 1) - timedelta(days=1)).replace(day=1)
        date_from = date(prev_month_first.year, prev_month_first.month, end_day)
        date_to = safe_date(year, month, start_day - 1)

    return date_from, date_to


def get_current_cut_off_dates(group):
    """Return (date_from, date_to) for the PayrollGroup period that today falls into."""
    today = date.today()

    def resolve_end(y, m, d):
        last = calendar.monthrange(y, m)[1]
        return last if d == 0 else min(d, last)

    def next_month(y, m):
        return (date(y, m, 1) + timedelta(days=32)).replace(day=1)

    def prev_month(y, m):
        return (date(y, m, 1) - timedelta(days=1)).replace(day=1)

    periods = [
        (group.start_day, group.end_day),
        (group.second_cut_off_start, group.second_cut_off_end),
        (group.third_cut_off_start, group.third_cut_off_end),
        (group.fourth_cut_off_start, group.fourth_cut_off_end),
    ]

    for start_d, end_d in periods:
        if not start_d or end_d is None:
            continue
        end_resolved = resolve_end(today.year, today.month, end_d)

        if start_d <= end_resolved:
            # Same-month period
            if start_d <= today.day <= end_resolved:
                return date(today.year, today.month, start_d), date(today.year, today.month, end_resolved)
        else:
            # Cross-month period (e.g. 26 – 10)
            if today.day >= start_d:
                nm = next_month(today.year, today.month)
                return date(today.year, today.month, start_d), date(nm.year, nm.month, resolve_end(nm.year, nm.month, end_d))
            elif today.day <= end_resolved:
                pm = prev_month(today.year, today.month)
                return date(pm.year, pm.month, start_d), date(today.year, today.month, end_resolved)

    return None, None


def _export_request_from_data(user, data, session=None):
    query = QueryDict("", mutable=True)
    for key, value in data.items():
        if isinstance(value, (list, tuple)):
            query.setlist(key, [str(item) for item in value])
        else:
            query[key] = "" if value is None else str(value)
    return SimpleNamespace(GET=query, user=user, META={}, session=session or {})


def _attendance_activity_export_filename():
    today_date = date.today().strftime("%Y-%m-%d")
    return f"Attendance_activity_{today_date}.xlsx"


def _write_plain_export_frame(writer, data_frame, sheet_name):
    data_frame.to_excel(writer, index=False, sheet_name=sheet_name)
    worksheet = writer.sheets[sheet_name]
    centered = writer.book.add_format({"align": "center", "valign": "vcenter"})
    if len(data_frame.columns):
        worksheet.set_column(0, len(data_frame.columns) - 1, 18, centered)
    for col_idx, column_name in enumerate(data_frame.columns):
        if column_name in INTERNAL_EXPORT_HEADERS:
            worksheet.set_column(col_idx, col_idx, 18, centered, {"hidden": True})
    return worksheet


def _write_attendance_activity_export_workbook(request, output, progress=None):
    employee = request.user.employee_get
    form = AttendanceActivityExportForm()
    selected_fields = request.GET.getlist("selected_fields")
    export_objects = AttendanceActivityFilter(request.GET).qs

    if not selected_fields:
        selected_fields = form.fields["selected_fields"].initial
        ids = request.GET.get("ids")
        if ids:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                export_objects = AttendanceActivity.objects.filter(id__in=json.loads(ids))

    payroll_group_ids = request.GET.getlist(
        "employee_id__employee_work_info__payroll_group_id"
    )
    has_date_range = request.GET.get("attendance_date_from") or request.GET.get(
        "attendance_date_till"
    )
    if payroll_group_ids and not has_date_range:
        group = PayrollGroup.objects.filter(id=payroll_group_ids[0]).first()
        if group and group.start_day and group.end_day:
            date_from, date_to = _get_payroll_period_dates(group.start_day, group.end_day)
            export_objects = export_objects.filter(
                attendance_date__gte=date_from,
                attendance_date__lte=date_to,
            )

    selected_columns = _attendance_activity_export_columns(form, selected_fields)
    data_export = _attendance_activity_export_data(
        export_objects, selected_columns, employee, progress
    )
    if progress:
        progress(75, _("Writing workbook"))
    data_frame = pd.DataFrame(data=data_export)
    total_ranges = _build_export_total_ranges(data_frame, selected_columns)
    data_frame, total_ranges = _insert_export_employee_separator_rows(
        data_frame, total_ranges
    )

    writer = pd.ExcelWriter(output, engine="xlsxwriter")
    writer.book.set_calc_mode("auto")
    worksheet = _write_plain_export_frame(writer, data_frame, "Sheet1")
    _write_export_row_formulas(
        writer.book, worksheet, data_frame, selected_columns, total_ranges
    )
    _write_export_totals_sheet(
        writer, "Sheet1", data_frame, selected_columns, total_ranges, "Totals"
    )
    writer.close()
    if progress:
        progress(95, _("Finalizing export"))


def export_attendance_activity_data(request):
    file_name = _attendance_activity_export_filename()
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{file_name}"'
    _write_attendance_activity_export_workbook(request, response)
    return response


EXPORT_ROW_FORMULA_FIELDS = (
    "daily_late_come",
    "daily_early_out",
    "daily_work_hours",
    "daily_basic_hours",
    "daily_break_hours",
    "daily_lunch_hours",
    "daily_overtime",
)

EXPORT_TOTAL_SHEET_COLUMNS = [
    ("EMP No.", "employee_number"),
    ("EMP NAME", "employee_id"),
    ("Total Basic Hours", "daily_basic_hours"),
    ("OT Hours", "daily_overtime"),
    ("Worked on Special Holiday", "daily_worked_special_holiday"),
    ("Worked on Regular Holiday", "daily_worked_regular_holiday"),
    ("OT on Worked on Special Holiday", "daily_ot_worked_special_holiday"),
    ("OT on Worked on Regular Holiday", "daily_ot_worked_regular_holiday"),
    ("Worked on Restday", "daily_worked_rest_day"),
    ("OT on Worked Restday", "daily_ot_worked_rest_day"),
    ("Night Differential Hours", "daily_night_differential"),
    ("Night Differential Hours- OVERTIME", "daily_night_differential_overtime"),
    (
        "Night Differential - Rest Day Overtime",
        "daily_night_differential_rest_day_overtime",
    ),
    ("Night Differential Hours-REST DAY", "daily_night_differential_rest_day"),
    (
        "Night Differential Regular Holiday - Hours",
        "daily_night_differential_regular_holiday",
    ),
    (
        "Night Differential Special Holiday - Hours",
        "daily_night_differential_special_holiday",
    ),
    (
        "Night Differential Hours-SPECIAL HOL. OVERTIME",
        "daily_night_differential_special_holiday_overtime",
    ),
    (
        "Night Differential - Overtime - Legal Hours",
        "daily_night_differential_regular_holiday_overtime",
    ),
    ("Rest Day Hours- Regular Holiday", "daily_rest_day_regular_holiday"),
    (
        "Overtime hours - Regular Holiday - Rest Day",
        "daily_ot_regular_holiday_rest_day",
    ),
    (
        "Night Differential Hours-REST DAY Regular HOL. OVERTIME",
        "daily_night_differential_rest_day_regular_holiday_overtime",
    ),
    (
        "Night Differential Hours-REST DAY Regular Pay.",
        "daily_night_differential_rest_day_regular_holiday",
    ),
    ("Rest Day Hours - Special Holiday", "daily_rest_day_special_holiday"),
    (
        "Night Differential Hours-REST DAY SPECIAL HOL.",
        "daily_night_differential_rest_day_special_holiday",
    ),
    (
        "Night Differential Hours-REST DAY Special HOL. OVERTIME",
        "daily_night_differential_rest_day_special_holiday_overtime",
    ),
    (
        "Overtime hours - Special Holiday - Rest Day",
        "daily_ot_special_holiday_rest_day",
    ),
    ("Late", "daily_late_come"),
    ("Undertime", "daily_early_out"),
]

EXPORT_TOTAL_SHEET_DURATION_FIELDS = {
    field_name
    for _column_name, field_name in EXPORT_TOTAL_SHEET_COLUMNS
    if field_name not in {"employee_number", "employee_id"}
}


def _build_export_total_ranges(df, selected_columns):
    """Record detail-sheet row ranges for each employee without adding subtotal rows."""
    df.columns = [str(c) for c in df.columns]
    total_ranges = []
    if df.empty:
        return total_ranges

    field_columns = {
        field_name: str(verbose_name)
        for field_name, verbose_name in selected_columns
        if str(verbose_name) in df.columns
    }
    emp_no_col = field_columns.get("employee_number")
    emp_name_col = field_columns.get("employee_id")
    emp_col = emp_no_col or emp_name_col

    def range_info(start_df_row, end_df_row):
        first_row = df.iloc[start_df_row] if start_df_row is not None else {}
        return {
            "start_df_row": start_df_row,
            "end_df_row": end_df_row,
            "employee_number": first_row.get(emp_no_col, "") if emp_no_col else "",
            "employee_name": first_row.get(emp_name_col, "") if emp_name_col else "",
        }

    if emp_col is None:
        total_ranges.append(range_info(0, len(df) - 1))
        return total_ranges

    prev_emp = df.iloc[0][emp_col]
    group_start = 0
    for row_idx, emp_val in enumerate(df[emp_col].tolist()):
        if row_idx > 0 and emp_val != prev_emp:
            total_ranges.append(range_info(group_start, row_idx - 1))
            group_start = row_idx
        prev_emp = emp_val
    total_ranges.append(range_info(group_start, len(df) - 1))
    return total_ranges


def _insert_export_employee_separator_rows(df, total_ranges, separator_rows=1):
    if df.empty or separator_rows <= 0 or len(total_ranges) <= 1:
        return df, total_ranges

    separator_after_rows = {
        total_range["end_df_row"]
        for total_range in total_ranges[:-1]
        if total_range.get("end_df_row") is not None
    }
    if not separator_after_rows:
        return df, total_ranges

    blank_row = {column: "" for column in df.columns}
    spaced_rows = []
    for row_idx in range(len(df)):
        spaced_rows.append(df.iloc[row_idx].to_dict())
        if row_idx in separator_after_rows:
            spaced_rows.extend(blank_row.copy() for _ in range(separator_rows))

    spaced_df = pd.DataFrame(spaced_rows, columns=df.columns)
    shifted_ranges = []
    for range_idx, total_range in enumerate(total_ranges):
        row_shift = range_idx * separator_rows
        shifted_range = total_range.copy()
        if shifted_range.get("start_df_row") is not None:
            shifted_range["start_df_row"] += row_shift
        if shifted_range.get("end_df_row") is not None:
            shifted_range["end_df_row"] += row_shift
        shifted_ranges.append(shifted_range)
    return spaced_df, shifted_ranges


def _formula_range_for_df_rows(col_idx, start_df_row, end_df_row):
    if start_df_row is None or end_df_row is None or start_df_row > end_df_row:
        return None
    col_name = xl_col_to_name(col_idx)
    start_excel_row = start_df_row + 2
    end_excel_row = end_df_row + 2
    return f"{col_name}{start_excel_row}:{col_name}{end_excel_row}"


def _formula_cell_for_df_row(col_idx, df_row):
    return f"{xl_col_to_name(col_idx)}{df_row + 2}"


def _quote_excel_sheet_name(sheet_name):
    return f"'{str(sheet_name).replace(chr(39), chr(39) * 2)}'"


def _sheet_formula_range_for_df_rows(sheet_name, col_idx, start_df_row, end_df_row):
    cell_range = _formula_range_for_df_rows(col_idx, start_df_row, end_df_row)
    if not cell_range:
        return None
    return f"{_quote_excel_sheet_name(sheet_name)}!{cell_range}"


def _duration_time_formula(cell_range):
    text_duration = (
        f"(VALUE(LEFT({cell_range},FIND(\":\",{cell_range})-1))*60+"
        f"VALUE(MID({cell_range},FIND(\":\",{cell_range})+1,2)))/1440"
    )
    return f"SUMPRODUCT(IFERROR(N({cell_range}),0)+IFERROR({text_duration},0))"


def _time_value_formula(cell_ref):
    return f"IF(ISNUMBER({cell_ref}),{cell_ref},TIMEVALUE({cell_ref}))"


def _paired_duration_formula(start_ref, end_ref):
    return (
        f'IF(OR({start_ref}="",{end_ref}=""),0,'
        f'SUM(IFERROR(MOD(TIMEVALUE(TRIM(TEXTSPLIT({end_ref},";")))-'
        f'TIMEVALUE(TRIM(TEXTSPLIT({start_ref},";"))),1),0)))'
    )


def _format_duration_formula(duration_formula):
    return (
        f'=IF(MAX(0,{duration_formula})=0,"",'
        f'TEXT(MAX(0,{duration_formula}),"[hh]:mm"))'
    )


def _excel_false():
    return "FALSE"


def _premium_duration_formula(duration_formula, condition_formula):
    return (
        f'=IF({condition_formula},'
        f'IF(MAX(0,{duration_formula})=0,"",'
        f'TEXT(MAX(0,{duration_formula}),"[hh]:mm")),'
        f'""'
        f")"
    )


def _night_overlap_formula(start_expr, end_expr):
    windows = (
        ("TIME(22,0,0)", "1+TIME(6,0,0)"),
        ("1+TIME(22,0,0)", "2+TIME(6,0,0)"),
    )
    parts = [
        f"MAX(0,MIN({end_expr},{window_end})-MAX({start_expr},{window_start}))"
        for window_start, window_end in windows
    ]
    return "+".join(parts)


PREMIUM_ROW_FORMULA_SPECS = {
    "daily_worked_special_holiday": ("regular_duration", "special_only"),
    "daily_ot_worked_special_holiday": ("overtime_duration", "special_only"),
    "daily_worked_regular_holiday": ("regular_duration", "regular_only"),
    "daily_ot_worked_regular_holiday": ("overtime_duration", "regular_only"),
    "daily_worked_rest_day": ("regular_duration", "rest_only"),
    "daily_ot_worked_rest_day": ("overtime_duration", "rest_only"),
    "daily_night_differential": ("regular_nd", "ordinary"),
    "daily_night_differential_overtime": ("overtime_nd", "ordinary"),
    "daily_night_differential_rest_day_overtime": ("overtime_nd", "rest_only"),
    "daily_night_differential_rest_day": ("regular_nd", "rest_only"),
    "daily_night_differential_regular_holiday": ("regular_nd", "regular_only"),
    "daily_night_differential_special_holiday": ("regular_nd", "special_only"),
    "daily_night_differential_special_holiday_overtime": (
        "overtime_nd",
        "special_only",
    ),
    "daily_night_differential_regular_holiday_overtime": (
        "overtime_nd",
        "regular_only",
    ),
    "daily_rest_day_regular_holiday": ("regular_duration", "regular_rest"),
    "daily_ot_regular_holiday_rest_day": ("overtime_duration", "regular_rest"),
    "daily_night_differential_rest_day_regular_holiday_overtime": (
        "overtime_nd",
        "regular_rest",
    ),
    "daily_night_differential_rest_day_regular_holiday": (
        "regular_nd",
        "regular_rest",
    ),
    "daily_rest_day_special_holiday": ("regular_duration", "special_rest"),
    "daily_night_differential_rest_day_special_holiday": (
        "regular_nd",
        "special_rest",
    ),
    "daily_night_differential_rest_day_special_holiday_overtime": (
        "overtime_nd",
        "special_rest",
    ),
    "daily_ot_special_holiday_rest_day": ("overtime_duration", "special_rest"),
}


def _selected_column_indexes(df, selected_columns):
    df_col_indexes = {str(col): idx for idx, col in enumerate(df.columns)}
    indexes = {
        field_name: df_col_indexes.get(str(verbose_name))
        for field_name, verbose_name in selected_columns
    }
    if indexes.get(INTERNAL_REST_DAY_EXPORT_FIELD) is None:
        indexes[INTERNAL_REST_DAY_EXPORT_FIELD] = df_col_indexes.get(
            INTERNAL_REST_DAY_EXPORT_HEADER
        )
    if indexes.get(INTERNAL_HOLIDAY_TYPE_EXPORT_FIELD) is None:
        indexes[INTERNAL_HOLIDAY_TYPE_EXPORT_FIELD] = df_col_indexes.get(
            INTERNAL_HOLIDAY_TYPE_EXPORT_HEADER
        )
    return indexes


def _row_has_leave(df, row_idx, col_indexes):
    for field_name in ("daily_leave", "daily_leave_type", "daily_leave_days"):
        col_idx = col_indexes.get(field_name)
        if col_idx is None:
            continue
        value = df.iat[row_idx, col_idx]
        if value is not None and str(value).strip() and str(value).strip() != "nan":
            return True
    return False


def _row_is_blank_export_separator(df, row_idx):
    return all(
        value is None or str(value).strip() in {"", "nan"}
        for value in df.iloc[row_idx].tolist()
    )


def _write_formula_cell(worksheet, row, col, formula, cell_format, use_array=False):
    if use_array:
        worksheet.write_array_formula(row, col, row, col, formula, cell_format)
    else:
        worksheet.write_formula(row, col, formula, cell_format)


def _write_premium_row_formulas(
    worksheet,
    xlsx_row,
    target,
    ref,
    clock_in,
    clock_out,
    shift_start,
    shift_end,
    break_duration,
    lunch_duration,
    duration_format,
):
    if not any(
        target(field_name) is not None
        for field_name in ATTENDANCE_PREMIUM_EXPORT_FIELD_NAMES
    ):
        return

    zero_formula = '=""'
    rest_day = ref("daily_rest_day")
    required_refs = (clock_in, clock_out, rest_day)
    if not all(required_refs):
        for field_name in ATTENDANCE_PREMIUM_EXPORT_FIELD_NAMES:
            col = target(field_name)
            if col is not None:
                _write_formula_cell(
                    worksheet, xlsx_row, col, zero_formula, duration_format
                )
        return

    shift_times_present = (
        f'AND({shift_start}<>"",{shift_end}<>"")'
        if shift_start and shift_end
        else _excel_false()
    )
    shift_end_adjusted = (
        f"IF({shift_times_present},"
        f"{_time_value_formula(shift_end)}+"
        f"IF({_time_value_formula(shift_end)}<{_time_value_formula(shift_start)},1,0),0)"
        if shift_start and shift_end
        else "0"
    )
    clock_in_adjusted = _time_value_formula(clock_in)
    clock_out_adjusted = (
        f"({_time_value_formula(clock_out)}+"
        f"IF({_time_value_formula(clock_out)}<{_time_value_formula(clock_in)},1,0))"
    )
    source_present = f'AND({clock_in}<>"",{clock_out}<>"")'

    holiday_type = ref("daily_holiday_type")
    regular_holiday = (
        f'ISNUMBER(SEARCH("Regular",{holiday_type}))' if holiday_type else _excel_false()
    )
    special_holiday = (
        f'ISNUMBER(SEARCH("Special",{holiday_type}))' if holiday_type else _excel_false()
    )
    is_rest_day = (
        f'OR(UPPER(TRIM({rest_day}&""))="YES",UPPER(TRIM({rest_day}&""))="TRUE")'
    )

    category_conditions = {
        "ordinary": (
            f"AND(NOT({regular_holiday}),"
            f"NOT({special_holiday}),NOT({is_rest_day}))"
        ),
        "regular_only": f"AND({regular_holiday},NOT({is_rest_day}))",
        "special_only": f"AND({special_holiday},NOT({is_rest_day}))",
        "rest_only": (
            f"AND({is_rest_day},"
            f"NOT({regular_holiday}),NOT({special_holiday}))"
        ),
        "regular_rest": f"AND({regular_holiday},{is_rest_day})",
        "special_rest": f"AND({special_holiday},{is_rest_day})",
    }

    regular_end = f"IF({shift_times_present},MIN({clock_out_adjusted},{shift_end_adjusted}),{clock_out_adjusted})"
    overtime_start = f"IF({shift_times_present},MAX({clock_in_adjusted},{shift_end_adjusted}),{clock_out_adjusted})"
    durations = {
        "regular_duration": (
            f"MAX(0,{regular_end}-{clock_in_adjusted}-{break_duration}-{lunch_duration})"
        ),
        "overtime_duration": (
            f"IF({shift_times_present},MAX(0,{clock_out_adjusted}-{overtime_start}),0)"
        ),
        "regular_nd": _night_overlap_formula(clock_in_adjusted, regular_end),
        "overtime_nd": _night_overlap_formula(overtime_start, clock_out_adjusted),
    }

    for field_name, (duration_key, category_key) in PREMIUM_ROW_FORMULA_SPECS.items():
        col = target(field_name)
        if col is None:
            continue
        condition = f"AND({source_present},{category_conditions[category_key]})"
        formula = _premium_duration_formula(durations[duration_key], condition)
        _write_formula_cell(worksheet, xlsx_row, col, formula, duration_format)


def _write_export_row_formulas(
    workbook, worksheet, df, selected_columns, total_ranges
):
    """Write Excel formulas for per-day duration columns."""
    if df.empty:
        return

    col_indexes = _selected_column_indexes(df, selected_columns)
    total_df_rows = {
        total_range.get("total_df_row")
        for total_range in total_ranges
        if total_range.get("total_df_row") is not None
    }
    duration_format = workbook.add_format({"align": "center", "valign": "vcenter"})

    def ref(field_name, row_idx):
        col_idx = col_indexes.get(field_name)
        if col_idx is None:
            return None
        return _formula_cell_for_df_row(col_idx, row_idx)

    def target(field_name):
        return col_indexes.get(field_name)

    for df_row in range(len(df)):
        if (
            df_row in total_df_rows
            or _row_is_blank_export_separator(df, df_row)
            or _row_has_leave(df, df_row, col_indexes)
        ):
            continue

        xlsx_row = df_row + 1
        clock_in = ref("daily_clock_in", df_row)
        clock_out = ref("daily_clock_out", df_row)
        shift_start = ref("daily_shift_start", df_row)
        shift_end = ref("daily_shift_end", df_row)
        break_in = ref("daily_break_in", df_row)
        break_out = ref("daily_break_out", df_row)
        lunch_in = ref("daily_lunch_in", df_row)
        lunch_out = ref("daily_lunch_out", df_row)
        rest_day = ref("daily_rest_day", df_row)
        holiday_type = ref("daily_holiday_type", df_row)
        rest_day_condition = (
            f'OR(UPPER(TRIM({rest_day}&""))="YES",UPPER(TRIM({rest_day}&""))="TRUE")'
            if rest_day
            else None
        )
        holiday_condition = (
            f'OR(ISNUMBER(SEARCH("SPECIAL",UPPER(TRIM({holiday_type}&"")))),'
            f'ISNUMBER(SEARCH("REGULAR",UPPER(TRIM({holiday_type}&"")))))'
            if holiday_type
            else None
        )
        zero_hour_conditions = [
            condition for condition in (rest_day_condition, holiday_condition) if condition
        ]
        zero_hours_clocked_condition = (
            f'AND({clock_in}<>"",{clock_out}<>"",OR({",".join(zero_hour_conditions)}))'
            if clock_in and clock_out and zero_hour_conditions
            else None
        )

        def zero_hours_formula(formula):
            if not zero_hours_clocked_condition:
                return formula
            return f'=IF({zero_hours_clocked_condition},"00:00",{formula[1:]})'

        shift_end_adjusted = None
        clock_in_adjusted = None
        clock_out_adjusted = None
        if shift_start and shift_end:
            shift_end_adjusted = (
                f"({_time_value_formula(shift_end)}+"
                f"IF({_time_value_formula(shift_end)}<{_time_value_formula(shift_start)},1,0))"
            )
            if clock_in:
                clock_in_adjusted = (
                    f"({_time_value_formula(clock_in)}+"
                    f"IF(AND({_time_value_formula(shift_end)}<{_time_value_formula(shift_start)},"
                    f"{_time_value_formula(clock_in)}<{_time_value_formula(shift_start)}),1,0))"
                )
            if clock_out:
                clock_out_adjusted = (
                    f"({_time_value_formula(clock_out)}+"
                    f"IF(AND({_time_value_formula(shift_end)}<{_time_value_formula(shift_start)},"
                    f"{_time_value_formula(clock_out)}<{_time_value_formula(shift_start)}),1,0))"
                )

        late_col = target("daily_late_come")
        if late_col is not None and clock_in and shift_start and shift_end:
            late_duration = f"MAX(0,{clock_in_adjusted}-{_time_value_formula(shift_start)})"
            formula = (
                f'=IF(OR({clock_in}="",{shift_start}="",{shift_end}=""),"",'
                f'IF({late_duration}=0,"",TEXT({late_duration},"[hh]:mm")))'
            )
            _write_formula_cell(worksheet, xlsx_row, late_col, formula, duration_format)

        early_col = target("daily_early_out")
        if early_col is not None and clock_out and shift_start and shift_end:
            early_duration = f"MAX(0,{shift_end_adjusted}-{clock_out_adjusted})"
            formula = (
                f'=IF(OR({clock_out}="",{shift_start}="",{shift_end}=""),"",'
                f'IF({early_duration}=0,"",TEXT({early_duration},"[hh]:mm")))'
            )
            _write_formula_cell(worksheet, xlsx_row, early_col, formula, duration_format)

        break_duration = "0"
        if break_in and break_out:
            break_duration = _paired_duration_formula(break_in, break_out)
        break_col = target("daily_break_hours")
        if break_col is not None and break_in and break_out:
            formula = _format_duration_formula(break_duration)
            _write_formula_cell(
                worksheet, xlsx_row, break_col, formula, duration_format, True
            )

        lunch_duration = "0"
        if lunch_in and lunch_out:
            lunch_duration = _paired_duration_formula(lunch_in, lunch_out)
        lunch_col = target("daily_lunch_hours")
        if lunch_col is not None and lunch_in and lunch_out:
            formula = _format_duration_formula(lunch_duration)
            _write_formula_cell(
                worksheet, xlsx_row, lunch_col, formula, duration_format, True
            )

        work_col = target("daily_work_hours")
        if work_col is not None and clock_in and clock_out:
            gross_duration = (
                f'IF(OR({clock_in}="",{clock_out}=""),0,'
                f"MOD({_time_value_formula(clock_out)}-{_time_value_formula(clock_in)},1))"
            )
            formula = _format_duration_formula(
                f"{gross_duration}-{break_duration}-{lunch_duration}"
            )
            formula = zero_hours_formula(formula)
            _write_formula_cell(
                worksheet, xlsx_row, work_col, formula, duration_format, True
            )

        basic_col = target("daily_basic_hours")
        if (
            basic_col is not None
            and clock_in
            and clock_out
            and shift_start
            and shift_end
        ):
            basic_duration = (
                f"MAX(0,{shift_end_adjusted}-"
                f"{_time_value_formula(shift_start)}-TIME(1,0,0))"
            )
            formula = (
                f'=IF(OR({clock_in}="",{clock_out}="",'
                f'{shift_start}="",{shift_end}=""),"",'
                f'IF({basic_duration}=0,"",TEXT({basic_duration},"[hh]:mm")))'
            )
            formula = zero_hours_formula(formula)
            _write_formula_cell(
                worksheet, xlsx_row, basic_col, formula, duration_format
            )
        elif basic_col is not None and zero_hours_clocked_condition:
            formula = f'=IF({zero_hours_clocked_condition},"00:00","")'
            _write_formula_cell(
                worksheet, xlsx_row, basic_col, formula, duration_format
            )

        overtime_col = target("daily_overtime")
        if overtime_col is not None and clock_out and shift_start and shift_end:
            overtime_duration = f"MAX(0,{clock_out_adjusted}-{shift_end_adjusted})"
            formula = (
                f'=IF(OR({clock_out}="",{shift_start}="",{shift_end}=""),"",'
                f'IF({overtime_duration}=0,"",TEXT({overtime_duration},"[hh]:mm")))'
            )
            formula = zero_hours_formula(formula)
            _write_formula_cell(
                worksheet, xlsx_row, overtime_col, formula, duration_format
            )
        elif overtime_col is not None and zero_hours_clocked_condition:
            formula = f'=IF({zero_hours_clocked_condition},"00:00","")'
            _write_formula_cell(
                worksheet, xlsx_row, overtime_col, formula, duration_format
            )

        _write_premium_row_formulas(
            worksheet,
            xlsx_row,
            target,
            lambda field_name: ref(field_name, df_row),
            clock_in,
            clock_out,
            shift_start,
            shift_end,
            break_duration,
            lunch_duration,
            duration_format,
        )


def _write_export_totals_sheet(
    writer, detail_sheet_name, df, selected_columns, total_ranges, sheet_name
):
    workbook = writer.book
    worksheet = workbook.add_worksheet(sheet_name)
    writer.sheets[sheet_name] = worksheet
    header_format = workbook.add_format(
        {"align": "center", "valign": "vcenter", "bold": True}
    )
    centered = workbook.add_format({"align": "center", "valign": "vcenter"})
    hour_total_format = workbook.add_format(
        {
            "align": "center",
            "valign": "vcenter",
            "num_format": '[hh]:mm "hr"',
        }
    )

    for col_idx, (column_name, _field_name) in enumerate(EXPORT_TOTAL_SHEET_COLUMNS):
        worksheet.write(0, col_idx, column_name, header_format)
    worksheet.set_column(0, len(EXPORT_TOTAL_SHEET_COLUMNS) - 1, 18, centered)
    worksheet.set_column(1, 1, 28, centered)

    if not total_ranges:
        return worksheet

    df_col_indexes = {str(col): idx for idx, col in enumerate(df.columns)}
    source_col_indexes = {
        field_name: df_col_indexes.get(str(verbose_name))
        for field_name, verbose_name in selected_columns
    }

    for output_row, total_range in enumerate(total_ranges, start=1):
        worksheet.write(output_row, 0, total_range.get("employee_number", ""), centered)
        worksheet.write(output_row, 1, total_range.get("employee_name", ""), centered)
        for output_col, (_column_name, field_name) in enumerate(
            EXPORT_TOTAL_SHEET_COLUMNS[2:], start=2
        ):
            source_col = source_col_indexes.get(field_name)
            if source_col is None or field_name not in EXPORT_TOTAL_SHEET_DURATION_FIELDS:
                worksheet.write_blank(output_row, output_col, None, centered)
                continue

            cell_range = _sheet_formula_range_for_df_rows(
                detail_sheet_name,
                source_col,
                total_range.get("start_df_row"),
                total_range.get("end_df_row"),
            )
            formula = (
                f"={_duration_time_formula(cell_range)}"
                if cell_range
                else "=0"
            )
            worksheet.write_array_formula(
                output_row,
                output_col,
                output_row,
                output_col,
                formula,
                hour_total_format,
            )

    return worksheet


def _payroll_group_export_filename():
    today_date = date.today().strftime("%Y-%m-%d")
    return f"Attendance_by_PayrollGroup_{today_date}.xlsx"


def _payroll_group_export_selected_columns():
    form = AttendanceActivityExportForm()
    excluded_fields = {
        "daily_clock_in_location",
        "daily_clock_out_location",
        "daily_break_in_location",
        "daily_break_out_location",
        "daily_lunch_in_location",
        "daily_lunch_out_location",
        "daily_clock_image",
        "daily_break_image",
        "daily_lunch_image",
        "daily_leave",
    }
    selected_fields = [
        field for field in form.fields["selected_fields"].initial if field not in excluded_fields
    ]
    return _attendance_activity_export_columns(form, selected_fields)


def _payroll_group_export_selections(request):
    try:
        selections = json.loads(request.GET.get("selections", "[]"))
    except (ValueError, TypeError):
        return []
    return selections if isinstance(selections, list) else []


def _payroll_group_accessible_employees(request, group):
    group_employees = Employee.objects.filter(
        employee_work_info__payroll_group_id=group
    ).select_related(
        "employee_work_info",
        "employee_work_info__business_unit_id",
        "employee_work_info__branch_id",
        "employee_work_info__department_id",
        "employee_work_info__payroll_group_id",
        "employee_work_info__shift_id",
        "employee_work_info__work_type_id",
    )
    self_employees = group_employees.filter(employee_user_id=request.user)
    group_employees = filtersubordinatesemployeemodel(
        request, group_employees, "attendance.view_attendanceovertime"
    )
    return (group_employees | self_employees).distinct().order_by("employee_no", "id")


def _payroll_group_export_sheet_name(group_name, used_names):
    base = str(group_name or _("Payroll Group"))[:31]
    n = used_names.get(base, 0) + 1
    used_names[base] = n
    return base if n == 1 else f"{base[:28]} ({n})"


def _write_attendance_payroll_group_export_workbook(request, output, progress=None):
    selections = _payroll_group_export_selections(request)
    if not selections:
        raise ValueError("No groups selected")

    employee = request.user.employee_get
    selected_columns = _payroll_group_export_selected_columns()
    group_ids = [sel.get("id") for sel in selections if sel.get("id")]
    groups_by_id = {
        group.id: group for group in PayrollGroup.objects.filter(id__in=group_ids)
    }

    writer = pd.ExcelWriter(output, engine="xlsxwriter")
    writer.book.set_calc_mode("auto")
    used_names = {}
    sheets_written = 0
    total = len(selections) or 1

    for index, sel in enumerate(selections, start=1):
        group = groups_by_id.get(sel.get("id"))
        if not group or not sel.get("dateFrom") or not sel.get("dateTo"):
            continue
        try:
            date_from = datetime.strptime(sel["dateFrom"], "%Y-%m-%d").date()
            date_to = datetime.strptime(sel["dateTo"], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if date_from > date_to:
            continue

        group_start = 5 + int(((index - 1) / total) * 85)
        group_end = 5 + int((index / total) * 85)
        if progress:
            progress(group_start, _("Loading payroll group"))

        group_employees = _payroll_group_accessible_employees(request, group)
        employee_ids = list(group_employees.values_list("id", flat=True))
        activities = AttendanceActivity.objects.filter(
            employee_id__in=employee_ids,
            attendance_date__gte=date_from,
            attendance_date__lte=date_to,
        ).select_related(
            *ATTENDANCE_ACTIVITY_EXPORT_RELATED_FIELDS
        ).order_by(
            "employee_id", "attendance_date", "clock_in_date", "clock_in", "id"
        )

        if progress:
            progress(group_start + 3, _("Building daily rows"))
        daily_rows = _attendance_activity_payroll_group_daily_rows(
            activities, group_employees, date_from, date_to
        )
        data_export = _attendance_activity_export_data_from_rows(
            daily_rows,
            selected_columns,
            employee,
            progress,
            group_start + 5,
            max(group_start + 6, group_end - 8),
        )
        df = pd.DataFrame(data=data_export)
        emp_no_col = str(_("Employee No."))
        date_col = str(_("Attendance Date"))
        sort_cols = [col for col in [emp_no_col, date_col] if col in df.columns]
        if sort_cols:
            df = df.sort_values(
                by=sort_cols, ascending=[True for _ in sort_cols]
            ).reset_index(drop=True)
        total_ranges = _build_export_total_ranges(df, selected_columns)
        df, total_ranges = _insert_export_employee_separator_rows(df, total_ranges)

        sheet_name = _payroll_group_export_sheet_name(group.name, used_names)
        worksheet = _write_plain_export_frame(writer, df, sheet_name)
        _write_export_row_formulas(
            writer.book, worksheet, df, selected_columns, total_ranges
        )
        totals_sheet_name = _payroll_group_export_sheet_name(
            f"Totals - {sheet_name}", used_names
        )
        _write_export_totals_sheet(
            writer, sheet_name, df, selected_columns, total_ranges, totals_sheet_name
        )
        sheets_written += 1
        if progress:
            progress(group_end, _("Writing workbook"))

    if not sheets_written:
        _write_plain_export_frame(writer, pd.DataFrame({"Message": ["No data"]}), "Sheet1")
    writer.close()
    if progress:
        progress(95, _("Finalizing export"))


@login_required
@permission_required("attendance.change_attendanceactivity")
def export_attendance_by_payroll_group(request):
    """Modal (HTMX) → renders selection form; plain GET → generates XLSX."""
    if request.META.get("HTTP_HX_REQUEST") == "true":
        payroll_groups = PayrollGroup.objects.all()
        groups_json = json.dumps(
            [
                {
                    "id": g.id,
                    "name": g.name,
                    "periods": [
                        {"start": g.start_day, "end": g.end_day},
                        {"start": g.second_cut_off_start, "end": g.second_cut_off_end},
                        {"start": g.third_cut_off_start, "end": g.third_cut_off_end},
                        {"start": g.fourth_cut_off_start, "end": g.fourth_cut_off_end},
                    ],
                }
                for g in payroll_groups
            ]
        )
        return render(
            request,
            "attendance/attendance_activity/payroll_group_export_modal.html",
            {"payroll_groups": payroll_groups, "groups_json": groups_json},
        )

    if not _payroll_group_export_selections(request):
        return HttpResponse("No groups selected", status=400)

    file_name = _payroll_group_export_filename()
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{file_name}"'
    _write_attendance_payroll_group_export_workbook(request, response)
    return response


def _export_post_data(request):
    return {
        key: request.POST.getlist(key)
        if len(request.POST.getlist(key)) > 1
        else request.POST.get(key)
        for key in request.POST
    }


def _start_attendance_export_job(request, filename, writer):
    user_id = request.user.id
    data = _export_post_data(request)
    session = {"selected_company": request.session.get("selected_company")}
    job_id = create_export_job(user_id, filename)

    def worker(output_path, progress):
        user = get_user_model().objects.get(pk=user_id)
        export_request = _export_request_from_data(user, data, session)
        previous_request = getattr(_thread_locals, "request", None)
        _thread_locals.request = export_request
        try:
            writer(export_request, output_path, progress)
        finally:
            _thread_locals.request = previous_request

    start_export_job(job_id, worker)
    return JsonResponse({"job_id": job_id})


@login_required
@permission_required("attendance.change_attendanceactivity")
@require_http_methods(["POST"])
def start_attendance_activity_export(request):
    return _start_attendance_export_job(
        request,
        _attendance_activity_export_filename(),
        _write_attendance_activity_export_workbook,
    )


@login_required
@permission_required("attendance.change_attendanceactivity")
@require_http_methods(["POST"])
def start_attendance_payroll_group_export(request):
    if not request.POST.get("selections"):
        return JsonResponse({"error": "No groups selected"}, status=400)
    return _start_attendance_export_job(
        request,
        _payroll_group_export_filename(),
        _write_attendance_payroll_group_export_workbook,
    )


@login_required
def attendance_export_progress(request, job_id):
    status = read_job_status(job_id)
    if not status or status.get("user_id") != request.user.id:
        return JsonResponse({"error": "Export job not found"}, status=404)
    return JsonResponse(
        {
            "status": status.get("status"),
            "percent": status.get("percent", 0),
            "message": status.get("message", ""),
            "filename": status.get("filename", ""),
            "error": status.get("error", ""),
        }
    )


@login_required
def attendance_export_download(request, job_id):
    status = read_job_status(job_id)
    if not status or status.get("user_id") != request.user.id:
        return HttpResponse("Export job not found", status=404)
    if status.get("status") != "complete":
        return HttpResponse("Export is not ready", status=409)

    path = export_job_file_path(job_id)
    if not path.exists():
        return HttpResponse("Export file not found", status=404)

    return FileResponse(
        path.open("rb"),
        as_attachment=True,
        filename=status.get("filename") or f"{job_id}.xlsx",
    )


def _daily_row_group_value(row, field):
    employee = row.employee
    work_info = getattr(employee, "employee_work_info", None)
    values = {
        "employee_id": employee,
        "attendance_date": row.attendance_date,
        "clock_in_date": row.work_in.clock_in_date if row.work_in else None,
        "clock_out_date": row.work_out.clock_out_date if row.work_out else None,
        "shift_day": row.shift_day,
        "employee_id__country": getattr(employee, "country", None),
        "employee_id__employee_work_info__reporting_manager_id": getattr(
            work_info, "reporting_manager_id", None
        ),
        "employee_id__employee_work_info__shift_id": row.shift,
        "employee_id__employee_work_info__work_type_id": row.work_type,
        "employee_id__employee_work_info__department_id": getattr(
            work_info, "department_id", None
        ),
        "employee_id__employee_work_info__job_position_id": getattr(
            work_info, "job_position_id", None
        ),
        "employee_id__employee_work_info__employee_type_id": getattr(
            work_info, "employee_type_id", None
        ),
        "employee_id__employee_work_info__company_id": getattr(
            work_info, "company_id", None
        ),
        "employee_id__employee_work_info__branch_id": getattr(
            work_info, "branch_id", None
        ),
        "employee_id__employee_work_info__payroll_group_id": getattr(
            work_info, "payroll_group_id", None
        ),
    }
    return values.get(field) or _("Unknown")


def group_daily_activity_rows(rows, field, request, page_name="page"):
    groups_by_value = {}
    for row in rows:
        grouper = _daily_row_group_value(row, field)
        groups_by_value.setdefault(grouper, []).append(row)

    groups = []
    for grouper, group_rows in groups_by_value.items():
        dynamic_name = f"dynamic_page_{page_name}{str(grouper)}".replace(" ", "_")
        groups.append(
            {
                "grouper": grouper,
                "list": paginator_qry(group_rows, request.GET.get(dynamic_name)),
                "dynamic_name": dynamic_name,
            }
        )
    return paginator_qry(groups, request.GET.get(page_name))


def attendance_validate(attendance):
    """
    This method is is used to check condition for at work in AttendanceValidationCondition
    model instance it return true if at work is smaller than condition
    args:
        attendance : attendance object
    """

    conditions = AttendanceValidationCondition.objects.all()
    # Set the default condition for 'at work' to 9:00 AM
    condition_for_at_work = strtime_seconds("09:00")
    if conditions.exists():
        condition_for_at_work = strtime_seconds(conditions[0].validation_at_work)
    at_work = strtime_seconds(attendance.attendance_worked_hour)
    return condition_for_at_work >= at_work


@login_required
@hx_request_required
def profile_attendance_tab(request):
    """
    This function is used to view attendance tab of an employee in profile view.

    Parameters:
    request (HttpRequest): The HTTP request object.
    emp_id (int): The id of the employee.

    Returns: return asset-request-tab template

    """
    user = request.user
    employee = user.employee_get
    employee_attendances = employee.employee_attendances.all()
    attendances_ids = json.dumps([instance.id for instance in employee_attendances])
    context = {
        "attendances": employee_attendances,
        "attendances_ids": attendances_ids,
    }
    return render(request, "tabs/profile-attendance-tab.html", context)


@login_required
@manager_can_enter("employee.view_employee")
def attendance_tab(request, emp_id):
    """
    This function is used to view attendance tab of an employee in individual view.

    Parameters:
    request (HttpRequest): The HTTP request object.
    emp_id (int): The id of the employee.

    Returns: return attendance-tab template
    """

    requests = Attendance.objects.filter(
        is_validate_request=True,
        employee_id=emp_id,
    )
    attendances_ids = json.dumps([instance.id for instance in requests])
    validate_attendances = Attendance.objects.filter(
        attendance_validated=False, employee_id=emp_id
    )
    validate_attendances_ids = json.dumps(
        [instance.id for instance in validate_attendances]
    )
    accounts = AttendanceOverTime.objects.filter(employee_id=emp_id)
    accounts_ids = json.dumps([instance.id for instance in accounts])

    context = {
        "requests": requests,
        "attendances_ids": attendances_ids,
        "accounts": accounts,
        "accounts_ids": accounts_ids,
        "validate_attendances": validate_attendances,
        "validate_attendances_ids": validate_attendances_ids,
    }
    return render(request, "tabs/attendance-tab.html", context=context)


@login_required
@hx_request_required
@manager_can_enter("attendance.add_attendance")
def attendance_create(request):
    """
    This method is used to render attendance create form and save if it is valid
    """
    if request.GET.get("previous_url"):
        data = request.GET.dict()
        employee_list = request.GET.getlist("employee_id")
        data["employee_id"] = employee_list
        form = AttendanceForm(initial=data)
    else:
        form = AttendanceForm()
    form = choosesubordinates(request, form, "attendance.add_attendance")
    if request.method == "POST":
        form = AttendanceForm(request.POST)
        form = choosesubordinates(request, form, "attendance.add_attendance")
        if form.is_valid():
            form.save()
            messages.success(request, _("Attendance added."))
            return HorillaRedirect(request)
    return render(request, "attendance/attendance/form.html", {"form": form})


@login_required
@permission_required("attendance.add_attendance")
def attendance_excel(_request):
    """
    Generate an empty Excel template for attendance data with predefined columns.

    Returns:
        HttpResponse: An HTTP response containing an empty Excel template with predefined columns.
    """
    try:
        columns = [
            "Employee No",
            "Shift",
            "Work type",
            "Attendance date",
            "Check-in date",
            "Check-in",
            "Check-out date",
            "Check-out",
            "Worked hour",
            "Minimum hour",
            "Approved By",
        ]
        sample_row = {
            "Employee No": "EMP-001",
            "Shift": "Day Shift",
            "Work type": "Office",
            "Attendance date": "2026-04-01",
            "Check-in date": "2026-04-01",
            "Check-in": "08:00",
            "Check-out date": "2026-04-01",
            "Check-out": "17:00",
            "Worked hour": "09:00",
            "Minimum hour": "08:00",
            "Approved By": "EMP-002",
        }
        data_frame = pd.DataFrame([sample_row], columns=columns)
        response = HttpResponse(content_type="application/ms-excel")
        response["Content-Disposition"] = 'attachment; filename="my_excel_file.xlsx"'
        data_frame.to_excel(response, index=False)
        return response
    except Exception as exception:
        return HttpResponse(exception)


@login_required
@permission_required("attendance.add_attendance")
def attendance_import(request):
    """
    Save the import of attendance data from an uploaded Excel file, validate the data,
    and return an Excel file with error details if validation fails for anyone
    of the attendance data.

    Parameters:
        request (HttpRequest): The HTTP request object containing the uploaded Excel file.

    Returns:
        HttpResponse or redirect: An HTTP response with an Excel file containing error details
        if validation fails, or a redirect to the attendance view if successful.
    """
    if request.method == "POST":
        file = request.FILES["attendance_import"]
        file_extension = file.name.split(".")[-1].lower()
        data_frame = (
            pd.read_csv(file) if file_extension == "csv" else pd.read_excel(file)
        )
        attendance_dicts = data_frame.to_dict("records")
        attendance_import = process_attendance_data(attendance_dicts)
        path_info = None
        if attendance_import:
            path_info = handle_attendance_errors(attendance_import)

    created_attendance_count = len(attendance_dicts) - len(attendance_import)
    context = {
        "created_count": created_attendance_count,
        "error_count": len(attendance_import),
        "model": _("Attendance"),
        "path_info": path_info,
    }
    html = render_to_string("import_popup.html", context)
    return HttpResponse(html)


@login_required
def attendance_export(request):
    resolver_match = request.resolver_match
    if (
        resolver_match
        and resolver_match.url_name
        and resolver_match.url_name == "attendance-info-export-form"
    ):
        return render(
            request,
            "attendance/attendance/export_filter.html",
            context={
                "export": AttendanceFilters(queryset=Attendance.objects.all()),
                "export_form": AttendanceExportForm(),
            },
        )
    return export_data(
        request=request,
        model=Attendance,
        filter_class=AttendanceFilters,
        form_class=AttendanceExportForm,
        file_name="Attendance_export",
    )


@login_required
@manager_can_enter("attendance.view_attendance")
def attendance_view(request):
    """
    This method is used to view attendances.
    """
    form = AttendanceForm()
    check_attendance = Attendance.objects.all()
    if check_attendance.exists():
        template = "attendance/attendance/attendance_view.html"
    else:
        template = "attendance/attendance/attendance_empty.html"
    context = build_attendance_tab_context(request, include_filter=True)
    context["form"] = form
    return render(
        request,
        template,
        context,
    )


@login_required
@hx_request_required
@manager_can_enter("attendance.change_attendance")
def attendance_update(request, obj_id):
    """
    This method render form to update attendance and save if the form is valid
    args:
        obj_id : attendance id
    """
    attendance = Attendance.objects.get(id=obj_id)
    if request.GET.get("previous_url"):
        form = AttendanceUpdateForm(initial=request.GET.dict())
    else:
        form = AttendanceUpdateForm(
            instance=attendance,
        )
    form = choosesubordinates(request, form, "attendance.change_attendance")
    if request.method == "POST":
        form = AttendanceUpdateForm(request.POST, instance=attendance)
        form = choosesubordinates(request, form, "attendance.change_attendance")
        if form.is_valid():
            form.save()
            messages.success(request, _("Attendance Updated."))
            urlencode = request.GET.urlencode()
            modified_url = f"/attendance/attendance-view/?{urlencode}"
            return HorillaRedirect(request)
    return render(
        request,
        "attendance/attendance/update_form.html",
        {"form": form, "urlencode": request.GET.urlencode(), "obj_id": obj_id},
    )


@login_required
@manager_can_enter("attendance.delete_attendance")
@require_http_methods(["POST"])
def attendance_delete(request, obj_id):
    """
    This method is used to delete attendance.
    args:
        obj_id : attendance id
    """
    try:
        attendance = Attendance.objects.get(id=obj_id)
        month = attendance.attendance_date
        month = month.strftime("%B").lower()
        overtime = attendance.employee_id.employee_overtime.filter(month=month).last()
        if overtime is not None and attendance.attendance_overtime_approve:
            # Subtract overtime of this attendance
            total_overtime = strtime_seconds(overtime.overtime)
            attendance_overtime_seconds = strtime_seconds(attendance.attendance_overtime)
            if total_overtime > attendance_overtime_seconds:
                total_overtime = total_overtime - attendance_overtime_seconds
            else:
                total_overtime = attendance_overtime_seconds - total_overtime
            overtime.overtime = format_time(total_overtime)
            overtime.save()
        try:
            attendance.delete()
            messages.success(request, _("Attendance deleted."))
        except ProtectedError as e:
            messages.error(request, _delete_blocked_message(e.protected_objects))
    except (Attendance.DoesNotExist, OverflowError):
        messages.error(request, _("Attendance Does not exists.."))
    return HorillaRedirect(request)


@login_required
@manager_can_enter("attendance.delete_attendance")
@require_http_methods(["POST"])
def attendance_bulk_delete(request):
    """
    This method is used to delete a bulk of attendances
    """
    success_count = 0
    error_messages = []
    ids = request.POST.getlist("ids", "[]")
    attendances = Attendance.objects.filter(id__in=ids)
    employee_ids = attendances.values_list("employee_id", flat=True)
    overtimes = AttendanceOverTime.objects.filter(
        employee_id__in=employee_ids
    ).in_bulk()

    with transaction.atomic():
        for attendance in attendances:
            try:
                month = attendance.attendance_date.strftime("%B").lower()
                overtime = overtimes.get(attendance.employee_id.id)

                if overtime and attendance.attendance_overtime_approve:
                    # Calculate the new overtime
                    total_overtime = strtime_seconds(overtime.overtime)
                    attendance_overtime_seconds = strtime_seconds(
                        attendance.attendance_overtime
                    )
                    total_overtime = abs(total_overtime - attendance_overtime_seconds)
                    overtime.overtime = format_time(total_overtime)
                    overtime.save()

                attendance.delete()
                success_count += 1

            except ProtectedError as e:
                error_messages.append(_delete_blocked_message(e.protected_objects))

    # Build response messages
    if success_count:
        messages.success(request, f"{success_count} attendances deleted successfully.")
    for error in error_messages:
        messages.error(request, error)
    return redirect("/attendance/attendance-search")


@login_required
def view_my_attendance(request):
    """
    This method is used to view self attendances of employee
    """
    user = request.user
    try:
        employee = user.employee_get
    except:
        return redirect("/employee/employee-profile")
    employee = user.employee_get
    employee_attendances = employee.employee_attendances.all()
    filter = AttendanceFilters()
    if employee_attendances.exists():
        template = "attendance/own_attendance/view_own_attendances.html"
    else:
        template = "attendance/own_attendance/own_empty.html"
    paginated_attendances = paginator_qry(employee_attendances, request.GET.get("page"))
    activity_meta_by_attendance = build_my_attendance_activity_meta(paginated_attendances)
    attendance_rows = build_daily_attendance_rows(paginated_attendances)
    attendances_ids = json.dumps(
        [instance.id for instance in paginated_attendances.object_list]
    )
    return render(
        request,
        template,
        {
            "attendances": paginated_attendances,
            "attendance_rows": attendance_rows,
            "attendances_ids": attendances_ids,
            "activity_meta_by_attendance": activity_meta_by_attendance,
            "f": filter,
            "gp_fields": AttendanceReGroup.fields,
        },
    )


@login_required
@hx_request_required
@manager_can_enter("attendance.add_attendanceovertime")
def attendance_overtime_create(request):
    """
    This method is used to render overtime creating form and save if the form is valid
    """
    form = AttendanceOverTimeForm()
    form = choosesubordinates(request, form, "attendance.add_attendanceovertime")
    if request.method == "POST":
        form = AttendanceOverTimeForm(request.POST)
        form = choosesubordinates(request, form, "attendance.add_attendanceovertime")
        if form.is_valid():
            form.save()
            messages.success(request, _("Attendance account added."))
            return HorillaRedirect(request)
    return render(request, "attendance/attendance_account/form.html", {"form": form})


@login_required
def attendance_overtime_view(request):
    """
    This method is used to view attendance account or overtime account.
    """
    previous_data = request.GET.urlencode()
    filter_obj = AttendanceOverTimeFilter(request.GET)
    if filter_obj.qs.exists():
        template = "attendance/attendance_account/attendance_overtime_view.html"
    else:
        template = "attendance/attendance_account/overtime_empty.html"
    self_account = filter_obj.qs.filter(employee_id__employee_user_id=request.user)
    accounts = filtersubordinates(
        request, filter_obj.qs, "attendance.view_attendanceovertime"
    )
    accounts = accounts | self_account
    accounts = accounts.distinct()
    form = AttendanceOverTimeForm()
    form = choosesubordinates(request, form, "attendance.add_attendanceovertime")
    data_dict = parse_qs(previous_data)
    get_key_instances(AttendanceOverTime, data_dict)
    return render(
        request,
        template,
        {
            "accounts": paginator_qry(accounts, request.GET.get("page")),
            "form": form,
            "pd": previous_data,
            "f": filter_obj,
            "gp_fields": AttendanceOvertimeReGroup.fields,
            "filter_dict": data_dict,
        },
    )


def attendance_account_export(request):
    if request.META.get("HTTP_HX_REQUEST") == "true":
        context = {
            "export_obj": AttendanceOverTimeFilter(),
            "export_fields": AttendanceOverTimeExportForm(),
        }

        return render(
            request,
            "attendance/attendance_account/attendance_account_export_filter.html",
            context=context,
        )
    return export_data(
        request=request,
        model=AttendanceOverTime,
        filter_class=AttendanceOverTimeFilter,
        form_class=AttendanceOverTimeExportForm,
        file_name="Attendance_Account",
    )


@login_required
@manager_can_enter("attendance.change_attendanceovertime")
@hx_request_required
def attendance_overtime_update(request, obj_id):
    """
    This method is used to update attendance overtime and save if the forms is valid
    args:
        obj_id : attendance overtime id
    """
    overtime = AttendanceOverTime.objects.get(id=obj_id)
    form = AttendanceOverTimeForm(instance=overtime)
    form = choosesubordinates(request, form, "attendance.change_attendanceovertime")
    if request.method == "POST":
        form = AttendanceOverTimeForm(request.POST, instance=overtime)
        form = choosesubordinates(request, form, "attendance.change_attendanceovertime")
        if form.is_valid():
            form.save()
            messages.success(request, _("Attendance account updated successfully."))
            return HorillaRedirect(request)
    return render(
        request, "attendance/attendance_account/update_form.html", {"form": form}
    )


@login_required
@permission_required("attendance.delete_attendanceoverTime")
@require_http_methods(["POST"])
def attendance_overtime_delete(request, obj_id):
    """
    This method is used to delete attendance overtime
    args:
        obj_id : attendance overtime id
    """
    previous_data = request.GET.urlencode()
    hx_target = request.META.get("HTTP_HX_TARGET", None)
    try:
        attendance = AttendanceOverTime.objects.get(id=obj_id)
        attendance.delete()
        if hx_target == "ot-table":
            messages.success(request, _("Hour account deleted."))
    except (AttendanceOverTime.DoesNotExist, OverflowError, ValueError):
        if hx_target == "ot-table":
            messages.error(request, _("Hour account not found"))
    except ProtectedError:
        if hx_target == "ot-table":
            messages.error(request, _("You cannot delete this hour account"))
    if hx_target and hx_target == "ot-table":
        hour_account = AttendanceOverTime.objects.all()
        if hour_account.exists():
            return redirect(f"/attendance/attendance-overtime-search?{previous_data}")
        else:
            return HorillaRedirect(request)
    elif hx_target:
        return HttpResponse()


@login_required
@permission_required("attendance.delete_attendanceovertime")
def attendance_account_bulk_delete(request):
    """
    This method is used to bulk delete for Payslip
    """
    ids = request.POST["ids"]
    ids = json.loads(ids)
    for id in ids:
        try:
            hour_account = AttendanceOverTime.objects.get(id=id)
            hour_account.delete()
            messages.success(
                request,
                _("{employee} hour account deleted.").format(
                    employee=hour_account.employee_id
                ),
            )
        except AttendanceOverTime.DoesNotExist:
            messages.error(request, _("Hour account not found."))
        except ProtectedError:
            messages.error(
                request,
                _("You cannot delete {hour_account}").format(hour_account=hour_account),
            )
    return JsonResponse({"message": "Success"})


@login_required
def attendance_activity_view(request):
    """
    This method will render a template to view all attendance activities
    """
    previous_data = request.GET.urlencode()
    filter_obj = AttendanceActivityFilter(request.GET)
    attendance_activities = filter_obj.qs
    self_attendance_activities = attendance_activities.filter(
        employee_id__employee_user_id=request.user
    )
    attendance_activities = filtersubordinates(
        request, filter_obj.qs, "attendance.view_attendanceovertime"
    )
    attendance_activities = attendance_activities | self_attendance_activities
    attendance_activities = attendance_activities.distinct()
    attendance_activities = attendance_activities.order_by("-pk")
    daily_activity_rows = build_daily_activity_rows(attendance_activities)
    activity_ids = json.dumps(
        sorted(
            {
                activity_id
                for row in daily_activity_rows
                for activity_id in row.activity_ids
            }
        )
    )
    if attendance_activities.exists():
        template = "attendance/attendance_activity/attendance_activity_view.html"
    else:
        template = "attendance/attendance_activity/activity_empty.html"
    return render(
        request,
        template,
        {
            "data": paginator_qry(daily_activity_rows, request.GET.get("page")),
            "pd": previous_data,
            "f": filter_obj,
            "gp_fields": AttendanceActivityReGroup.fields,
            "activity_ids": activity_ids,
            "branches": Branch.objects.all(),
            "payroll_groups": PayrollGroup.objects.all(),
        },
    )


@login_required
def activity_daily_single_view(request, employee_id, attendance_date):
    request_copy = request.GET.copy()
    previous_data = request_copy.urlencode()
    try:
        parsed_attendance_date = datetime.strptime(attendance_date, "%Y-%m-%d").date()
    except ValueError:
        return HttpResponseBadRequest(_("Invalid attendance date"))

    activities = AttendanceActivity.objects.filter(
        employee_id_id=employee_id,
        attendance_date=parsed_attendance_date,
    ).order_by("clock_in_date", "clock_in", "id")
    rows = build_daily_activity_rows(activities)
    row = rows[0] if rows else None
    return render(
        request,
        "attendance/attendance_activity/daily_attendance_activity.html",
        {
            "pd": previous_data,
            "row": row,
        },
    )


@login_required
@permission_required("attendance.change_attendanceactivity")
@require_http_methods(["GET", "POST"])
def attendance_activity_update(request, employee_id, attendance_date):
    """
    Render and save one day-level work in/out editor for an activity row.
    """

    request_copy = request.GET.copy()
    previous_data = request_copy.urlencode()
    try:
        parsed_attendance_date = datetime.strptime(attendance_date, "%Y-%m-%d").date()
    except ValueError:
        return HttpResponseBadRequest(_("Invalid attendance date"))

    employee = (
        Employee.objects.filter(id=employee_id)
        .select_related("employee_work_info__shift_id", "employee_work_info__work_type_id")
        .first()
    )
    attendance = (
        Attendance.objects.filter(
            employee_id_id=employee_id,
            attendance_date=parsed_attendance_date,
        )
        .select_related("shift_id", "work_type_id")
        .first()
    )
    work_activities = AttendanceActivity.objects.filter(
        employee_id_id=employee_id,
        attendance_date=parsed_attendance_date,
        activity_type="work",
    )
    first_work_activity = work_activities.order_by("clock_in_date", "clock_in", "id").first()
    clock_out_activity = (
        work_activities.filter(clock_out_date__isnull=False, clock_out__isnull=False)
        .order_by("-clock_out_date", "-clock_out", "-id")
        .first()
    )
    if first_work_activity and not clock_out_activity:
        clock_out_activity = first_work_activity

    work_info = getattr(employee, "employee_work_info", None) if employee else None
    shift = (
        attendance.shift_id
        if attendance and attendance.shift_id
        else getattr(work_info, "shift_id", None)
    )
    work_type = (
        attendance.work_type_id
        if attendance and attendance.work_type_id
        else getattr(work_info, "work_type_id", None)
    )
    attendance_instance = attendance or Attendance(
        employee_id=employee,
        attendance_date=parsed_attendance_date,
        shift_id=shift,
        work_type_id=work_type,
    )
    saved = False
    initial = {
        "employee_id": employee,
        "attendance_date": parsed_attendance_date.isoformat(),
        "shift_id": shift,
        "work_type_id": work_type,
    }
    if first_work_activity:
        initial.update(
            {
                "attendance_clock_in_date": (
                    first_work_activity.clock_in_date.isoformat()
                    if first_work_activity.clock_in_date
                    else None
                ),
                "attendance_clock_in": (
                    first_work_activity.clock_in.strftime("%H:%M")
                    if first_work_activity.clock_in
                    else None
                ),
            }
        )
    if clock_out_activity:
        initial.update(
            {
                "attendance_clock_out_date": (
                    clock_out_activity.clock_out_date.isoformat()
                    if clock_out_activity.clock_out_date
                    else None
                ),
                "attendance_clock_out": (
                    clock_out_activity.clock_out.strftime("%H:%M")
                    if clock_out_activity.clock_out
                    else None
                ),
            }
        )

    if request.method == "POST":
        form = AttendanceActivityUpdateForm(
            request.POST,
            instance=attendance_instance,
            initial=initial,
        )
        if form.is_valid() and first_work_activity:
            clock_in_date = form.cleaned_data["attendance_clock_in_date"]
            clock_in = form.cleaned_data["attendance_clock_in"]
            clock_out_date = form.cleaned_data.get("attendance_clock_out_date")
            clock_out = form.cleaned_data.get("attendance_clock_out")

            with transaction.atomic():
                # Save activities first so Attendance.save() reads updated rows
                # when it calls schedule_end_overtime_calculation internally.
                first_work_activity.clock_in_date = clock_in_date
                first_work_activity.clock_in = clock_in
                first_work_activity.in_datetime = datetime.combine(clock_in_date, clock_in)

                out_target = clock_out_activity or first_work_activity
                # When clock_out_activity and first_work_activity are the same
                # DB row (two separate QuerySet hits with equal ids), collapse
                # to one Python object so all changes land on first_work_activity
                # and are persisted by the single first_work_activity.save() below.
                if out_target is not first_work_activity and out_target.id == first_work_activity.id:
                    out_target = first_work_activity
                if clock_out_date and clock_out:
                    out_target.clock_out_date = clock_out_date
                    out_target.clock_out = clock_out
                    out_target.out_datetime = datetime.combine(clock_out_date, clock_out)
                else:
                    out_target.clock_out_date = None
                    out_target.clock_out = None
                    out_target.out_datetime = None

                first_work_activity.save()
                if out_target.id != first_work_activity.id:
                    out_target.save()

                updated_attendance = form.save(commit=False)
                updated_attendance.employee_id = employee
                updated_attendance.attendance_date = parsed_attendance_date
                updated_attendance.attendance_clock_in_date = clock_in_date
                updated_attendance.attendance_clock_in = clock_in
                updated_attendance.attendance_clock_out_date = clock_out_date
                updated_attendance.attendance_clock_out = clock_out
                updated_attendance.save()

                if updated_attendance.shift_id:
                    recalculate_attendance_for_shift(updated_attendance.shift_id)
            messages.success(request, _("Attendance activity updated."))
            saved = True
        elif form.is_valid():
            messages.error(request, _("Attendance activity Does not exists.."))
    else:
        form = AttendanceActivityUpdateForm(
            instance=attendance_instance,
            initial=initial,
        )

    if not first_work_activity:
        messages.error(request, _("Attendance activity Does not exists.."))

    return render(
        request,
        "attendance/attendance_activity/update_form.html",
        {
            "form": form,
            "employee": employee,
            "employee_id": employee_id,
            "attendance_date": parsed_attendance_date,
            "attendance_date_iso": parsed_attendance_date.isoformat(),
            "shift": shift,
            "work_type": work_type,
            "has_work_activity": bool(first_work_activity),
            "pd": previous_data,
            "saved": saved,
        },
    )


@login_required
def activity_single_view(request, obj_id):
    request_copy = request.GET.copy()
    request_copy.pop("instances_ids", None)
    previous_data = request_copy.urlencode()
    activity = AttendanceActivity.objects.filter(id=obj_id).first()

    instance_ids_json = request.GET["instances_ids"]
    instance_ids = json.loads(instance_ids_json) if instance_ids_json else []
    previous_instance, next_instance = closest_numbers(instance_ids, obj_id)
    context = {
        "pd": previous_data,
        "activity": activity,
        "previous_instance": previous_instance,
        "next_instance": next_instance,
        "instance_ids_json": instance_ids_json,
    }
    if activity:
        attendance = Attendance.objects.filter(
            attendance_date=activity.attendance_date
        ).first()
        context["attendance"] = attendance

    return render(
        request,
        "attendance/attendance_activity/single_attendance_activity.html",
        context=context,
    )


def _cascade_delete_attendance(employee_id_id, attendance_date):
    """
    After deleting activity rows, clean up the related Attendance record.

    If no work activities remain for the employee+date, the Attendance is
    deleted via its custom delete() which cascades to AttendanceLateComeEarlyOut.
    If activities remain, validation/OT flags are reset and the attendance is
    recalculated so it no longer appears in the validated/approved tabs.
    """
    remaining = AttendanceActivity.objects.filter(
        employee_id_id=employee_id_id,
        attendance_date=attendance_date,
        activity_type="work",
    )
    attendance = Attendance.objects.filter(
        employee_id_id=employee_id_id,
        attendance_date=attendance_date,
    ).first()
    if attendance is None:
        return
    if not remaining.exists():
        attendance.delete()
    else:
        Attendance.objects.filter(pk=attendance.pk).update(
            attendance_validated=False,
            attendance_overtime_approve=False,
            approved_overtime_second=0,
        )
        if attendance.shift_id:
            recalculate_attendance_for_shift(attendance.shift_id)


@login_required
@permission_required("attendance.delete_attendanceactivity")
@require_http_methods(["POST", "DELETE"])
def attendance_activity_delete(request, obj_id):
    """
    This method is used to delete attendance activity
    args:
        obj_id : attendance activity id
    """
    request_copy = request.GET.copy()
    request_copy.pop("instances_ids", None)
    previous_data = request_copy.urlencode()
    try:
        with transaction.atomic():
            activity = AttendanceActivity.objects.get(id=obj_id)
            employee_id_id = activity.employee_id_id
            attendance_date = activity.attendance_date
            activity.delete()
            _cascade_delete_attendance(employee_id_id, attendance_date)
        messages.success(request, _("Attendance activity deleted"))
    except AttendanceActivity.DoesNotExist:
        messages.error(request, _("Attendance activity Does not exists.."))
    except ProtectedError:
        messages.error(request, _("You cannot delete this activity"))
    if not request.GET.get("instances_ids"):
        return redirect(f"/attendance/attendance-activity-search?{previous_data}")
    else:
        instances_ids = request.GET.get("instances_ids")
        instances_list = json.loads(instances_ids)
        if obj_id in instances_list:
            instances_list.remove(obj_id)
        previous_instance, next_instance = closest_numbers(
            json.loads(instances_ids), obj_id
        )
        return redirect(
            f"/attendance/attendance-activity-single-view/{next_instance}/?{previous_data}&instances_ids={instances_list}"
        )


@login_required
@permission_required("attendance.delete_attendanceactivity")
@require_http_methods(["POST"])
def attendance_activity_bulk_delete(request):
    """
    Deletes a bulk of AttendanceActivity records based on a list of IDs.
    """
    try:
        ids_json = request.POST.get("ids", "[]")

        try:
            ids = json.loads(ids_json)
        except json.JSONDecodeError:
            messages.error(request, _("Invalid list of IDs provided."))
            return HttpResponse("<script>$('.filterButton')[0].click()</script>")

        try:
            ids = [int(i) for i in ids]
        except (ValueError, TypeError):
            messages.error(request, _("Invalid list of IDs provided."))
            return HttpResponse("<script>$('.filterButton')[0].click()</script>")

        if not ids:
            messages.warning(
                request, _("No attendance activities selected for deletion.")
            )
            return HttpResponse("<script>$('.filterButton')[0].click()</script>")

        # Perform the delete operation in a transaction
        with transaction.atomic():
            activities = AttendanceActivity.objects.filter(id__in=ids)
            affected = list(
                activities.values_list("employee_id_id", "attendance_date").distinct()
            )
            count = activities.count()
            activities.delete()
            for emp_id, att_date in affected:
                _cascade_delete_attendance(emp_id, att_date)

        if count > 0:
            messages.success(
                request,
                _("{count} attendance activities deleted successfully.").format(
                    count=count
                ),
            )
        else:
            messages.info(
                request,
                _("No matching attendance activities were found to delete."),
            )

    except Exception as e:
        logger.exception("Error during bulk delete of attendance activities")
        messages.error(
            request,
            _("Failed to delete attendance activities: {error}").format(error=str(e)),
        )

    return HttpResponse("<script>$('.filterButton')[0].click()</script>")


def process_activity_dicts(activity_dicts):
    from attendance.views.clock_in_out import clock_in, clock_out

    if not activity_dicts:
        return []

    sorted_activity_dicts = sort_activity_dicts(activity_dicts)
    error_dicts = []  # List to store dictionaries with errors

    for activity in sorted_activity_dicts:
        employee_no = activity.get("Employee No")
        if not employee_no:
            activity["Error 1"] = "Please add the Employee No column in the Excel sheet."
            error_dicts.append(activity)
            continue

        employee = Employee.objects.filter(employee_no=employee_no).first()
        if not employee:
            activity["Error 2"] = "Invalid Employee No"
            error_dicts.append(activity)
            continue

        check_in_date = parse_date(activity["In Date"], "Error 4", activity)
        check_out_date = parse_date(activity["Out Date"], "Error 5", activity)
        check_in_time = (
            parse_time(activity["Check In"])
            if not pd.isna(activity["Check In"])
            else None
        )
        check_out_time = (
            parse_time(activity["Check Out"])
            if not pd.isna(activity["Check Out"])
            else None
        )

        if any(key.startswith("Error") for key in activity.keys()):
            error_dicts.append(activity)
            continue

        if check_in_time:
            try:
                clock_in(
                    Request(
                        user=employee.employee_user_id,
                        date=check_in_date,
                        time=check_in_time,
                        datetime=django_timezone.make_aware(
                            datetime.combine(check_in_date, check_in_time)
                        ),
                    )
                )
            except Exception as e:
                activity["Error 6"] = f"Got an error in import clock in {e}"
                error_dicts.append(activity)

        if check_out_time and check_out_date:
            try:
                clock_out(
                    Request(
                        user=employee.employee_user_id,
                        date=check_out_date,
                        time=check_out_time,
                        datetime=django_timezone.make_aware(
                            datetime.combine(check_out_date, check_out_time)
                        ),
                    )
                )
            except Exception as e:
                activity["Error 7"] = f"Got an error in import clock out {e}"
                error_dicts.append(activity)

    return error_dicts


def validate_activity_import_headers(data_frame):
    expected_headers = ACTIVITY_IMPORT_HEADERS
    received_headers = list(data_frame.columns)
    return received_headers == expected_headers, received_headers


def handle_activity_import_error(error_data):

    # Directly create the DataFrame from the list of dictionaries
    data_frame = pd.DataFrame(error_data)

    # Create an HTTP response with an Excel attachment
    response = HttpResponse(content_type="application/ms-excel")
    response["Content-Disposition"] = 'attachment; filename="ImportError.xlsx"'
    data_frame.to_excel(response, index=False)

    def get_activity_error_sheet(request):
        remove_dynamic_url(path_info)
        return response

    from attendance.urls import path, urlpatterns

    # Create a unique path for the error file download
    path_info = f"activity-error-sheet-{uuid.uuid4()}"
    urlpatterns.append(path(path_info, get_activity_error_sheet, name=path_info))
    DYNAMIC_URL_PATTERNS.append(path_info)

    # Return the path information
    path_info = f"attendance/{path_info}"
    return path_info


@login_required
@permission_required("attendance.add_attendanceactivity")
def attendance_activity_import(request):
    if request.method == "POST":
        file = request.FILES["activity_import"]
        data_frame = pd.read_excel(file)
        header_is_valid, received_headers = validate_activity_import_headers(data_frame)
        if not header_is_valid:
            expected_headers_text = ", ".join(ACTIVITY_IMPORT_HEADERS)
            received_headers_text = (
                ", ".join(received_headers) if received_headers else _("No headers found")
            )
            import_error_dicts = [
                {
                    "Header Error": _(
                        "Invalid template headers. Please download and use the latest template with exact column names and order."
                    ),
                    "Expected Headers": expected_headers_text,
                    "Received Headers": received_headers_text,
                }
            ]
            path_info = handle_activity_import_error(import_error_dicts)
            context = {
                "created_count": 0,
                "error_count": len(import_error_dicts),
                "model": _("Attendance Activity"),
                "path_info": path_info,
            }
            html = render_to_string("import_popup.html", context)
            messages.error(
                request,
                _(
                    "Header mismatch in attendance activity import file. Please re-download the template and keep exact header names and order."
                ),
            )
            return HttpResponse(html)

        activity_dicts = data_frame.to_dict("records")
        if activity_dicts:
            import_error_dicts = process_activity_dicts(activity_dicts)
            path_info = handle_activity_import_error(import_error_dicts)
            created_activity_count = len(activity_dicts) - len(import_error_dicts)
            context = {
                "created_count": created_activity_count,
                "error_count": len(import_error_dicts),
                "model": _("Attendance Activity"),
                "path_info": path_info,
            }
            html = render_to_string("import_popup.html", context)
            messages.success(request, _("Attendance activity imported successfully"))
            return HttpResponse(html)
    return render(request, "attendance/attendance_activity/import_activity.html")


@login_required
@permission_required("attendance.add_attendanceactivity")
def attendance_activity_import_excel(request):
    if request.method == "GET":
        data_frame = pd.DataFrame([ACTIVITY_IMPORT_SAMPLE_ROW], columns=ACTIVITY_IMPORT_HEADERS)
        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = 'attachment; filename="activity_excel.xlsx"'
        data_frame.to_excel(response, index=False)
        return response


@login_required
@permission_required("attendance.change_attendanceactivity")
def attendance_activity_export(request):
    if request.META.get("HTTP_HX_REQUEST") == "true":
        export_form = AttendanceActivityExportForm()
        context = {
            "export_form": export_form,
            "export": AttendanceActivityFilter(
                queryset=AttendanceActivity.objects.all()
            ),
        }
        return render(
            request,
            "attendance/attendance_activity/export_filter.html",
            context=context,
        )
    return export_attendance_activity_data(request)


@login_required
def on_time_view(request):
    """
    This method render template to view all on come early out entries
    """
    total_attendances = AttendanceFilters(request.GET).qs
    ids_to_exclude = AttendanceLateComeEarlyOut.objects.filter(
        attendance_id__id__in=[attendance.id for attendance in total_attendances],
        type="late_come",
    ).values_list("attendance_id__id", flat=True)
    # Exclude attendances with related objects in AttendanceLateComeEarlyOut
    total_attendances = total_attendances.exclude(id__in=ids_to_exclude)
    context = {
        "attendances": total_attendances,
    }
    return render(
        request, "attendance/attendance/attendance_on_time.html", context=context
    )


@login_required
@install_required
def late_come_early_out_view(request):
    """
    This method render template to view all late come early out entries
    """
    filter_obj = LateComeEarlyOutFilter(request.GET)
    if filter_obj.qs.exists():
        template = "attendance/late_come_early_out/reports.html"
    else:
        template = "attendance/late_come_early_out/reports_empty.html"
    self_reports = filter_obj.qs.filter(employee_id__employee_user_id=request.user)
    reports = filtersubordinates(
        request, filter_obj.qs, "attendance.view_attendancelatecomeearlyout"
    )

    reports = reports | self_reports
    reports = reports.distinct()
    late_in_early_out_ids = json.dumps(
        [instance.id for instance in paginator_qry(reports, None)]
    )
    previous_data = request.GET.urlencode()
    data_dict = parse_qs(previous_data)
    get_key_instances(AttendanceLateComeEarlyOut, data_dict)
    return render(
        request,
        template,
        {
            "data": paginator_qry(reports, request.GET.get("page")),
            "f": filter_obj,
            "gp_fields": LateComeEarlyOutReGroup.fields,
            "filter_dict": data_dict,
            "late_in_early_out_ids": late_in_early_out_ids,
        },
    )


@login_required
@hx_request_required
def late_in_early_out_single_view(request, obj_id):
    request_copy = request.GET.copy()
    request_copy.pop("instances_ids", None)
    previous_data = request_copy.urlencode()
    late_in_early_out = AttendanceLateComeEarlyOut.objects.filter(id=obj_id).first()
    instance_ids_json = request.GET["instances_ids"]
    instance_ids = json.loads(instance_ids_json) if instance_ids_json else []
    previous_instance, next_instance = closest_numbers(instance_ids, obj_id)
    context = {
        "late_in_early_out": late_in_early_out,
        "previous_instance": previous_instance,
        "next_instance": next_instance,
        "instance_ids_json": instance_ids_json,
        "pd": previous_data,
    }
    return render(
        request, "attendance/late_come_early_out/single_report.html", context=context
    )


@login_required
@permission_required("attendance.delete_attendancelatecomeearlyout")
@hx_request_required
@require_http_methods(["POST"])
def late_come_early_out_delete(request, obj_id):
    """
    This method is used to delete the late come early out instance
    args:
        obj_id : late come early out instance id
    """
    request_copy = request.GET.copy()
    request_copy.pop("instances_ids", None)
    previous_data = request_copy.urlencode()
    try:
        AttendanceLateComeEarlyOut.objects.get(id=obj_id).delete()
        messages.success(request, _("Late-in early-out deleted"))
    except AttendanceLateComeEarlyOut.DoesNotExist:
        messages.error(request, _("Late-in early-out does not exists.."))
    except ProtectedError:
        messages.error(request, _("You cannot delete this Late-in early-out"))
    if not request.GET.get("instances_ids"):
        return redirect(f"/attendance/late-come-early-out-search?{previous_data}")
    else:
        instances_ids = request.GET.get("instances_ids")
        instances_list = json.loads(instances_ids)
        if obj_id in instances_list:
            instances_list.remove(obj_id)
        previous_instance, next_instance = closest_numbers(
            json.loads(instances_ids), obj_id
        )
        return redirect(
            f"/attendance/late-in-early-out-single-view/{next_instance}/?{previous_data}&instances_ids={instances_list}"
        )


@login_required
@permission_required("attendance.delete_attendancelatecomeearlyout")
@require_http_methods(["POST"])
def late_come_early_out_bulk_delete(request):
    """
    This method is used to delete bulk of attendances
    """
    ids = request.POST["ids"]
    ids = json.loads(ids)
    for attendance_id in ids:
        try:
            late_come = AttendanceLateComeEarlyOut.objects.get(id=attendance_id)
            late_come.delete()
            messages.success(
                request,
                _("{employee} Late-in early-out deleted.").format(
                    employee=late_come.employee_id
                ),
            )
        except (AttendanceLateComeEarlyOut.DoesNotExist, OverflowError, ValueError):
            messages.error(request, _("Attendance not found."))
    return JsonResponse({"message": "Success"})


@login_required
@permission_required("attendance.change_attendancelatecomeearlyout")
def late_come_early_out_export(request):
    """
    Export late come early out data to an Excel file.
    This view function takes a GET request and exports attendance late come early out data into an Excel file.
    The exported Excel file will include the selected fields from the AttendanceLateComeEarlyOut model.
    """
    if request.META.get("HTTP_HX_REQUEST") == "true":
        context = {
            "export": LateComeEarlyOutFilter(
                queryset=AttendanceLateComeEarlyOut.objects.all()
            ),
            "export_form": LateComeEarlyOutExportForm(),
        }

        return render(
            request,
            "attendance/late_come_early_out/export_filter.html",
            context=context,
        )
    return export_data(
        request=request,
        model=AttendanceLateComeEarlyOut,
        filter_class=LateComeEarlyOutFilter,
        form_class=LateComeEarlyOutExportForm,
        file_name="Late_come_",
    )


@login_required
@permission_required("attendance.change_attendancevalidationcondition")
@require_http_methods(["POST"])
def validation_condition_delete(request, obj_id):
    """
    This method is used to delete created validation condition
    args:
        obj_id  : validation condition id
    """
    try:
        AttendanceValidationCondition.objects.get(id=obj_id).delete()
        messages.success(request, _("validation condition deleted."))
    except AttendanceValidationCondition.DoesNotExist:
        messages.error(request, _("validation condition Does not exists.."))
    except ProtectedError:
        messages.error(request, _("You cannot delete this validation condition."))
    return redirect("/attendance/validation-condition-view")


@login_required
@require_http_methods(["POST"])
@manager_can_enter("attendance.change_attendance")
def validate_bulk_attendance(request):
    """
    This method is used to validate a bulk of attendances.
    """
    ids = json.loads(request.POST["ids"])
    validate_req_count = 0
    success_messages = []
    error_messages = []

    for obj_id in ids:
        try:
            attendance = Attendance.objects.get(id=obj_id)

            if attendance.is_validate_request:
                error_messages.append(
                    _(
                        "Pending attendance update request for {}'s attendance on {}!"
                    ).format(attendance.employee_id, attendance.attendance_date)
                )
                continue

            attendance.attendance_validated = True
            attendance.save()
            validate_req_count += 1

            # Send notification
            notify.send(
                request.user.employee_get,
                recipient=attendance.employee_id.employee_user_id,
                verb=f"Your attendance for the date {attendance.attendance_date} is validated",
                verb_ar=f"تم التحقق من حضورك في تاريخ {attendance.attendance_date}",
                verb_de=f"Ihre Anwesenheit für das Datum {attendance.attendance_date} wurde bestätigt",
                verb_es=f"Se ha validado su asistencia para la fecha {attendance.attendance_date}",
                verb_fr=f"Votre présence pour la date {attendance.attendance_date} est validée",
                redirect=reverse("view-my-attendance") + f"?id={attendance.id}",
                icon="checkmark",
            )

        except Attendance.DoesNotExist:
            error_messages.append(_("Attendance not found"))
        except (OverflowError, ValueError):
            error_messages.append(_("Invalid attendance ID"))

    # Handle messages
    if validate_req_count > 0:
        messages.success(
            request, _("{} Attendances validated.").format(validate_req_count)
        )
    for msg in success_messages + error_messages:
        if "Pending" in msg:
            messages.info(request, msg)
        else:
            messages.error(request, msg)

    return JsonResponse({"message": "success"})


@login_required
@manager_can_enter("attendance.change_attendance")
def validate_this_attendance(request, obj_id):
    """
    This method is used to validate attendance
    args:
        id  : attendance id
    """
    try:
        attendance = Attendance.objects.get(id=obj_id)
        attendance.attendance_validated = True
        attendance.save()
        urlencode = request.GET.urlencode()
        modified_url = f"/attendance/attendance-view/?{urlencode}"
        messages.success(
            request,
            (
                f"{attendance.employee_id} {attendance.attendance_date.strftime('%d %b %Y') }"
                + " "
                + _("Attendance validated.")
            ),
        )
        notify.send(
            request.user.employee_get,
            recipient=attendance.employee_id.employee_user_id,
            verb=f"Your attendance for the date {attendance.attendance_date} is validated",
            verb_ar=f"تم تحقيق حضورك في تاريخ {attendance.attendance_date}",
            verb_de=f"Deine Anwesenheit für das Datum {attendance.attendance_date} ist bestätigt.",
            verb_es=f"Se valida tu asistencia para la fecha {attendance.attendance_date}.",
            verb_fr=f"Votre présence pour la date {attendance.attendance_date} est validée.",
            redirect=reverse("view-my-attendance") + f"?id={attendance.id}",
            icon="checkmark",
        )
    except (Attendance.DoesNotExist, ValueError):
        messages.error(request, _("Attendance not found"))

    return HorillaRedirect(request)


@login_required
def revalidate_this_attendance(request, obj_id):
    """
    This method is used to not validate the attendance.
    args:
        id  : attendance id
    """

    attendance = Attendance.objects.get(id=obj_id)
    if is_reportingmanger(request, attendance) or request.user.has_perm(
        "attendance.change_attendance"
    ):
        attendance.attendance_validated = False
        attendance.save()
        with contextlib.suppress(Exception):
            notify.send(
                request.user.employee_get,
                recipient=(
                    attendance.employee_id.employee_work_info.reporting_manager_id.employee_user_id
                ),
                verb=f"{attendance.employee_id} requested revalidation for \
                    {attendance.attendance_date} attendance",
                verb_ar=f"{attendance.employee_id} طلب إعادة\
                      التحقق من حضور تاريخ {attendance.attendance_date}",
                verb_de=f"{attendance.employee_id} beantragte eine Neubewertung der \
                    Teilnahme am {attendance.attendance_date}",
                verb_es=f"{attendance.employee_id} solicitó la validación nuevamente \
                    para la asistencia del {attendance.attendance_date}",
                verb_fr=f"{attendance.employee_id} a demandé une revalidation pour la \
                    présence du {attendance.attendance_date}",
                redirect=reverse("view-my-attendance") + f"?id={attendance.id}",
                icon="refresh",
            )
        return HorillaRedirect(request)
    return HttpResponse("You Cannot Request for others attendance")


@login_required
@manager_can_enter("attendance.change_attendance")
def approve_overtime(request, obj_id):
    """
    This method is used to approve attendance overtime
    args:
        obj_id  : attendance id
    """
    try:
        attendance = Attendance.objects.get(id=obj_id)
        attendance.attendance_overtime_approve = True
        attendance.save()
        urlencode = request.GET.urlencode()
        modified_url = f"/attendance/attendance-view/?{urlencode}"
        messages.success(
            request,
            f"{attendance.employee_id}'s {attendance.attendance_date.strftime('%d %b %Y')} overtime approved",
        )
        with contextlib.suppress(Exception):
            notify.send(
                request.user.employee_get,
                recipient=attendance.employee_id.employee_user_id,
                verb=f"Your {attendance.attendance_date}'s attendance \
                    overtime approved.",
                verb_ar=f"تمت الموافقة على إضافة ساعات العمل الإضافية لتاريخ \
                    {attendance.attendance_date}.",
                verb_de=f"Die Überstunden für den {attendance.attendance_date}\
                      wurden genehmigt.",
                verb_es=f"Se ha aprobado el tiempo extra de asistencia para el \
                    {attendance.attendance_date}.",
                verb_fr=f"Les heures supplémentaires pour la date\
                      {attendance.attendance_date} ont été approuvées.",
                redirect=reverse("attendance-overtime-view") + f"?id={attendance.id}",
                icon="checkmark",
            )
    except (Attendance.DoesNotExist, OverflowError):
        messages.error(request, _("Attendance not found"))
    return HorillaRedirect(request)


@login_required
@manager_can_enter("attendance.change_attendance")
def approve_bulk_overtime(request):
    """
    This method is used to approve bulk of attendance
    """
    ids = request.POST["ids"]
    ids = json.loads(ids)
    for attendance_id in ids:
        try:
            attendance = Attendance.objects.get(id=attendance_id)
            attendance.attendance_overtime_approve = True
            attendance.save()
            messages.success(request, _("Overtime approved"))
            notify.send(
                request.user.employee_get,
                recipient=attendance.employee_id.employee_user_id,
                verb=f"Overtime approved for\
                      {attendance.attendance_date}'s attendance",
                verb_ar=f"تمت الموافقة على العمل الإضافي لحضور تاريخ \
                    {attendance.attendance_date}",
                verb_de=f"Überstunden für die Anwesenheit am \
                    {attendance.attendance_date} genehmigt",
                verb_es=f"Horas extra aprobadas para la asistencia del \
                    {attendance.attendance_date}",
                verb_fr=f"Heures supplémentaires approuvées pour la présence du \
                    {attendance.attendance_date}",
                redirect=reverse("attendance-overtime-view") + f"?id={attendance.id}",
                icon="checkmark",
            )
        except (Attendance.DoesNotExist, OverflowError, ValueError):
            messages.error(request, _("Attendance not found"))
    return JsonResponse({"message": "Success"})


@login_required
# @manager_can_enter("attendance.change_attendance")
def attendance_add_to_batch(request):
    """
    This method is used to add attendance to a batch
    """
    batches = BatchAttendance.objects.all()
    ids = request.GET.getlist("ids")
    if request.method == "POST":
        ids = request.GET["ids"]
        # Remove brackets and quotes, then split and convert to integers
        int_ids = [int(x.strip().strip("'")) for x in ids.strip("[]").split(",")]
        batch_id = request.POST.get("batch_attendance_id")
        if batch_id:
            batch = BatchAttendance.objects.filter(id=batch_id).first()
            for id in int_ids:
                try:
                    attendance_req = Attendance.objects.filter(id=id).first()
                    attendance_req.batch_attendance_id = batch
                    attendance_req.save()
                except Exception as e:
                    logger.error(e)
                    messages.error(request, _("Something went wrong."))
                    return HorillaRedirect(request)
            messages.success(request, _(f"Attendances added to {batch}."))
            return HorillaRedirect(request)
        else:
            messages.error(request, _("Something went wrong."))
            return HorillaRedirect(request)
    return render(
        request,
        "attendance/attendance/attendance_add_batch.html",
        {"batches": batches, "ids": ids},
    )


@login_required
@hx_request_required
def update_fields_based_shift(request):
    shift_id = request.GET.get("shift_id")
    hx_target = request.META.get("HTTP_HX_TARGET")

    employee_ids = (
        request.GET.get("employee_id")
        if hx_target == "attendanceUpdateForm" or hx_target == "attendanceRequestDiv"
        else request.GET.getlist("employee_id")
    )
    employee_queryset = (
        (
            Employee.objects.get(id=employee_ids)
            if hx_target == "attendanceUpdateForm"
            or hx_target == "attendanceRequestDiv"
            else Employee.objects.filter(id__in=employee_ids)
        )
        if employee_ids
        else None
    )
    attendance_date_str = request.GET.get("attendance_date")

    attendance_date = (
        datetime.strptime(attendance_date_str, "%Y-%m-%d").date()
        if attendance_date_str
        else datetime.today().date()
    )
    day = EmployeeShiftDay.objects.filter(
        day=attendance_date.strftime("%A").lower()
    ).first()
    schedule_today = shift_schedule_with_weekday_fallback(day, shift_id)

    shift_start_time = schedule_today.start_time if schedule_today else ""
    shift_end_time = schedule_today.end_time if schedule_today else ""
    minimum_hour = schedule_today.minimum_working_hour if schedule_today else "00:00"

    if schedule_today and shift_end_time < shift_start_time:
        attendance_clock_out_date = (attendance_date + timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )
    else:
        attendance_clock_out_date = attendance_date.strftime("%Y-%m-%d")

    if attendance_date == datetime.today().date():
        shift_end_time = datetime.now().time()
        worked_hour = "00:00"
    else:
        worked_hour = minimum_hour

    minimum_hour = attendance_day_checking(str(attendance_date), minimum_hour)

    initial_data = {
        "work_type_id": WorkType.find(request.GET.get("work_type_id")),
        "shift_id": shift_id,
        "employee_id": employee_queryset,
        "minimum_hour": minimum_hour,
        "attendance_date": attendance_date.strftime("%Y-%m-%d"),
        "attendance_clock_in": (
            shift_start_time.strftime("%H:%M") if shift_start_time else ""
        ),
        "attendance_clock_out": (
            shift_end_time.strftime("%H:%M") if shift_end_time else ""
        ),
        "attendance_worked_hour": worked_hour,
        "attendance_clock_in_date": attendance_date.strftime("%Y-%m-%d"),
        "attendance_clock_out_date": attendance_clock_out_date,
    }
    form = (
        AttendanceUpdateForm(initial=initial_data)
        if hx_target == "attendanceUpdateForm"
        else (
            NewRequestForm(initial=initial_data)
            if hx_target == "attendanceRequestDiv"
            else AttendanceForm(initial=initial_data)
        )
    )
    return render(
        request,
        "attendance/attendance/update_hx_form.html",
        {"request": request, "form": form},
    )


@login_required
@hx_request_required
def update_worked_hour_field(request):
    """
    Update the worked hour field based on clock-in and clock-out times.

    This view function calculates the total worked hours for an employee
    by parsing the clock-in and clock-out dates and times from the request
    parameters. It computes the duration between the two times and formats
    the result as a string in the "HH:MM" format. The computed worked hours
    are then initialized in an AttendanceForm, which is rendered in the
    specified HTML template.
    """
    clock_in = parse_datetime(
        (
            now().strftime("%Y-%m-%d")
            if request.GET.get("create_bulk")
            else request.GET.get("attendance_clock_in_date")
        ),
        request.GET.get("attendance_clock_in"),
    )
    clock_out = parse_datetime(
        (
            now().strftime("%Y-%m-%d")
            if request.GET.get("create_bulk")
            else request.GET.get("attendance_clock_out_date")
        ),
        request.GET.get("attendance_clock_out"),
    )

    total_seconds = (
        (clock_out - clock_in).total_seconds() if clock_in and clock_out else -1
    )
    hours, minutes = divmod(max(total_seconds, 0), 3600)
    worked_hours_str = f"{int(hours):02}:{int(minutes // 60):02}"

    form = AttendanceForm(initial={"attendance_worked_hour": worked_hours_str})
    return render(
        request,
        "attendance/attendance/update_hx_form.html",
        {"request": request, "form": form},
    )


@login_required
def form_date_checking(request):
    attendance_date_str = request.POST["attendance_date"]
    minimum_hour = "00:00"
    # Converting to date type.
    attendance_date = datetime.strptime(attendance_date_str, "%Y-%m-%d").date()

    if request.POST["shift_id"]:
        shift_id = request.POST["shift_id"]
        day = EmployeeShiftDay.objects.filter(
            day=attendance_date.strftime("%A").lower()
        ).first()
        schedule_today = shift_schedule_with_weekday_fallback(day, shift_id)

        # Checking the Shift is present in the selected attendance day.
        if schedule_today is not None:
            minimum_hour = schedule_today.minimum_working_hour

    attendance_date = str(attendance_date)
    minimum_hour = attendance_day_checking(attendance_date, minimum_hour)

    return JsonResponse(
        {
            "minimum_hour": minimum_hour,
        }
    )


@login_required
def user_request_one_view(request, id):
    """
    function used to view one user attendance request.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return one user attendance request view template
    """
    attendance_request = Attendance.objects.get(id=id)

    at_work_seconds = attendance_request.at_work_second
    hours_at_work = at_work_seconds // 3600
    minutes_at_work = (at_work_seconds % 3600) // 60
    at_work = "{:02}:{:02}".format(hours_at_work, minutes_at_work)

    over_time_seconds = attendance_request.overtime_second
    hours_over_time = over_time_seconds // 3600
    minutes_over_time = (over_time_seconds % 3600) // 60
    over_time = "{:02}:{:02}".format(hours_over_time, minutes_over_time)
    instance_ids_json = request.GET["instances_ids"]
    instance_ids = json.loads(instance_ids_json) if instance_ids_json else []
    previous_instance, next_instance = closest_numbers(instance_ids, id)
    return render(
        request,
        "attendance/attendance/attendance_request_one.html",
        {
            "attendance_request": attendance_request,
            "at_work": at_work,
            "over_time": over_time,
            "previous_instance": previous_instance,
            "next_instance": next_instance,
            "instance_ids_json": instance_ids_json,
            "dashboard": request.GET.get("dashboard"),
        },
    )


@login_required
@hx_request_required
def get_attendance_activities(request, obj_id):
    attendance = Attendance.find(obj_id)
    return render(
        request,
        "attendance/attendance/attendance_activites_view.html",
        context={"attendance": attendance},
    )


@login_required
def hour_attendance_select(request):
    page_number = request.GET.get("page")
    context = {}

    if page_number == "all":
        if request.user.has_perm("attendance.view_attendanceovertime"):
            employees = AttendanceOverTime.objects.all()
        else:
            employees = AttendanceOverTime.objects.filter(
                employee_id__employee_user_id=request.user
            ) | AttendanceOverTime.objects.filter(
                employee_id__employee_work_info__reporting_manager_id__employee_user_id=request.user
            )

        employee_ids = [str(emp.id) for emp in employees]
        total_count = employees.count()

        context = {"employee_ids": employee_ids, "total_count": total_count}

    return JsonResponse(context, safe=False)


@login_required
def hour_attendance_select_filter(request):
    page_number = request.GET.get("page")
    filtered = request.GET.get("filter")
    filters = json.loads(filtered) if filtered else {}

    if page_number == "all":
        if request.user.has_perm("attendance.view_attendanceovertime"):
            employee_filter = AttendanceOverTimeFilter(
                filters, queryset=AttendanceOverTime.objects.all()
            )
        else:
            employee_filter = AttendanceOverTimeFilter(
                filters,
                queryset=AttendanceOverTime.objects.filter(
                    employee_id__employee_user_id=request.user
                )
                | AttendanceOverTime.objects.filter(
                    employee_id__employee_work_info__reporting_manager_id__employee_user_id=request.user
                ),
            )

        # Get the filtered queryset
        filtered_employees = employee_filter.qs

        employee_ids = [str(emp.id) for emp in filtered_employees]
        total_count = filtered_employees.count()

        context = {"employee_ids": employee_ids, "total_count": total_count}

        return JsonResponse(context)


@login_required
def activity_attendance_select(request):
    page_number = request.GET.get("page")

    if page_number == "all":
        if request.user.has_perm("attendance.view_attendanceovertime"):
            employees = AttendanceActivity.objects.all()
        else:
            employees = AttendanceActivity.objects.filter(
                employee_id__employee_user_id=request.user
            ) | AttendanceActivity.objects.filter(
                employee_id__employee_work_info__reporting_manager_id__employee_user_id=request.user
            )

    employee_ids = [str(emp.id) for emp in employees]
    total_count = employees.count()

    context = {"employee_ids": employee_ids, "total_count": total_count}

    return JsonResponse(context, safe=False)


@login_required
def activity_attendance_select_filter(request):
    page_number = request.GET.get("page")
    filtered = request.GET.get("filter")
    filters = json.loads(filtered) if filtered else {}

    if page_number == "all":
        if request.user.has_perm("attendance.view_attendanceovertime"):
            employee_filter = AttendanceActivityFilter(
                filters, queryset=AttendanceActivity.objects.all()
            )
        else:
            employee_filter = AttendanceActivityFilter(
                filters,
                queryset=AttendanceActivity.objects.filter(
                    employee_id__employee_user_id=request.user
                )
                | AttendanceActivity.objects.filter(
                    employee_id__employee_work_info__reporting_manager_id__employee_user_id=request.user
                ),
            )

        # Get the filtered queryset
        filtered_employees = employee_filter.qs

        employee_ids = [str(emp.id) for emp in filtered_employees]
        total_count = filtered_employees.count()

        context = {"employee_ids": employee_ids, "total_count": total_count}

        return JsonResponse(context)


@login_required
def latecome_attendance_select(request):
    page_number = request.GET.get("page")

    if page_number == "all":
        if request.user.has_perm("attendance.view_attendancelatecomeearlyout"):
            employees = AttendanceLateComeEarlyOut.objects.all()
        else:
            employees = AttendanceLateComeEarlyOut.objects.filter(
                employee_id__employee_user_id=request.user
            ) | AttendanceLateComeEarlyOut.objects.filter(
                employee_id__employee_work_info__reporting_manager_id__employee_user_id=request.user
            )

    employee_ids = [str(emp.id) for emp in employees]
    total_count = employees.count()

    context = {"employee_ids": employee_ids, "total_count": total_count}

    return JsonResponse(context, safe=False)


@login_required
def latecome_attendance_select_filter(request):
    page_number = request.GET.get("page")
    filtered = request.GET.get("filter")
    filters = json.loads(filtered) if filtered else {}

    if page_number == "all":
        if request.user.has_perm("attendance.view_attendancelatecomeearlyout"):
            employee_filter = LateComeEarlyOutFilter(
                filters, queryset=AttendanceLateComeEarlyOut.objects.all()
            )
        else:
            employee_filter = LateComeEarlyOutFilter(
                filters,
                queryset=AttendanceLateComeEarlyOut.objects.filter(
                    employee_id__employee_user_id=request.user
                )
                | AttendanceLateComeEarlyOut.objects.filter(
                    employee_id__employee_work_info__reporting_manager_id__employee_user_id=request.user
                ),
            )

        # Get the filtered queryset
        filtered_employees = employee_filter.qs

        employee_ids = [str(emp.id) for emp in filtered_employees]
        total_count = filtered_employees.count()

        context = {"employee_ids": employee_ids, "total_count": total_count}

        return JsonResponse(context)


@login_required
@hx_request_required
@permission_required("attendance.add_gracetime")
def create_grace_time(request):
    """
    function used to create grace time .

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    GET : return grace time form template
    """
    is_default = eval_validate(request.GET.get("default"))
    form = GraceTimeForm(initial={"is_default": is_default})
    if request.method == "POST":
        form = GraceTimeForm(request.POST)
        if form.is_valid():
            cleaned_data = form.cleaned_data
            gracetime = form.save()
            shifts = cleaned_data.get("shifts")
            for shift in shifts:
                shift.grace_time_id = gracetime
                shift.save()
            messages.success(request, _("Grace time created successfully."))
            return HorillaRedirect(request)
    return render(
        request,
        "attendance/grace_time/grace_time_form.html",
        {"form": form, "is_default": is_default},
    )


@login_required
@hx_request_required
@permission_required("base.change_employeeshift")
def assign_shift(request, grace_id):
    gracetime = GraceTime.objects.filter(id=grace_id).first() if grace_id else None
    if gracetime:
        form = GraceTimeAssignForm()
        if request.method == "POST":
            form = GraceTimeAssignForm(request.POST)
            if form.is_valid():
                cleaned_data = form.cleaned_data
                shifts = cleaned_data.get("shifts")
                for shift in shifts:
                    shift.grace_time_id = gracetime
                    shift.save()
                messages.success(request, _("Grace time added to shifts successfully."))
                return HorillaRedirect(request)
        return render(
            request,
            "attendance/grace_time/assign_shift.html",
            {"form": form, "grace_time": gracetime},
        )


@login_required
@hx_request_required
@permission_required("attendance.change_gracetime")
def update_grace_time(request, grace_id):
    """
    function used to create grace time .

    Parameters:
    request (HttpRequest): The HTTP request object.
    grace_id: id of grace time object
    Returns:
    GET : return grace time form template
    """
    grace_time = GraceTime.objects.get(id=grace_id)
    form = GraceTimeForm(instance=grace_time)
    if request.method == "POST":
        form = GraceTimeForm(request.POST, instance=grace_time)
        if form.is_valid():
            instance = form.save(commit=False)
            instance.save()
            messages.success(request, _("Grace time updated successfully."))
            return HorillaRedirect(request)
    context = {
        "form": form,
        "grace_id": grace_id,
    }
    return render(
        request, "attendance/grace_time/grace_time_form.html", context=context
    )


@login_required
@permission_required("attendance.delete_gracetime")
def delete_grace_time(request, grace_id):
    """
    function used to delete grace time .

    Parameters:
    request (HttpRequest): The HTTP request object.
    grace_id: id of grace time object
    Returns:
    GET : return grace time form template
    """
    try:
        GraceTime.objects.get(id=grace_id).delete()
        messages.success(request, _("Grace time deleted successfully."))
    except GraceTime.DoesNotExist:
        messages.error(request, _("Grace Time Does not exists.."))
    except ProtectedError:
        messages.error(request, _("Related datas exists."))
    context = {
        "condition": AttendanceValidationCondition.objects.first(),
        "default_grace_time": GraceTime.objects.filter(is_default=True).first(),
        "grace_times": GraceTime.objects.all().exclude(is_default=True),
    }

    return render(request, "attendance/grace_time/grace_time_table.html", context)


@login_required
@permission_required("attendance.update_gracetime")
def update_isactive_gracetime(request):
    """
    ajax function to update is active field in GraceTime.
    Args:
    - isChecked: Boolean value representing the state of grace time,
    - gracetimeId: Id of GraceTime object
    """
    isChecked = request.POST.get("isChecked")
    gracetimeId = request.POST.get("gracetimeId")
    gracetime = GraceTime.objects.get(id=gracetimeId)
    if isChecked == "true":
        gracetime.is_active = True
        response = {
            "type": "success",
            "message": _("Gracetime activated successfully."),
        }
    else:
        gracetime.is_active = False
        response = {
            "type": "success",
            "message": _("Gracetime deactivated successfully."),
        }
    gracetime.save()
    return JsonResponse(response)


@login_required
@permission_required("attendance.update_gracetime")
def update_gracetime_clock_in_clock_out(request):
    """
    ajax function to update is active field in grace time.
    Args:
    - isChecked: Boolean value representing the state of grace time,
    - gracetimeId: Id of PayslipAutoGenerate object
    """
    isChecked = request.POST.get("isChecked")
    gracetimeId = request.POST.get("gracetimeId")
    update = request.POST.get("update")
    garcetime = GraceTime.objects.get(id=gracetimeId)
    if update == "clock_in":
        if isChecked == "true":
            garcetime.allowed_clock_in = True
            response = {
                "type": "success",
                "message": _("Gracetime applicable on clock-In successfully."),
            }
        else:
            garcetime.allowed_clock_in = False
            response = {
                "type": "success",
                "message": _("Gracetime unapplicable on clock-In  successfully."),
            }
    elif update == "clock_out":
        if isChecked == "true":
            garcetime.allowed_clock_out = True
            response = {
                "type": "success",
                "message": _("Gracetime applicable on clock-out successfully."),
            }
        else:
            garcetime.allowed_clock_out = False
            response = {
                "type": "success",
                "message": _("Gracetime unapplicable on clock-out successfully."),
            }
    else:
        response = {
            "type": "error",
            "message": _("Something went wrong ."),
        }
    garcetime.save()
    return JsonResponse(response)


@login_required
def create_attendancerequest_comment(request, attendance_id):
    """
    This method renders form and template to create Attendance request comments
    """
    previous_data = request.GET.urlencode()
    attendance = Attendance.objects.filter(id=attendance_id).first()
    emp = request.user.employee_get
    form = AttendanceRequestCommentForm(
        initial={"employee_id": emp.id, "request_id": attendance_id}
    )

    if request.method == "POST":
        form = AttendanceRequestCommentForm(request.POST)
        if form.is_valid():
            form.instance.employee_id = emp
            form.instance.request_id = attendance
            form.save()
            comments = AttendanceRequestComment.objects.filter(
                request_id=attendance_id
            ).order_by("-created_at")
            no_comments = False
            if not comments.exists():
                no_comments = True
            form = AttendanceRequestCommentForm(
                initial={"employee_id": emp.id, "request_id": attendance_id}
            )
            messages.success(request, _("Comment added successfully!"))
            work_info = EmployeeWorkInformation.objects.filter(
                employee_id=attendance.employee_id
            )
            if work_info.exists():
                if (
                    attendance.employee_id.employee_work_info.reporting_manager_id
                    is not None
                ):
                    if request.user.employee_get.id == attendance.employee_id.id:
                        rec = (
                            attendance.employee_id.employee_work_info.reporting_manager_id.employee_user_id
                        )
                        notify.send(
                            request.user.employee_get,
                            recipient=rec,
                            verb=f"{attendance.employee_id}'s attendance request has received a comment.",
                            verb_ar=f"تلقت طلب الحضور {attendance.employee_id} تعليقًا.",
                            verb_de=f"{attendance.employee_id}s Anfrage zur Anwesenheit hat einen Kommentar erhalten.",
                            verb_es=f"La solicitud de asistencia de {attendance.employee_id} ha recibido un comentario.",
                            verb_fr=f"La demande de présence de {attendance.employee_id} a reçu un commentaire.",
                            redirect=reverse("request-attendance-view")
                            + f"?id={attendance.id}",
                            icon="chatbox-ellipses",
                        )
                    elif (
                        request.user.employee_get.id
                        == attendance.employee_id.employee_work_info.reporting_manager_id.id
                    ):
                        rec = attendance.employee_id.employee_user_id
                        notify.send(
                            request.user.employee_get,
                            recipient=rec,
                            verb="Your attendance request has received a comment.",
                            verb_ar="تلقى طلب الحضور الخاص بك تعليقًا.",
                            verb_de="Ihr Antrag auf Anwesenheit hat einen Kommentar erhalten.",
                            verb_es="Tu solicitud de asistencia ha recibido un comentario.",
                            verb_fr="Votre demande de présence a reçu un commentaire.",
                            redirect=reverse("request-attendance-view")
                            + f"?id={attendance.id}",
                            icon="chatbox-ellipses",
                        )
                    else:
                        rec = [
                            attendance.employee_id.employee_user_id,
                            attendance.employee_id.employee_work_info.reporting_manager_id.employee_user_id,
                        ]
                        notify.send(
                            request.user.employee_get,
                            recipient=rec,
                            verb=f"{attendance.employee_id}'s attendance request has received a comment.",
                            verb_ar=f"تلقت طلب الحضور {attendance.employee_id} تعليقًا.",
                            verb_de=f"{attendance.employee_id}s Anfrage zur Anwesenheit hat einen Kommentar erhalten.",
                            verb_es=f"La solicitud de asistencia de {attendance.employee_id} ha recibido un comentario.",
                            verb_fr=f"La demande de présence de {attendance.employee_id} a reçu un commentaire.",
                            redirect=reverse("request-attendance-view")
                            + f"?id={attendance.id}",
                            icon="chatbox-ellipses",
                        )
                else:
                    rec = attendance.employee_id.employee_user_id
                    notify.send(
                        request.user.employee_get,
                        recipient=rec,
                        verb="Your attendance request has received a comment.",
                        verb_ar="تلقى طلب الحضور الخاص بك تعليقًا.",
                        verb_de="Ihr Antrag auf Anwesenheit hat einen Kommentar erhalten.",
                        verb_es="Tu solicitud de asistencia ha recibido un comentario.",
                        verb_fr="Votre demande de présence a reçu un commentaire.",
                        redirect=reverse("request-attendance-view")
                        + f"?id={attendance.id}",
                        icon="chatbox-ellipses",
                    )
            return render(
                request,
                "requests/attendance/attendance_comment.html",
                {
                    "comments": comments,
                    "no_comments": no_comments,
                    "request_id": attendance_id,
                },
            )
    return render(
        request,
        "requests/attendance/attendance_comment.html",
        {
            "form": form,
            "request_id": attendance_id,
            "pd": previous_data,
        },
    )


@login_required
def view_attendancerequest_comment(request, attendance_id):
    """
    This method is used to show Attendance request comments
    """
    comments = AttendanceRequestComment.objects.filter(
        request_id=attendance_id
    ).order_by("-created_at")
    no_comments = False
    if not comments.exists():
        no_comments = True

    if request.FILES:
        files = request.FILES.getlist("files")
        comment_id = request.GET["comment_id"]
        comment = AttendanceRequestComment.objects.get(id=comment_id)
        attachments = []
        for file in files:
            file_instance = AttendanceRequestFile()
            file_instance.file = file
            file_instance.save()
            attachments.append(file_instance)
        comment.files.add(*attachments)

    return render(
        request,
        "requests/attendance/attendance_comment.html",
        {"comments": comments, "no_comments": no_comments, "request_id": attendance_id},
    )


@login_required
def delete_attendancerequest_comment(request, comment_id):
    """
    This method is used to delete Attendance request comments
    """
    script = ""
    comment = AttendanceRequestComment.objects.get(id=comment_id)
    comment.delete()
    messages.success(request, _("Comment deleted successfully!"))
    return HttpResponse(script)


@login_required
def delete_comment_file(request):
    """
    Used to delete attachment
    """
    script = ""
    ids = request.GET.getlist("ids")
    AttendanceRequestFile.objects.filter(id__in=ids).delete()
    messages.success(request, _("File deleted successfully"))
    return HttpResponse(script)


@login_required
def work_records(request):
    today = date.today()
    previous_data = request.GET.urlencode()
    context = {
        "current_date": today,
        "pd": previous_data,
    }
    return render(
        request, "attendance/work_record/work_record_view.html", context=context
    )


@login_required
@hx_request_required
def work_records_change_month(request):
    previous_data = request.GET.urlencode()
    employee_filter_form = EmployeeFilter(request.GET or None)

    employees = filtersubordinatesemployeemodel(
        request, employee_filter_form.qs, "attendance.view_attendance"
    )

    month_str = request.GET.get("month", f"{date.today().year}-{date.today().month}")
    try:
        year, month = map(int, month_str.split("-"))
    except ValueError:
        year, month = date.today().year, date.today().month

    employees = [request.user.employee_get] + list(employees)

    month_dates = [
        datetime(year, month, day).date()
        for week in calendar.monthcalendar(year, month)
        for day in week
        if day
    ]

    work_records = WorkRecords.objects.filter(
        date__in=month_dates, employee_id__in=employees
    ).select_related("employee_id", "shift_id", "attendance_id")

    joining_date_map = dict(
        EmployeeWorkInformation.objects.filter(employee_id__in=employees).values_list(
            "employee_id", "date_joining"
        )
    )

    work_records_dict = {(wr.employee_id.id, wr.date): wr for wr in work_records}

    data = {}
    for employee in employees:
        joining_date = joining_date_map.get(employee.id)
        employee_records = []
        for current_date in month_dates:
            record = work_records_dict.get((employee.id, current_date))
            if joining_date and current_date < joining_date:
                employee_records.append(None)
                continue
            if joining_date is None and record and record.work_record_type == "DFT":
                employee_records.append(None)
                continue
            employee_records.append(record)
        data[employee] = employee_records

    paginator = Paginator(list(data.items()), get_pagination())
    page = paginator.get_page(request.GET.get("page"))

    context = {
        "current_month_dates_list": month_dates,
        "leave_dates": monthly_leave_days(month, year),
        "data": page,
        "pd": previous_data,
        "current_date": date.today(),
        "f": employee_filter_form,
    }

    return render(request, "attendance/work_record/work_record_list.html", context)


@login_required
@permission_required("attendance.view_workrecords")
def work_record_export(request):
    try:
        month = int(request.GET.get("month") or date.today().month)
        year = int(request.GET.get("year") or date.today().year)
    except ValueError:
        return HttpResponseBadRequest("Invalid month or year parameter.")

    employees = EmployeeFilter(request.GET).qs
    records = WorkRecords.objects.filter(date__month=month, date__year=year)
    num_days = calendar.monthrange(year, month)[1]
    all_date_objects = [date(year, month, day) for day in range(1, num_days + 1)]
    leave_dates = set(monthly_leave_days(month, year))
    joining_date_map = dict(
        EmployeeWorkInformation.objects.filter(employee_id__in=employees).values_list(
            "employee_id", "date_joining"
        )
    )

    record_lookup = defaultdict(lambda: "ABS")
    for record in records:
        if record.date <= date.today():
            record_key = (record.employee_id, record.date)
            record_lookup[record_key] = record.work_record_type

    date_format = request.user.employee_get.get_date_format()
    format_string = HORILLA_DATE_FORMATS.get(date_format)
    formatted_dates = [day.strftime(format_string) for day in all_date_objects]
    data_rows = []

    for employee in employees:
        row_data = {"Employee": employee}
        joining_date = joining_date_map.get(employee.id)
        for day, formatted_day in zip(all_date_objects, formatted_dates):
            if joining_date and day < joining_date:
                row_data[formatted_day] = ""
                continue
            if not day in leave_dates and day < date.today():
                fallback = "" if joining_date is None else "DFT"
                value = record_lookup.get((employee, day), fallback)
                row_data[formatted_day] = (
                    "" if joining_date is None and value == "DFT" else value
                )
            else:
                data = record_lookup.get((employee, day), "")
                row_data[formatted_day] = data if data != "DFT" else ""
        data_rows.append(row_data)

    columns = ["Employee"] + formatted_dates
    df = pd.DataFrame(data_rows, columns=columns)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Sheet1")
        workbook = writer.book
        worksheet = writer.sheets["Sheet1"]

        formats = {
            "ABS": workbook.add_format(
                {"bg_color": "#808080", "font_color": "#ffffff"}
            ),
            "FDP": workbook.add_format(
                {"bg_color": "#38c338", "font_color": "#ffffff"}
            ),
            "HDP": workbook.add_format(
                {"bg_color": "#dfdf52", "font_color": "#000000"}
            ),
            "CONF": workbook.add_format(
                {"bg_color": "#ed4c4c", "font_color": "#ffffff"}
            ),
            "DFT": workbook.add_format(
                {"bg_color": "#a8b1ff", "font_color": "#ffffff"}
            ),
        }

        for row_idx, row in enumerate(df.itertuples(index=False), start=1):
            for col_idx, cell_value in enumerate(row[1:], start=1):
                if cell_value in formats:
                    worksheet.write(row_idx, col_idx, cell_value, formats[cell_value])

        for col_idx, col in enumerate(df.columns):
            max_len = max(df[col].astype(str).map(len).max(), len(col))
            worksheet.set_column(col_idx, col_idx, max_len)

    output.seek(0)

    response = HttpResponse(
        output.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="work_record_export.xlsx"'
    return response


@login_required
@hx_request_required
@permission_required("attendance.add_attendancegeneralsetting")
def enable_timerunner(request):
    """
    This method is used to enable/disable the timerunner feature
    """

    time_runner = AttendanceGeneralSetting.objects.first()
    time_runner = time_runner if time_runner else AttendanceGeneralSetting()
    time_runner.time_runner = "time_runner" in request.GET.keys()
    time_runner.save()
    return HttpResponse("success")


@login_required
@permission_required("base.view_tracklatecomeearlyout")
def track_late_come_early_out(request):
    """
    Renders the form to track late arrivals and early departures in attendance.
    """
    tracking = TrackLateComeEarlyOut.objects.first()
    form = TrackLateComeEarlyOutForm(
        initial={"is_enable": tracking.is_enable} if tracking else {}
    )
    return render(
        request, "attendance/late_come_early_out/tracking.html", {"form": form}
    )


@login_required
@permission_required("base.change_tracklatecomeearlyout")
def enable_disable_tracking_late_come_early_out(request):
    """
    Enables or disables the tracking of late arrivals and early departures in attendance.
    """
    if request.method == "POST":
        enable = bool(request.POST.get("is_enable"))
        tracking, created = TrackLateComeEarlyOut.objects.get_or_create()
        tracking.is_enable = enable
        tracking.save()
        message = _("enabled") if enable else _("disabled")
        messages.success(
            request, _("Tracking late come early out {} successfully").format(message)
        )
    return HorillaRedirect(request)


@login_required
def check_in_check_out_setting(request):
    """
    Check in check out setting
    """
    attendance_settings = AttendanceGeneralSetting.objects.all()
    return render(
        request,
        "attendance/settings/check_in_check_out_enable_form.html",
        {"attendance_settings": attendance_settings},
    )


@login_required
@hx_request_required
@permission_required("attendance.change_attendancegeneralsetting")
def enable_disable_check_in(request):
    """
    Enables or disables check-in check-out.
    """
    if request.method == "POST":
        setting_id = request.POST.get("setting_Id")
        setting = AttendanceGeneralSetting.objects.filter(id=setting_id).first()
        if not setting:
            return HttpResponse("")

        if (
            "portal_break_limit" in request.POST
            or "portal_break_minutes" in request.POST
            or "portal_lunch_minutes" in request.POST
        ):
            portal_setting_fields = [
                "portal_break_limit",
                "portal_break_minutes",
                "portal_lunch_minutes",
            ]
            updated_fields = []
            for field_name in portal_setting_fields:
                if field_name not in request.POST:
                    continue
                try:
                    value = int(request.POST.get(field_name))
                except (TypeError, ValueError):
                    value = None
                if value and value > 0:
                    setattr(setting, field_name, value)
                    updated_fields.append(field_name)
            if updated_fields:
                setting.save(update_fields=updated_fields)
                messages.success(request, _("Portal break/lunch settings updated."))
            return HttpResponse("success")

        is_checked = request.POST.get("isChecked")
        enable = bool(is_checked)
        setting.enable_check_in = enable
        setting.save(update_fields=["enable_check_in"])

        message = _("Check In/Check Out has been successfully {}.").format(
            _("enabled") if enable else _("disabled")
        )
        messages.success(request, message)
        if enable:
            return render(request, "attendance/components/in_out_component.html")

    return HttpResponse("")


@login_required
@permission_required("attendance.view_attendancevalidationcondition")
def grace_time_view(request):
    """
    This method view attendance validation conditions.
    """
    condition = AttendanceValidationCondition.objects.first()
    default_grace_time = GraceTime.objects.filter(is_default=True).first()
    grace_times = GraceTime.objects.all().exclude(is_default=True)
    return render(
        request,
        "attendance/grace_time/grace_time.html",
        {
            "condition": condition,
            "default_grace_time": default_grace_time,
            "grace_times": grace_times,
        },
    )


@login_required
@permission_required("attendance.view_attendancevalidationcondition")
def validation_condition_view(request):
    """
    This method view attendance validation conditions.
    """

    condition = AttendanceValidationCondition.objects.first()
    default_grace_time = GraceTime.objects.filter(is_default=True).first()
    return render(
        request,
        "attendance/break_point/condition.html",
        {"condition": condition, "default_grace_time": default_grace_time},
    )


@login_required
@permission_required("attendance.add_attendancevalidationcondition")
def validation_condition_create(request):
    """
    This method render a form to create attendance validation conditions,
    and create if the form is valid.
    """
    form = AttendanceValidationConditionForm()
    if request.method == "POST":
        form = AttendanceValidationConditionForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, _("Attendance Break-point settings created."))
            form = AttendanceValidationConditionForm()
    return render(
        request,
        "attendance/break_point/condition_form.html",
        {"form": form},
    )


@login_required
@hx_request_required
@permission_required("attendance.change_attendancevalidationcondition")
def validation_condition_update(request, obj_id):
    """
    This method is used to update validation condition
    Args:
        obj_id : validation condition instance id
    """
    condition = AttendanceValidationCondition.objects.get(id=obj_id)
    form = AttendanceValidationConditionForm(instance=condition)
    if request.method == "POST":
        form = AttendanceValidationConditionForm(request.POST, instance=condition)
        if form.is_valid():
            form.save()
            messages.success(request, _("Attendance Break-point settings updated."))
    return render(
        request,
        "attendance/break_point/condition_form.html",
        {"form": form, "condition": condition},
    )


@login_required
@permission_required("attendance.add_attendance")
def allowed_ips(request):
    """
    This function is used to view the allowed ips
    """
    allowed_ips = AttendanceAllowedIP.objects.first()
    return render(
        request,
        "attendance/ip_restriction/ip_restriction.html",
        {"allowed_ips": allowed_ips},
    )


@login_required
@permission_required("attendance.add_attendance")
def enable_ip_restriction(request):
    """
    This function is used to enable the allowed ips
    """
    form = AttendanceAllowedIPForm()
    if request.method == "POST":
        ip_restiction = AttendanceAllowedIP.objects.first()

        if not ip_restiction:
            ip_restiction = AttendanceAllowedIP.objects.create(is_enabled=True)
            return HorillaRedirect(request)

        if not ip_restiction.is_enabled:
            ip_restiction.is_enabled = True
        elif ip_restiction.is_enabled:
            ip_restiction.is_enabled = False

        ip_restiction.save()
        return HorillaRedirect(request)


def validate_ip_address(self, value):
    """
    This function is used to check if the provided IP is in the ipv4 or ipv6 format.

    Args:
        value: The IP address to validate
    """
    try:
        validate_ipv46_address(value)
    except ValidationError:
        raise ValidationError("Enter a valid IPv4 or IPv6 address.")
    return value


@login_required
@permission_required("attendance.add_attendance")
def create_allowed_ips(request):
    """
    This function is used to create the allowed IPs.
    """
    if request.method == "POST":
        form = AttendanceAllowedIPForm(request.POST)
        if form.is_valid():
            ip_addresses = form.cleaned_data.get("ip_addresses")
            allowed_ips = AttendanceAllowedIP.objects.first()
            if allowed_ips:
                existing_ips = set(allowed_ips.additional_data.get("allowed_ips", []))
                new_ips = set(ip_addresses)
                duplicates = new_ips.intersection(existing_ips)

                if duplicates:
                    messages.error(
                        request, f"IP addresses already exist: {', '.join(duplicates)}"
                    )

                non_duplicates = new_ips - duplicates

                if non_duplicates:
                    allowed_ips.additional_data["allowed_ips"] = list(
                        existing_ips.union(non_duplicates)
                    )
                    allowed_ips.save()
                    messages.success(request, "IP addresses saved successfully")
                else:
                    messages.info(
                        request,
                        "All provided IP addresses are already in the allowed list.",
                    )

            else:
                AttendanceAllowedIP.objects.create(
                    is_enabled=True, additional_data={"allowed_ips": ip_addresses}
                )
                messages.success(request, "IP addresses saved successfully")

            return HorillaRedirect(request)
    else:
        form = AttendanceAllowedIPForm()

    return render(
        request, "attendance/ip_restriction/restrict_form.html", {"form": form}
    )


@login_required
@permission_required("attendance.delete_attendance")
def delete_allowed_ips(request):
    """
    This function is used to delete the allowed ips
    """
    try:
        ids = request.GET.getlist("id")
        allowed_ips = AttendanceAllowedIP.objects.first()
        ips = allowed_ips.additional_data["allowed_ips"]
        for id in ids:
            ips.pop(eval_validate(id))

        allowed_ips.additional_data["allowed_ips"] = ips
        allowed_ips.save()

        messages.success(request, "IP address removed successfully")
    except:
        messages.error(request, "Invalid id")
    return redirect("allowed-ips")


@login_required
@permission_required("attendance.change_attendance")
def edit_allowed_ips(request):
    """
    This function is used to edit the allowed IPs.
    """
    allowed_ips = AttendanceAllowedIP.objects.first()
    if not allowed_ips:
        messages.error(request, "No allowed IPs found.")
        return redirect("allowed-ips")

    ips = allowed_ips.additional_data.get("allowed_ips", [])
    id = request.GET.get("id")

    try:
        id = int(id)
        if id < 0 or id >= len(ips):
            raise IndexError

        initial_ip = ips[id]
        form = AttendanceAllowedIPForm(initial={"ip_addresses": initial_ip})

        if request.method == "POST":
            form = AttendanceAllowedIPForm(request.POST)
            if form.is_valid():
                new_ip = form.cleaned_data["ip_addresses"][0]

                existing_ips = set(allowed_ips.additional_data.get("allowed_ips", []))

                if new_ip in existing_ips:
                    messages.error(request, "IP address already exists.")
                else:
                    existing_ips.discard(initial_ip)
                    existing_ips.add(new_ip)

                    allowed_ips.additional_data["allowed_ips"] = list(existing_ips)
                    allowed_ips.save()
                    messages.success(request, "IP address updated successfully")
                return HorillaRedirect(request)

    except (ValueError, IndexError):
        messages.error(request, "Invalid ID provided.")

    return render(
        request,
        "attendance/ip_restriction/restrict_form.html",
        {"form": form, "id": id},
    )
