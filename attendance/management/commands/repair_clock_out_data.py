"""Repair clock-in/clock-out data left behind by the attendance-date bug.

While punches were misdated to the previous day (shift-schedule AM/PM typos),
three kinds of bad data were produced:

1. Auto-checkout closed an activity at 23:59 of the wrong (earlier) date, so
   its clock-out is before its clock-in. The employee's real clock-out was
   then stored in a separate zero-length activity (in == out).
2. Duplicate manual import rows were added for some of those days.
3. Attendance rows for the re-dated days are missing or stale.

Runs as a dry run unless --apply is given.
"""

from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from attendance.methods.utils import (
    recalculate_attendance_for_day,
    shift_schedule_with_weekday_fallback,
)
from attendance.models import (
    Attendance,
    AttendanceActivity,
    AttendanceLateComeEarlyOut,
)
from base.models import SHIFT_MINIMUM_WORKING_HOUR, EmployeeShiftDay

# Manual imports (no selfies) that duplicate real website punches.
DUPLICATE_IMPORT_IDS = [10359, 10360, 5128]

# Manual imports that are the only record of their day but carry the
# previous day's attendance date.
ALIGN_IMPORT_IDS = [10361]

# Attendance rows found on 2026-11-25 that only held a misdated next-day punch.
GHOST_ATTENDANCE_IDS = [
    8132, 9320, 10373, 5563, 4698, 3857, 6056, 7101, 4324, 7249, 6034, 4974,
]

# Activities re-dated by `repair_attendance_activity_dates` on 2026-11-25.
REDATED_ACTIVITY_IDS = [
    9186, 10493, 11602, 5841, 6041, 6227, 6413, 6841, 7028, 7240, 7428, 7627,
    8033, 8203, 8401, 8596, 5734, 5920, 5922, 6305, 6492, 6639, 6640, 6852,
    7013, 7229, 7376, 7584, 7767, 7892, 7972, 8109, 8296, 8493, 5336, 7877,
    6567, 7791, 8795, 7456, 6586, 7807, 8887, 8890, 8892, 8894, 10228, 10231,
    11380, 5401, 8119, 10361,
]

BROKEN_TOLERANCE = timedelta(seconds=60)
ZERO_LENGTH = timedelta(seconds=60)

CLOCK_OUT_FIELDS = [
    "clock_out",
    "clock_out_date",
    "out_datetime",
    "clock_out_selfie",
    "clock_out_latitude",
    "clock_out_longitude",
    "clock_out_gps_address",
    "latitude",
    "longitude",
    "gps_address",
    "location_verified",
]


def activity_in(activity):
    if activity.clock_in_date and activity.clock_in:
        return datetime.combine(activity.clock_in_date, activity.clock_in)
    return None


def activity_out(activity):
    if activity.clock_out_date and activity.clock_out:
        return datetime.combine(activity.clock_out_date, activity.clock_out)
    return None


def is_broken(activity):
    start, end = activity_in(activity), activity_out(activity)
    return bool(start and end and end < start - BROKEN_TOLERANCE)


def is_zero_length_clock_out(activity):
    start, end = activity_in(activity), activity_out(activity)
    return bool(
        start
        and end
        and abs(end - start) < ZERO_LENGTH
        and not activity.clock_in_selfie
    )


def day_obj(value):
    return EmployeeShiftDay.objects.filter(day=value.strftime("%A").lower()).first()


def aware(value):
    return timezone.make_aware(value, timezone.get_current_timezone())


class Command(BaseCommand):
    help = (
        "Repair activities whose clock-out is before their clock-in, delete "
        "known duplicate imports, and rebuild the affected Attendance rows."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the changes. Without this flag nothing is saved.",
        )

    def handle(self, *args, **options):
        with transaction.atomic():
            touched = set()
            self._delete_duplicate_imports(touched)
            self._align_imports(touched)
            self._repair_broken_activities(touched)
            self._delete_ghost_attendance(touched)
            self._rebuild_attendance(touched)
            if not options["apply"]:
                transaction.set_rollback(True)
                self.stdout.write(
                    self.style.WARNING("Dry run - rolled back. Re-run with --apply.")
                )
            else:
                self.stdout.write(self.style.SUCCESS("Applied."))

    # -- step a ---------------------------------------------------------
    def _delete_duplicate_imports(self, touched):
        self.stdout.write("Duplicate imports:")
        for activity in AttendanceActivity._base_manager.filter(
            id__in=DUPLICATE_IMPORT_IDS
        ):
            if (
                activity.source != "import"
                or activity.clock_in_selfie
                or activity.clock_out_selfie
            ):
                self.stdout.write(
                    f"  skip #{activity.id}: no longer a selfie-less import"
                )
                continue
            touched.add((activity.employee_id_id, activity.attendance_date))
            if activity.clock_in_date:
                touched.add((activity.employee_id_id, activity.clock_in_date))
            self.stdout.write(
                f"  delete #{activity.id} {activity.employee_id.employee_no} "
                f"{activity_in(activity)} -> {activity_out(activity)}"
            )
            activity.delete()

    def _align_imports(self, touched):
        """Give the remaining misdated import rows their clock-in date."""
        for activity in AttendanceActivity._base_manager.filter(
            id__in=ALIGN_IMPORT_IDS
        ).exclude(attendance_date=F("clock_in_date")):
            self.stdout.write(
                f"  align import #{activity.id} {activity.employee_id.employee_no}: "
                f"{activity.attendance_date} -> {activity.clock_in_date}"
            )
            activity.attendance_date = activity.clock_in_date
            activity.shift_day = day_obj(activity.clock_in_date)
            activity.save(update_fields=["attendance_date", "shift_day"])
            touched.add((activity.employee_id_id, activity.attendance_date))

    # -- step b ---------------------------------------------------------
    def _repair_broken_activities(self, touched):
        self.stdout.write("Activities with clock-out before clock-in:")
        candidates = AttendanceActivity._base_manager.filter(
            clock_out__isnull=False,
            clock_out_date__isnull=False,
            clock_in_date__isnull=False,
        ).select_related("employee_id")
        broken = [activity for activity in candidates if is_broken(activity)]

        for activity in broken:
            employee_no = activity.employee_id.employee_no
            before = f"{activity_in(activity)} -> {activity_out(activity)}"
            following = (
                AttendanceActivity._base_manager.filter(
                    employee_id_id=activity.employee_id_id,
                    clock_in_date=activity.clock_in_date,
                )
                .exclude(pk=activity.pk)
                .filter(clock_in__gt=activity.clock_in)
                .order_by("clock_in", "id")
                .first()
            )

            if following and is_zero_length_clock_out(following):
                for field in CLOCK_OUT_FIELDS:
                    value = getattr(following, field)
                    if field.endswith("selfie") and not value:
                        continue
                    if value is None and field != "out_datetime":
                        continue
                    setattr(activity, field, value)
                if not activity.out_datetime:
                    activity.out_datetime = aware(activity_out(activity))
                activity.is_auto_punch_out = False
                activity.save()
                action = f"merged clock-out from #{following.id} (deleted)"
                following.delete()
            elif following:
                activity.clock_out_date = following.clock_in_date
                activity.clock_out = following.clock_in
                activity.out_datetime = following.in_datetime or aware(
                    activity_in(following)
                )
                activity.is_auto_punch_out = False
                activity.save()
                action = f"closed at next clock-in #{following.id}"
            else:
                end_time = self._scheduled_end(activity)
                if not end_time:
                    self.stdout.write(
                        f"  #{activity.id} {employee_no}: {before} - no schedule "
                        "end time found, left unchanged"
                    )
                    continue
                activity.clock_out_date = activity.clock_in_date
                activity.clock_out = end_time
                activity.out_datetime = aware(activity_out(activity))
                activity.is_auto_punch_out = True
                activity.save()
                action = "closed at scheduled end time (auto)"

            touched.add((activity.employee_id_id, activity.attendance_date))
            self.stdout.write(
                f"  #{activity.id} {employee_no}: {before} => "
                f"{activity_in(activity)} -> {activity_out(activity)} ({action})"
            )

    def _scheduled_end(self, activity):
        attendance = Attendance._base_manager.filter(
            employee_id_id=activity.employee_id_id,
            attendance_date=activity.attendance_date,
        ).first()
        shift = getattr(attendance, "shift_id", None)
        if not shift:
            work_info = getattr(activity.employee_id, "employee_work_info", None)
            shift = getattr(work_info, "shift_id", None)
        schedule = shift_schedule_with_weekday_fallback(
            day_obj(activity.clock_in_date), shift
        )
        end_time = getattr(schedule, "end_time", None)
        if end_time and end_time > activity.clock_in:
            return end_time
        return None

    # -- step d ---------------------------------------------------------
    def _delete_ghost_attendance(self, touched):
        """Delete Attendance rows that only held a misdated next-day punch.

        A ghost row has no activities on its own date, its clock-in or
        clock-out points into the next day, and the next day already has
        its own Attendance row.
        """
        self.stdout.write("Ghost attendance rows (no punches on their date):")
        ghosts = (
            Attendance._base_manager.annotate(
                has_activity=Exists(
                    AttendanceActivity._base_manager.filter(
                        employee_id=OuterRef("employee_id"),
                        attendance_date=OuterRef("attendance_date"),
                    )
                )
            )
            .filter(id__in=GHOST_ATTENDANCE_IDS, has_activity=False)
            .filter(
                Q(attendance_clock_in_date__gt=F("attendance_date"))
                | Q(attendance_clock_out_date__gt=F("attendance_date"))
            )
            .select_related("employee_id")
        )
        for ghost in ghosts:
            next_date = ghost.attendance_date + timedelta(days=1)
            next_row = Attendance._base_manager.filter(
                employee_id_id=ghost.employee_id_id, attendance_date=next_date
            ).first()
            if not next_row:
                # The rebuild step moves it onto the next day instead.
                touched.add((ghost.employee_id_id, next_date))
                continue
            if ghost.notes and ghost.notes not in (next_row.notes or ""):
                next_row.notes = (
                    f"{next_row.notes}\n{ghost.notes}" if next_row.notes else ghost.notes
                )
                next_row.save(update_fields=["notes"])
            late_come = AttendanceLateComeEarlyOut._base_manager.filter(
                attendance_id=ghost
            )
            late_count = late_come.count()
            late_come.delete()
            self.stdout.write(
                f"  delete #{ghost.id} {ghost.employee_id.employee_no} "
                f"{ghost.attendance_date} (in {ghost.attendance_clock_in_date} "
                f"{ghost.attendance_clock_in}, worked {ghost.attendance_worked_hour}, "
                f"{late_count} late-come record(s)"
                f"{', note moved' if ghost.notes else ''})"
            )
            ghost.delete()
            touched.add((ghost.employee_id_id, next_date))

    # -- step c ---------------------------------------------------------
    def _rebuild_attendance(self, touched):
        for activity in AttendanceActivity._base_manager.filter(
            id__in=REDATED_ACTIVITY_IDS
        ):
            touched.add((activity.employee_id_id, activity.attendance_date))
        missing = AttendanceActivity._base_manager.annotate(
            has_attendance=Exists(
                Attendance._base_manager.filter(
                    employee_id=OuterRef("employee_id"),
                    attendance_date=OuterRef("attendance_date"),
                )
            )
        ).filter(has_attendance=False, attendance_date__isnull=False)
        for activity in missing:
            touched.add((activity.employee_id_id, activity.attendance_date))

        self.stdout.write(f"Rebuilding attendance for {len(touched)} day(s):")
        suspicious = []
        for employee_id, attendance_date in sorted(touched):
            if not attendance_date:
                continue
            previous_date = attendance_date - timedelta(days=1)
            day_activities = AttendanceActivity._base_manager.filter(
                employee_id_id=employee_id, attendance_date=attendance_date
            ).order_by("clock_in_date", "clock_in", "id")
            previous_has_activities = AttendanceActivity._base_manager.filter(
                employee_id_id=employee_id, attendance_date=previous_date
            ).exists()
            attendance = Attendance._base_manager.filter(
                employee_id_id=employee_id, attendance_date=attendance_date
            ).first()
            previous = Attendance._base_manager.filter(
                employee_id_id=employee_id, attendance_date=previous_date
            ).first()
            first = day_activities.first()

            if first and not attendance:
                if previous and not previous_has_activities:
                    previous.attendance_date = attendance_date
                    previous.attendance_day = day_obj(attendance_date)
                    previous.attendance_clock_in_date = first.clock_in_date
                    previous.attendance_clock_in = first.clock_in
                    previous.save()
                    attendance, previous = previous, None
                    action = "moved previous-day row"
                else:
                    work_info = first.employee_id.employee_work_info
                    attendance = Attendance(
                        employee_id=first.employee_id,
                        attendance_date=attendance_date,
                        shift_id=(previous.shift_id if previous else work_info.shift_id),
                        work_type_id=work_info.work_type_id,
                        attendance_day=day_obj(attendance_date),
                        attendance_clock_in_date=first.clock_in_date,
                        attendance_clock_in=first.clock_in,
                        minimum_hour=SHIFT_MINIMUM_WORKING_HOUR,
                    )
                    attendance.save()
                    action = "created"
            else:
                action = "recalculated"

            rows = []
            if attendance and first:
                recalculate_attendance_for_day(attendance)
                rows.append((attendance, action))
            if previous and previous_has_activities:
                recalculate_attendance_for_day(previous)
                rows.append((previous, "recalculated"))

            for row, row_action in rows:
                row.refresh_from_db()
                worked = row.attendance_worked_hour or "00:00"
                hours = int(worked.lstrip("-").split(":")[0])
                flag = ""
                if worked.startswith("-") or hours > 16:
                    flag = "  <-- CHECK"
                    suspicious.append(row)
                self.stdout.write(
                    f"  {row.employee_id.employee_no} {row.attendance_date} #{row.id} "
                    f"[{row_action}] in {row.attendance_clock_in_date} "
                    f"{row.attendance_clock_in} out {row.attendance_clock_out_date} "
                    f"{row.attendance_clock_out} worked {worked}{flag}"
                )
        self.stdout.write(f"Rows needing manual review: {len(suspicious)}")
