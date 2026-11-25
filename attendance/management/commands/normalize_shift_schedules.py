"""Normalize employee shift schedules.

1. Correct AM/PM entry mistakes that turned a 9-hour day shift into a
   21-hour "overnight" shift (for example 23:00 -> 20:00 instead of
   11:00 -> 20:00). Such rows make morning clock-ins land on the previous
   attendance date.
2. Set every schedule's minimum working hours to 09:00.

Runs as a dry run unless --apply is given.
"""

from datetime import time

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.dateparse import parse_date

from attendance.models import Attendance
from base.models import (
    SHIFT_MINIMUM_WORKING_HOUR,
    EmployeeShiftSchedule,
    shift_span_seconds,
)

TYPO_SPAN_SECONDS = 21 * 60 * 60


def corrected_times(start_time, end_time):
    """Return (start, end) for a 21h AM/PM typo, or None if not a typo."""
    if not start_time or not end_time or start_time <= end_time:
        return None
    if shift_span_seconds(start_time, end_time) != TYPO_SPAN_SECONDS:
        return None
    if start_time.hour >= 12:
        # Start was entered as PM instead of AM: 23:00 -> 11:00.
        return time(start_time.hour - 12, start_time.minute, start_time.second), end_time
    # End was entered as AM instead of PM: 06:00 -> 18:00.
    return start_time, time(end_time.hour + 12, end_time.minute, end_time.second)


class Command(BaseCommand):
    help = (
        "Fix AM/PM typos in shift schedules (21-hour overnight spans) and set "
        "every schedule's minimum working hours to 09:00."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the changes. Without this flag only a report is printed.",
        )
        parser.add_argument(
            "--update-attendance-from",
            dest="attendance_from",
            metavar="YYYY-MM-DD",
            help=(
                "Also set Attendance.minimum_hour to 09:00 for attendance dates "
                "on or after this date."
            ),
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        attendance_from = None
        if options.get("attendance_from"):
            attendance_from = parse_date(options["attendance_from"])
            if attendance_from is None:
                raise CommandError("--update-attendance-from must be YYYY-MM-DD.")

        schedules = EmployeeShiftSchedule._base_manager.select_related(
            "shift_id", "day"
        ).order_by("shift_id__employee_shift", "day_id", "id")

        typo_fixes = []
        for schedule in schedules:
            fixed = corrected_times(schedule.start_time, schedule.end_time)
            if fixed:
                typo_fixes.append((schedule, fixed))

        self.stdout.write(f"AM/PM typo schedules: {len(typo_fixes)}")
        for schedule, (start, end) in typo_fixes:
            self.stdout.write(
                f"  #{schedule.id} {schedule.shift_id} {schedule.day.day}: "
                f"{schedule.start_time:%H:%M}->{schedule.end_time:%H:%M} "
                f"=> {start:%H:%M}->{end:%H:%M}"
            )

        not_nine = EmployeeShiftSchedule._base_manager.exclude(
            minimum_working_hour=SHIFT_MINIMUM_WORKING_HOUR
        )
        self.stdout.write(
            f"Schedules with minimum working hours != {SHIFT_MINIMUM_WORKING_HOUR}: "
            f"{not_nine.count()}"
        )

        attendances = None
        if attendance_from:
            attendances = Attendance._base_manager.filter(
                attendance_date__gte=attendance_from
            ).exclude(minimum_hour=SHIFT_MINIMUM_WORKING_HOUR)
            self.stdout.write(
                f"Attendance rows from {attendance_from} with minimum hour != "
                f"{SHIFT_MINIMUM_WORKING_HOUR}: {attendances.count()}"
            )

        if not apply:
            self.stdout.write(self.style.WARNING("Dry run. Re-run with --apply to save."))
            return

        with transaction.atomic():
            for schedule, (start, end) in typo_fixes:
                schedule.start_time = start
                schedule.end_time = end
                # save() recomputes is_night_shift and forces 09:00.
                schedule.save()
            updated = not_nine.update(minimum_working_hour=SHIFT_MINIMUM_WORKING_HOUR)
            attendance_updated = (
                attendances.update(minimum_hour=SHIFT_MINIMUM_WORKING_HOUR)
                if attendances is not None
                else 0
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Fixed {len(typo_fixes)} typo schedules, set {updated} schedules "
                f"to {SHIFT_MINIMUM_WORKING_HOUR}, updated {attendance_updated} "
                "attendance rows."
            )
        )
