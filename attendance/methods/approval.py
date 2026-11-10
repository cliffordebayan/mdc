"""Shared transactional helpers for manager-approved attendance requests."""

import json
from datetime import date, datetime, time

from django.db import transaction
from django.utils import timezone

from attendance.models import Attendance, AttendanceActivity
from attendance.methods.utils import shift_schedule_today
from attendance.views.clock_in_out import early_out, late_come
from base.models import EmployeeShiftDay


REQUESTED_ATTENDANCE_FIELDS = {
    "attendance_date",
    "attendance_clock_in_date",
    "attendance_clock_in",
    "attendance_clock_out_date",
    "attendance_clock_out",
    "shift_id",
    "work_type_id",
    "attendance_worked_hour",
    "minimum_hour",
    "batch_attendance_id",
}


def _activity_datetime(activity_date, activity_time):
    if not activity_date or not activity_time:
        return None
    value = datetime.combine(activity_date, activity_time)
    if timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_default_timezone())
    return value


def _requested_value(field, value):
    if value in (None, "", "None"):
        return None
    if field.endswith("_date") or field == "attendance_date":
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value))
    if field in {
        "attendance_clock_in",
        "attendance_clock_out",
    }:
        if isinstance(value, time):
            return value
        return time.fromisoformat(str(value))
    if field in {"shift_id", "work_type_id", "batch_attendance_id"}:
        return int(value)
    return value


def _apply_requested_data(attendance, requested_data):
    for field, value in (requested_data or {}).items():
        if field in REQUESTED_ATTENDANCE_FIELDS:
            setattr(attendance, field, _requested_value(field, value))


@transaction.atomic
def approve_attendance_request(attendance_or_id, approver=None):
    """Apply a pending attendance request and synchronize its activity."""
    if isinstance(attendance_or_id, Attendance):
        attendance = (
            Attendance._base_manager.select_for_update().get(pk=attendance_or_id.pk)
        )
    else:
        attendance = Attendance._base_manager.select_for_update().get(
            pk=attendance_or_id
        )
    if not attendance.is_validate_request:
        raise ValueError("Attendance request is no longer pending.")

    previous_state = {
        "attendance_date": attendance.attendance_date,
        "attendance_clock_in_date": attendance.attendance_clock_in_date,
        "attendance_clock_in": attendance.attendance_clock_in,
    }
    requested_data = attendance.requested_data
    if isinstance(requested_data, str):
        requested_data = json.loads(requested_data)
    _apply_requested_data(attendance, requested_data)
    attendance.attendance_validated = True
    attendance.is_validate_request_approved = True
    attendance.is_validate_request = False
    attendance.approval_status = "approved"
    attendance.approval_decided_at = timezone.now()
    attendance.approval_decided_by = approver
    attendance.approved_by = approver
    attendance.approval_decision_note = None
    attendance.request_description = None
    attendance.requested_data = None
    attendance.save()

    sync_approved_attendance_activity(attendance, previous_state)

    if attendance.shift_id and attendance.attendance_date:
        day = EmployeeShiftDay.objects.filter(
            day=attendance.attendance_date.strftime("%A").lower()
        ).first()
        if day:
            _, start_time_sec, end_time_sec = shift_schedule_today(
                day=day, shift=attendance.shift_id
            )
            if attendance.attendance_clock_in:
                late_come(
                    attendance,
                    start_time=start_time_sec,
                    end_time=end_time_sec,
                    shift=attendance.shift_id,
                )
            if attendance.attendance_clock_out:
                early_out(
                    attendance,
                    start_time=start_time_sec,
                    end_time=end_time_sec,
                    shift=attendance.shift_id,
                )
    return attendance


@transaction.atomic
def reject_attendance_request(attendance_or_id, approver=None, note=None):
    """Reject a pending request without creating or changing an activity."""
    if isinstance(attendance_or_id, Attendance):
        attendance = (
            Attendance._base_manager.select_for_update().get(pk=attendance_or_id.pk)
        )
    else:
        attendance = Attendance._base_manager.select_for_update().get(
            pk=attendance_or_id
        )
    if not attendance.is_validate_request:
        raise ValueError("Attendance request is no longer pending.")

    is_create_request = attendance.request_type == "create_request"
    attendance.is_validate_request = False
    attendance.is_validate_request_approved = False
    attendance.attendance_validated = False
    attendance.approval_status = "rejected"
    attendance.approval_decided_at = timezone.now()
    attendance.approval_decided_by = approver
    attendance.approval_decision_note = note or "Rejected by reporting manager."
    if is_create_request:
        attendance.attendance_clock_in_date = None
        attendance.attendance_clock_in = None
        attendance.attendance_clock_out_date = None
        attendance.attendance_clock_out = None
        attendance.attendance_worked_hour = "00:00"
    attendance.save()
    return attendance


def sync_approved_attendance_activity(attendance, previous_state=None):
    """Create or update the work activity represented by approved attendance."""
    if not attendance.attendance_clock_in:
        return None

    previous_state = previous_state or {}
    previous_date = previous_state.get("attendance_date", attendance.attendance_date)
    previous_in_date = previous_state.get(
        "attendance_clock_in_date", attendance.attendance_clock_in_date
    )
    previous_in = previous_state.get(
        "attendance_clock_in", attendance.attendance_clock_in
    )
    activity = (
        AttendanceActivity._base_manager.filter(
            employee_id=attendance.employee_id,
            attendance_date=previous_date,
            clock_in_date=previous_in_date,
            clock_in=previous_in,
        )
        .order_by("id")
        .first()
    )
    if activity is None:
        activity = (
            AttendanceActivity._base_manager.filter(
                employee_id=attendance.employee_id,
                attendance_date=attendance.attendance_date,
                clock_in_date=attendance.attendance_clock_in_date,
                clock_in=attendance.attendance_clock_in,
            )
            .order_by("id")
            .first()
        )

    shift_day = attendance.attendance_day
    if shift_day is None:
        shift_day = EmployeeShiftDay._base_manager.filter(
            day=attendance.attendance_date.strftime("%A").lower()
        ).first()

    values = {
        "employee_id": attendance.employee_id,
        "attendance_date": attendance.attendance_date,
        "shift_day": shift_day,
        "clock_in_date": attendance.attendance_clock_in_date
        or attendance.attendance_date,
        "clock_in": attendance.attendance_clock_in,
        "in_datetime": _activity_datetime(
            attendance.attendance_clock_in_date or attendance.attendance_date,
            attendance.attendance_clock_in,
        ),
        "clock_out_date": attendance.attendance_clock_out_date
        if attendance.attendance_clock_out
        else None,
        "clock_out": attendance.attendance_clock_out,
        "out_datetime": _activity_datetime(
            attendance.attendance_clock_out_date,
            attendance.attendance_clock_out,
        ),
        "source": "website",
    }
    if activity is None:
        return AttendanceActivity._base_manager.create(**values)

    for field, value in values.items():
        setattr(activity, field, value)
    activity.save()
    return activity
