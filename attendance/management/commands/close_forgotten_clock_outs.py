"""Close punches whose clock-out was forgotten, instead of days later.

Before the portal fix, a punch left open (no clock-out, no auto-checkout) was
closed by the employee's next clock-in, possibly days later. Example: in
Nov 19 08:24, "out" Nov 23 08:13, 95:49 worked. The real clock-out is
unknown, so the punch is closed at its own clock-in (zero hours) and flagged
as an automatic punch-out. Attendance views then show Clock Out and Worked
Hours as blank.

Runs as a dry run unless --apply is given.
"""

from datetime import datetime

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from attendance.methods.utils import recalculate_attendance_for_day
from attendance.models import Attendance, AttendanceActivity


def find_forgotten_clock_outs():
    candidates = (
        AttendanceActivity._base_manager.filter(
            clock_in_date__isnull=False,
            clock_in__isnull=False,
            clock_out__isnull=False,
            clock_out_date__gt=F("attendance_date"),
        )
        .filter(clock_out_selfie__in=["", None])
        .select_related("employee_id")
    )
    found = []
    for activity in candidates:
        if (activity.activity_type or "work") != "work":
            continue
        closed_by_next_clock_in = (
            AttendanceActivity._base_manager.filter(
                employee_id_id=activity.employee_id_id,
                clock_in_date=activity.clock_out_date,
                clock_in=activity.clock_out,
            )
            .exclude(pk=activity.pk)
            .exclude(attendance_date=activity.attendance_date)
            .exists()
        )
        if closed_by_next_clock_in:
            found.append(activity)
    return found


class Command(BaseCommand):
    help = (
        "Close punches that were left open and later closed by the next "
        "clock-in: Clock Out and Worked Hours become blank."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the changes. Without this flag nothing is saved.",
        )

    def handle(self, *args, **options):
        with transaction.atomic():
            activities = find_forgotten_clock_outs()
            self.stdout.write(f"Forgotten clock-outs: {len(activities)}")
            days = set()
            for activity in activities:
                self.stdout.write(
                    f"  #{activity.id} {activity.employee_id.employee_no} "
                    f"{activity.attendance_date}: in {activity.clock_in:%H:%M}, "
                    f"out {activity.clock_out_date} {activity.clock_out:%H:%M} => blank"
                )
                activity.clock_out = activity.clock_in
                activity.clock_out_date = activity.clock_in_date
                activity.out_datetime = activity.in_datetime or timezone.make_aware(
                    datetime.combine(activity.clock_in_date, activity.clock_in),
                    timezone.get_current_timezone(),
                )
                activity.is_auto_punch_out = True
                activity.save()
                days.add((activity.employee_id_id, activity.attendance_date))

            for employee_id, attendance_date in sorted(days):
                attendance = Attendance._base_manager.filter(
                    employee_id_id=employee_id, attendance_date=attendance_date
                ).first()
                if attendance:
                    recalculate_attendance_for_day(attendance)

            if not options["apply"]:
                transaction.set_rollback(True)
                self.stdout.write(
                    self.style.WARNING("Dry run - rolled back. Re-run with --apply.")
                )
            else:
                self.stdout.write(self.style.SUCCESS("Applied."))
