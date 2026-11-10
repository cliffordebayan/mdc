"""Employee-owned shift creation and assignment helpers.

Shift records with no owner_employee are reusable templates. Once a template
is assigned to an employee, the employee receives an independent copy.
"""

from datetime import date, datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from base.models import EmployeeShift, EmployeeShiftDay, EmployeeShiftSchedule
from employee.models import Employee, EmployeeWorkInformation


DEFAULT_START_TIME = time(8, 0)
DEFAULT_END_TIME = time(17, 0)
DEFAULT_WORKING_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday")
DEFAULT_REST_DAYS = ("saturday", "sunday")


def _employee_shift_name(employee):
    identifier = str(employee.employee_no or employee.pk or "employee")
    suffix = "_shift"
    max_length = EmployeeShift._meta.get_field("employee_shift").max_length
    name = f"{identifier}{suffix}"
    if len(name) <= max_length:
        return name
    return f"employee_{employee.pk}{suffix}"[:max_length]


def _duration_label(start_time, end_time):
    if not start_time or not end_time:
        return "00:00"
    start = datetime.combine(date(2000, 1, 1), start_time)
    end = datetime.combine(date(2000, 1, 1), end_time)
    if end <= start:
        end += timedelta(days=1)
    minutes = int((end - start).total_seconds() // 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _copy_schedule_fields(source_schedule):
    return {
        "minimum_working_hour": source_schedule.minimum_working_hour,
        "start_time": source_schedule.start_time,
        "end_time": source_schedule.end_time,
        "is_night_shift": bool(
            source_schedule.start_time
            and source_schedule.end_time
            and source_schedule.start_time > source_schedule.end_time
        ),
        "is_rest_day": source_schedule.is_rest_day,
        "is_auto_punch_out_enabled": getattr(
            source_schedule, "is_auto_punch_out_enabled", False
        ),
        "auto_punch_out_time": getattr(source_schedule, "auto_punch_out_time", None),
    }


def _copy_shift_schedules(source_shift, target_shift):
    source_schedules = list(
        EmployeeShiftSchedule._base_manager.filter(shift_id=source_shift).order_by("id")
    )
    if not source_schedules:
        EmployeeShiftSchedule._base_manager.filter(shift_id=target_shift).delete()
        _create_default_schedules(target_shift)
        return

    source_day_ids = {schedule.day_id for schedule in source_schedules}
    EmployeeShiftSchedule._base_manager.filter(shift_id=target_shift).exclude(
        day_id__in=source_day_ids
    ).delete()
    for source_schedule in source_schedules:
        EmployeeShiftSchedule._base_manager.update_or_create(
            shift_id=target_shift,
            day_id=source_schedule.day_id,
            defaults=_copy_schedule_fields(source_schedule),
        )


def _create_default_schedules(shift):
    days = {
        day.day: day
        for day in EmployeeShiftDay._base_manager.filter(
            day__in=(*DEFAULT_WORKING_DAYS, *DEFAULT_REST_DAYS)
        )
    }
    for day_name in (*DEFAULT_WORKING_DAYS, *DEFAULT_REST_DAYS):
        day = days.get(day_name)
        if day is None:
            day = EmployeeShiftDay._base_manager.create(day=day_name)
        EmployeeShiftSchedule._base_manager.update_or_create(
            shift_id=shift,
            day=day,
            defaults={
                "minimum_working_hour": "09:00",
                "start_time": DEFAULT_START_TIME,
                "end_time": DEFAULT_END_TIME,
                "is_night_shift": False,
                "is_rest_day": day_name in DEFAULT_REST_DAYS,
            },
        )


def _copy_shift_metadata(source_shift, target_shift):
    target_shift.weekly_full_time = getattr(source_shift, "weekly_full_time", "40:00")
    target_shift.full_time = getattr(source_shift, "full_time", "200:00")
    if hasattr(target_shift, "grace_time_id_id"):
        target_shift.grace_time_id_id = getattr(source_shift, "grace_time_id_id", None)
    target_shift.save()

    if hasattr(source_shift, "company_id") and hasattr(target_shift, "company_id"):
        target_shift.company_id.set(source_shift.company_id.all())


@transaction.atomic
def assign_employee_shift(employee, source_shift=None):
    """Assign an independent shift to an employee."""
    employee = Employee.objects.entire().select_for_update().get(pk=employee.pk)
    work_info = (
        EmployeeWorkInformation._base_manager.select_for_update()
        .filter(employee_id=employee)
        .first()
    )
    if work_info is None:
        work_info = EmployeeWorkInformation(employee_id=employee)

    current_shift = work_info.shift_id
    source_shift = source_shift or current_shift
    if source_shift and source_shift.owner_employee_id not in (None, employee.pk):
        raise ValidationError(
            _("An employee cannot use another employee's private shift.")
        )

    owned_shift = (
        EmployeeShift._base_manager.select_for_update()
        .filter(owner_employee_id=employee.pk)
        .first()
    )
    if owned_shift is None:
        create_kwargs = {
            "employee_shift": _employee_shift_name(employee),
            "owner_employee": employee,
            "weekly_full_time": getattr(source_shift, "weekly_full_time", "40:00")
            if source_shift
            else "40:00",
            "full_time": getattr(source_shift, "full_time", "200:00")
            if source_shift
            else "200:00",
        }
        if hasattr(EmployeeShift, "grace_time_id"):
            create_kwargs["grace_time_id_id"] = (
                getattr(source_shift, "grace_time_id_id", None) if source_shift else None
            )
        owned_shift = EmployeeShift._base_manager.create(**create_kwargs)

    if source_shift and source_shift.pk != owned_shift.pk:
        _copy_shift_metadata(source_shift, owned_shift)
        _copy_shift_schedules(source_shift, owned_shift)
    elif not EmployeeShiftSchedule._base_manager.filter(shift_id=owned_shift).exists():
        _create_default_schedules(owned_shift)

    if work_info.pk is None:
        work_info.shift_id = owned_shift
        work_info.save()
    elif work_info.shift_id_id != owned_shift.pk:
        work_info.shift_id = owned_shift
        work_info.save(update_fields=["shift_id"])
    return owned_shift


def ensure_employee_owned_shift(employee):
    """Return the employee's private shift, creating it when necessary."""
    employee = Employee.objects.entire().get(pk=employee.pk)
    work_info = (
        EmployeeWorkInformation._base_manager.filter(employee_id=employee)
        .select_related("shift_id")
        .first()
    )
    if (
        work_info
        and work_info.shift_id
        and work_info.shift_id.owner_employee_id == employee.pk
    ):
        return work_info.shift_id
    return assign_employee_shift(employee, work_info.shift_id if work_info else None)


@transaction.atomic
def update_employee_shift_schedule(
    employee,
    day,
    start_time,
    end_time,
    minimum_working_hour=None,
    is_rest_day=False,
    is_auto_punch_out_enabled=False,
    auto_punch_out_time=None,
):
    """Persist one employee-owned recurring schedule row."""
    shift = ensure_employee_owned_shift(employee)
    minimum_working_hour = minimum_working_hour or _duration_label(start_time, end_time)
    schedule, _created = EmployeeShiftSchedule._base_manager.update_or_create(
        shift_id=shift,
        day=day,
        defaults={
            "minimum_working_hour": minimum_working_hour,
            "start_time": start_time,
            "end_time": end_time,
            "is_night_shift": bool(start_time and end_time and start_time > end_time),
            "is_rest_day": is_rest_day,
            "is_auto_punch_out_enabled": is_auto_punch_out_enabled,
            "auto_punch_out_time": auto_punch_out_time,
        },
    )
    return shift, schedule
