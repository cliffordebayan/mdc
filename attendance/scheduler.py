import datetime
import sys

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from base.backends import logger


def auto_checkout_attendance():
    """
    Close open attendance activities after their configured automatic checkout
    time. This runs in the scheduler so normal page requests never perform the
    attendance-wide scan.
    """
    from attendance.methods.utils import Request
    from attendance.models import Attendance, AttendanceActivity
    from attendance.views.clock_in_out import clock_out
    from base.models import EmployeeShiftSchedule

    current_time = timezone.localtime(timezone.now())
    automatic_check_out_shifts = EmployeeShiftSchedule.objects.filter(
        is_auto_punch_out_enabled=True,
        auto_punch_out_time__isnull=False,
    ).select_related("day", "shift_id")

    for shift_schedule in automatic_check_out_shifts.iterator():
        activities = (
            AttendanceActivity.objects.filter(
                shift_day=shift_schedule.day,
                clock_out_date__isnull=True,
                clock_out__isnull=True,
            )
            .select_related("employee_id", "employee_id__employee_user_id")
            .order_by("-created_at")
        )

        for activity in activities.iterator():
            try:
                with transaction.atomic():
                    # Re-read and lock the activity before acting on it. This
                    # makes overlapping scheduler processes safe: a process
                    # that waits for the lock will see the activity as closed
                    # and skip it on its next query.
                    activity = (
                        AttendanceActivity.objects.select_for_update(of=("self",))
                        .select_related(
                            "employee_id", "employee_id__employee_user_id"
                        )
                        .filter(
                            pk=activity.pk,
                            clock_out_date__isnull=True,
                            clock_out__isnull=True,
                        )
                        .order_by("pk")
                        .first()
                    )
                    if not activity:
                        continue

                    attendance = (
                        Attendance.objects.select_for_update(of=("self",))
                        .filter(
                            employee_id=activity.employee_id,
                            attendance_clock_out__isnull=True,
                            attendance_clock_out_date__isnull=True,
                            shift_id=shift_schedule.shift_id,
                            attendance_day=shift_schedule.day,
                            attendance_date=activity.attendance_date,
                        )
                        .order_by("pk")
                        .first()
                    )

                    if not attendance:
                        continue

                    checkout_date = activity.attendance_date
                    if shift_schedule.is_night_shift:
                        checkout_date += datetime.timedelta(days=1)

                    checkout_at = timezone.make_aware(
                        datetime.datetime.combine(
                            checkout_date,
                            shift_schedule.auto_punch_out_time,
                        ),
                        timezone=timezone.get_current_timezone(),
                    )

                    if checkout_at >= current_time:
                        continue

                    clock_out(
                        Request(
                            user=attendance.employee_id.employee_user_id,
                            date=checkout_date,
                            time=shift_schedule.auto_punch_out_time,
                            datetime=checkout_at,
                        )
                    )
            except Exception as error:
                logger.exception(
                    "Automatic attendance checkout failed for activity %s: %s",
                    activity.pk,
                    error,
                )


def create_work_record():
    from attendance.models import WorkRecords
    from employee.models import Employee

    current_date = datetime.datetime.today().date()
    work_records = WorkRecords.objects.filter(date=current_date).values_list(
        "employee_id", flat=True
    )
    employees = Employee.objects.exclude(id__in=work_records)
    records_to_create = []

    for employee in employees:
        try:
            joining_date = employee.employee_work_info.date_joining
            if not joining_date or current_date < joining_date:
                continue

            shift_schedule = employee.get_shift_schedule()
            if shift_schedule is None:
                continue

            shift = employee.get_shift()
            record = WorkRecords(
                employee_id=employee,
                date=current_date,
                work_record_type="DFT",
                shift_id=shift,
                message="",
            )
            records_to_create.append(record)
        except Exception as e:
            logger.error(f"Error preparing work record for {employee}: {e}")

    if records_to_create:
        try:
            WorkRecords.objects.bulk_create(records_to_create)
            print(f"Created {len(records_to_create)} work records for {current_date}.")
        except Exception as e:
            logger.error(f"Failed to bulk create work records: {e}")
    else:
        print(f"No new work records to create for {current_date}.")


if not any(
    cmd in sys.argv
    for cmd in ["makemigrations", "migrate", "compilemessages", "flush", "shell", "test"]
):
    """
    Initializes and starts background tasks using APScheduler when the server is running.
    """
    scheduler = BackgroundScheduler(timezone=pytz.timezone(settings.TIME_ZONE))

    scheduler.add_job(
        create_work_record, "interval", minutes=30, misfire_grace_time=3600 * 3
    )
    scheduler.add_job(
        auto_checkout_attendance,
        "interval",
        minutes=1,
        id="auto_checkout_attendance",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        create_work_record,
        "cron",
        hour=0,
        minute=30,
        misfire_grace_time=3600 * 9,
        id="create_daily_work_record",
        replace_existing=True,
    )

    scheduler.start()
