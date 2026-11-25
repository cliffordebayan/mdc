"""Turn end-of-day "repeat Clock In" punches into the Clock Out they meant.

Before the portal fix, pressing Clock In while already clocked in closed the
open punch (A) and started a new one (B) that auto-checkout later closed at
23:59. The B press was really the employee's clock-out, so A's clock-out
keeps B's time, B's selfie/GPS become A's clock-out selfie/GPS, and B is
deleted.

Runs as a dry run unless --apply is given.
"""

from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from attendance.methods.utils import recalculate_attendance_for_day
from attendance.models import (
    Attendance,
    AttendanceActivity,
    AttendancePortalMultiPunchEmployee,
)

AUTO_CLOSE_TIME = time(23, 59)
CHAIN_TOLERANCE = timedelta(seconds=2)


def _in_dt(activity):
    return datetime.combine(activity.clock_in_date, activity.clock_in)


def _out_dt(activity):
    if activity.clock_out_date and activity.clock_out:
        return datetime.combine(activity.clock_out_date, activity.clock_out)
    return None


def _is_work(activity):
    return (activity.activity_type or "work") == "work"


def find_repeat_clock_in_pairs(today=None):
    """Return [(closed_activity, repeat_clock_in_activity), ...]."""
    today = today or timezone.localdate()
    multi_punch_ids = set(
        AttendancePortalMultiPunchEmployee._base_manager.filter(
            is_active=True
        ).values_list("employee_id_id", flat=True)
    )
    candidates = (
        AttendanceActivity._base_manager.filter(
            clock_in_date__isnull=False,
            clock_in__isnull=False,
            clock_out__isnull=False,
            attendance_date__lt=today,
        )
        .exclude(employee_id_id__in=multi_punch_ids)
        .select_related("employee_id")
    )
    pairs = []
    for repeat in candidates:
        auto_closed = repeat.is_auto_punch_out or repeat.clock_out == AUTO_CLOSE_TIME
        if not auto_closed or repeat.clock_out_selfie or not _is_work(repeat):
            continue
        day = list(
            AttendanceActivity._base_manager.filter(
                employee_id_id=repeat.employee_id_id,
                attendance_date=repeat.attendance_date,
            ).order_by("clock_in_date", "clock_in", "id")
        )
        if day[-1].pk != repeat.pk:
            continue  # not the last punch of the day
        earlier = [a for a in day if a.pk != repeat.pk and _out_dt(a)]
        closed = next(
            (
                a
                for a in reversed(earlier)
                if abs(_out_dt(a) - _in_dt(repeat)) <= CHAIN_TOLERANCE
            ),
            None,
        )
        if not closed or closed.clock_out_selfie or not _is_work(closed):
            continue
        pairs.append((closed, repeat))
    return pairs


class Command(BaseCommand):
    help = (
        "Convert end-of-day repeat Clock In punches (auto-closed at 23:59) "
        "into the Clock Out of the earlier punch."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the changes. Without this flag nothing is saved.",
        )

    def handle(self, *args, **options):
        with transaction.atomic():
            pairs = find_repeat_clock_in_pairs()
            self.stdout.write(f"Repeat Clock In pairs: {len(pairs)}")
            days = set()
            for closed, repeat in pairs:
                self.stdout.write(
                    f"  {closed.employee_id.employee_no} {closed.attendance_date}: "
                    f"#{closed.id} {closed.clock_in:%H:%M}->{closed.clock_out:%H:%M} + "
                    f"#{repeat.id} {repeat.clock_in:%H:%M}->{repeat.clock_out:%H:%M} "
                    f"=> {closed.clock_in:%H:%M}->{repeat.clock_in:%H:%M}"
                )
                closed.clock_out_date = repeat.clock_in_date
                closed.clock_out = repeat.clock_in
                closed.out_datetime = repeat.in_datetime or timezone.make_aware(
                    _in_dt(repeat), timezone.get_current_timezone()
                )
                if repeat.clock_in_selfie:
                    closed.clock_out_selfie = repeat.clock_in_selfie.name
                closed.clock_out_latitude = repeat.clock_in_latitude
                closed.clock_out_longitude = repeat.clock_in_longitude
                closed.clock_out_gps_address = repeat.clock_in_gps_address
                if repeat.clock_in_latitude is not None:
                    closed.latitude = repeat.clock_in_latitude
                    closed.longitude = repeat.clock_in_longitude
                    closed.gps_address = repeat.clock_in_gps_address
                closed.is_auto_punch_out = False
                closed.save()
                repeat.delete()
                days.add((closed.employee_id_id, closed.attendance_date))

            self.stdout.write(f"Recalculating {len(days)} attendance day(s):")
            for employee_id, attendance_date in sorted(days):
                attendance = Attendance._base_manager.filter(
                    employee_id_id=employee_id, attendance_date=attendance_date
                ).first()
                if not attendance:
                    continue
                before = attendance.attendance_worked_hour
                recalculate_attendance_for_day(attendance)
                attendance.refresh_from_db()
                self.stdout.write(
                    f"  {attendance.employee_id.employee_no} {attendance_date}: "
                    f"out {attendance.attendance_clock_out}, worked {before} -> "
                    f"{attendance.attendance_worked_hour}"
                )

            if not options["apply"]:
                transaction.set_rollback(True)
                self.stdout.write(
                    self.style.WARNING("Dry run - rolled back. Re-run with --apply.")
                )
            else:
                self.stdout.write(self.style.SUCCESS("Applied."))
