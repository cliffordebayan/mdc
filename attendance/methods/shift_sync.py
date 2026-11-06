"""Synchronize administrator-managed shifts with attendance portal copies."""

import logging

from attendance.models import (
    AttendancePortalShiftContext,
    AttendancePortalShiftOverride,
)
from base.models import EmployeeShift, EmployeeShiftSchedule
from employee.models import EmployeeWorkInformation

logger = logging.getLogger(__name__)


def _copy_shift_metadata(source_shift, portal_shift):
    """Copy base-shift metadata without running model-level side effects."""
    update_values = {
        "weekly_full_time": source_shift.weekly_full_time,
        "full_time": source_shift.full_time,
        "grace_time_id_id": getattr(source_shift, "grace_time_id_id", None),
    }
    EmployeeShift._base_manager.filter(pk=portal_shift.pk).update(**update_values)


def _copy_schedule(source_schedule, portal_shift):
    """Copy one recurring schedule row to an employee's portal shift."""
    defaults = {
        "minimum_working_hour": source_schedule.minimum_working_hour,
        "start_time": source_schedule.start_time,
        "end_time": source_schedule.end_time,
        "is_night_shift": source_schedule.is_night_shift,
        "is_rest_day": source_schedule.is_rest_day,
        "is_auto_punch_out_enabled": source_schedule.is_auto_punch_out_enabled,
        "auto_punch_out_time": source_schedule.auto_punch_out_time,
    }
    EmployeeShiftSchedule._base_manager.update_or_create(
        shift_id_id=portal_shift.pk,
        day_id=source_schedule.day_id,
        defaults=defaults,
    )


def synchronize_portal_shift_change(source_shift, schedules=None):
    """Propagate an administrator shift change to employee portal copies.

    The base shift remains the source of truth. Portal copies are updated only
    for employees that already have a portal context, and only the current
    attendance row is recalculated. Historical attendance rows are untouched.
    Active portal overrides for changed weekdays are disabled because an admin
    schedule edit is authoritative.
    """
    if not source_shift or not getattr(source_shift, "pk", None):
        return {"employees": 0, "synced": 0, "failed": 0}

    changed_schedules = [schedule for schedule in (schedules or []) if schedule]
    work_infos = list(
        EmployeeWorkInformation.objects.filter(shift_id=source_shift).select_related(
            "employee_id"
        )
    )
    employee_ids = [work_info.employee_id_id for work_info in work_infos]

    if changed_schedules and employee_ids:
        AttendancePortalShiftOverride._base_manager.filter(
            employee_id_id__in=employee_ids,
            day_id__in={schedule.day_id for schedule in changed_schedules},
            is_active=True,
        ).update(is_active=False)

    synced = 0
    failed = 0
    for work_info in work_infos:
        employee = work_info.employee_id
        try:
            portal_context = (
                AttendancePortalShiftContext._base_manager.filter(
                    employee_id_id=work_info.employee_id_id,
                )
                .select_related("shift_id")
                .first()
            )
            if not portal_context:
                continue

            portal_shift = portal_context.shift_id
            _copy_shift_metadata(source_shift, portal_shift)
            for schedule in changed_schedules:
                _copy_schedule(schedule, portal_shift)

            # Import lazily to avoid coupling the reusable synchronization
            # service to the portal view module during app initialization.
            from attendance.methods.utils import recalculate_attendance_for_day
            from attendance.views.portal import synchronize_portal_attendance_shift

            current_attendance = synchronize_portal_attendance_shift(employee)
            if current_attendance:
                recalculate_attendance_for_day(current_attendance)
            synced += 1
        except Exception:
            failed += 1
            logger.exception(
                "Attendance portal shift synchronization failed for employee %s",
                work_info.employee_id_id,
            )

    return {"employees": len(work_infos), "synced": synced, "failed": failed}
