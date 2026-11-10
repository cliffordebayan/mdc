from datetime import time

from django.db import migrations
from django.utils import timezone


WORKING_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday")
REST_DAYS = ("saturday", "sunday")


def _schedule_values(schedule):
    return {
        "minimum_working_hour": schedule.minimum_working_hour,
        "start_time": schedule.start_time,
        "end_time": schedule.end_time,
        "is_night_shift": bool(
            schedule.start_time
            and schedule.end_time
            and schedule.start_time > schedule.end_time
        ),
        "is_rest_day": schedule.is_rest_day,
        "is_auto_punch_out_enabled": getattr(
            schedule, "is_auto_punch_out_enabled", False
        ),
        "auto_punch_out_time": getattr(schedule, "auto_punch_out_time", None),
    }


def _copy_schedules(source, target, Schedule):
    source_rows = list(Schedule.objects.filter(shift_id=source).order_by("id"))
    if source_rows:
        source_day_ids = {row.day_id for row in source_rows}
        Schedule.objects.filter(shift_id=target).exclude(
            day_id__in=source_day_ids
        ).delete()
        for row in source_rows:
            Schedule.objects.update_or_create(
                shift_id=target,
                day_id=row.day_id,
                defaults=_schedule_values(row),
            )
        return

    Schedule.objects.filter(shift_id=target).delete()


def _default_schedules(target, Day, Schedule):
    days = {
        day.day: day
        for day in Day.objects.filter(day__in=(*WORKING_DAYS, *REST_DAYS))
    }
    for day_name in (*WORKING_DAYS, *REST_DAYS):
        day = days.get(day_name) or Day.objects.create(day=day_name)
        Schedule.objects.update_or_create(
            shift_id=target,
            day=day,
            defaults={
                "minimum_working_hour": "09:00",
                "start_time": time(8, 0),
                "end_time": time(17, 0),
                "is_night_shift": False,
                "is_rest_day": day_name in REST_DAYS,
                "is_auto_punch_out_enabled": False,
                "auto_punch_out_time": None,
            },
        )


def _copy_shift(source, employee, Shift, Schedule):
    target = Shift.objects.create(
        employee_shift=f"{employee.employee_no or employee.pk}_shift"[:50],
        owner_employee=employee,
        weekly_full_time=getattr(source, "weekly_full_time", "40:00")
        if source
        else "40:00",
        full_time=getattr(source, "full_time", "200:00") if source else "200:00",
        grace_time_id_id=getattr(source, "grace_time_id_id", None)
        if source
        else None,
    )
    if source:
        target.company_id.set(source.company_id.all())
        _copy_schedules(source, target, Schedule)
    return target


def forwards(apps, schema_editor):
    Employee = apps.get_model("employee", "Employee")
    WorkInfo = apps.get_model("employee", "EmployeeWorkInformation")
    Shift = apps.get_model("base", "EmployeeShift")
    Day = apps.get_model("base", "EmployeeShiftDay")
    Schedule = apps.get_model("base", "EmployeeShiftSchedule")
    Attendance = apps.get_model("attendance", "Attendance")
    PortalContext = apps.get_model("attendance", "AttendancePortalShiftContext")
    PortalOverride = apps.get_model("attendance", "AttendancePortalShiftOverride")

    for employee in Employee.objects.all().iterator():
        work_info = WorkInfo.objects.filter(employee_id=employee).first()
        context = PortalContext.objects.filter(employee_id=employee).first()
        current_shift = work_info.shift_id if work_info else None
        owned_shift = Shift.objects.filter(owner_employee=employee).first()

        context_shift = context.shift_id if context else None
        if context_shift and context_shift.owner_employee_id in (None, employee.pk):
            owned_shift = context_shift
            if owned_shift.owner_employee_id != employee.pk:
                owned_shift.owner_employee = employee
                owned_shift.save(update_fields=["owner_employee"])
        elif owned_shift is None:
            source_shift = current_shift or context_shift
            owned_shift = _copy_shift(source_shift, employee, Shift, Schedule)

        if not Schedule.objects.filter(shift_id=owned_shift).exists():
            _default_schedules(owned_shift, Day, Schedule)

        for override in PortalOverride.objects.filter(
            employee_id=employee,
            is_active=True,
        ).select_related("day"):
            Schedule.objects.update_or_create(
                shift_id=owned_shift,
                day_id=override.day_id,
                defaults={
                    "minimum_working_hour": override.minimum_working_hour,
                    "start_time": override.start_time,
                    "end_time": override.end_time,
                    "is_night_shift": override.is_night_shift,
                    "is_rest_day": False,
                    "is_auto_punch_out_enabled": False,
                    "auto_punch_out_time": None,
                },
            )
            override.is_active = False
            override.save(update_fields=["is_active"])

        if work_info is None:
            work_info = WorkInfo.objects.create(
                employee_id=employee,
                shift_id=owned_shift,
            )
        elif work_info.shift_id_id != owned_shift.pk:
            work_info.shift_id = owned_shift
            work_info.save(update_fields=["shift_id"])

        # Only today's attendance is a live calculation target. Historical
        # attendance keeps its original shift foreign key for auditability.
        today = timezone.localdate()
        today_day = Day.objects.filter(day=today.strftime("%A").lower()).first()
        today_schedule = (
            Schedule.objects.filter(
                shift_id=owned_shift,
                day=today_day,
                is_rest_day=False,
            ).first()
            if today_day
            else None
        )
        if today_schedule:
            Attendance.objects.filter(
                employee_id=employee,
                attendance_date=today,
            ).update(
                shift_id=owned_shift.pk,
                minimum_hour=today_schedule.minimum_working_hour,
            )

        if context:
            context.delete()


def backwards(apps, schema_editor):
    # Historical attendance rows intentionally remain untouched. The ownership
    # migration is not safely reversible without recreating shared schedules.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("base", "0014_employeeshift_owner_employee"),
        ("employee", "0020_alter_bank_modified_by"),
        ("attendance", "0018_attendanceactivity_is_auto_punch_out"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
