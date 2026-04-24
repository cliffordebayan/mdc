import datetime
import sys

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from django.conf import settings

from base.backends import logger


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
    for cmd in ["makemigrations", "migrate", "compilemessages", "flush", "shell"]
):
    """
    Initializes and starts background tasks using APScheduler when the server is running.
    """
    scheduler = BackgroundScheduler(timezone=pytz.timezone(settings.TIME_ZONE))

    scheduler.add_job(
        create_work_record, "interval", minutes=30, misfire_grace_time=3600 * 3
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
