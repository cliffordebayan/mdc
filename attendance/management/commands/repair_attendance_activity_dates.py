from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F
from django.utils.dateparse import parse_date
from django.utils import timezone

from attendance.methods.utils import (
    calculate_worked_hours,
    get_effective_attendance_schedule,
)
from attendance.models import Attendance, AttendanceActivity
from base.models import EmployeeShiftDay


class Command(BaseCommand):
    help = (
        "Repair regular-shift attendance and activity dates that were shifted "
        "away from the employee's local clock-in date."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply the repairs. Without this flag, only a dry-run report is produced.",
        )
        parser.add_argument(
            "--from-date",
            dest="from_date",
            help="Only inspect clock-in dates on or after YYYY-MM-DD.",
        )
        parser.add_argument(
            "--to-date",
            dest="to_date",
            help="Only inspect clock-in dates on or before YYYY-MM-DD.",
        )
        parser.add_argument(
            "--employee-no",
            dest="employee_no",
            help="Only inspect the employee with this employee number.",
        )

    def handle(self, *args, **options):
        from_date = self._parse_option_date(options.get("from_date"), "--from-date")
        to_date = self._parse_option_date(options.get("to_date"), "--to-date")
        if from_date and to_date and from_date > to_date:
            raise CommandError("--from-date cannot be later than --to-date.")

        activities = (
            AttendanceActivity._base_manager.filter(
                clock_in_date__isnull=False,
            )
            .exclude(attendance_date=F("clock_in_date"))
            .select_related("employee_id", "shift_day")
            .order_by("employee_id_id", "clock_in_date", "id")
        )
        employee_no = options.get("employee_no")
        if employee_no:
            activities = activities.filter(employee_id__employee_no=employee_no)
        if from_date:
            activities = activities.filter(clock_in_date__gte=from_date)
        if to_date:
            activities = activities.filter(clock_in_date__lte=to_date)

        repairs = []
        repairs_without_attendance = []
        skipped_overnight = []
        skipped_without_target = []

        for activity in activities.iterator():
            target_attendance = (
                Attendance._base_manager.select_related("attendance_day", "shift_id")
                .filter(
                    employee_id_id=activity.employee_id_id,
                    attendance_date=activity.clock_in_date,
                )
                .first()
            )
            if not target_attendance:
                if (
                    activity.clock_in_date
                    and activity.attendance_date
                    and activity.clock_in_date == activity.attendance_date + timedelta(days=1)
                    and self._local_created_date(activity) == activity.clock_in_date
                ):
                    source_attendance = (
                        Attendance._base_manager.select_related(
                            "attendance_day", "shift_id"
                        )
                        .filter(
                            employee_id_id=activity.employee_id_id,
                            attendance_date=activity.attendance_date,
                        )
                        .first()
                    )
                    if not self._is_legitimate_overnight(
                        activity, source_attendance
                    ):
                        target_day = EmployeeShiftDay.objects.filter(
                            day=activity.clock_in_date.strftime("%A").lower()
                        ).first()
                        repairs_without_attendance.append((activity, target_day))
                        continue
                skipped_without_target.append(activity)
                continue

            source_attendance = (
                Attendance._base_manager.select_related("attendance_day", "shift_id")
                .filter(
                    employee_id_id=activity.employee_id_id,
                    attendance_date=activity.attendance_date,
                )
                .first()
            )
            if self._is_legitimate_overnight(
                activity, source_attendance, target_attendance
            ):
                skipped_overnight.append(activity)
                continue

            repairs.append((activity, target_attendance))

        attendance_repairs = []
        skipped_attendance_overnight = []
        skipped_attendance_collision = []
        attendances = Attendance._base_manager.filter(
            attendance_clock_in_date__isnull=False,
        ).select_related("attendance_day", "shift_id")
        if employee_no:
            attendances = attendances.filter(employee_id__employee_no=employee_no)

        for attendance in attendances.iterator():
            old_date = attendance.attendance_date
            target_date = self._local_created_date(attendance)
            if not old_date or not target_date:
                continue
            if target_date != old_date + timedelta(days=1):
                continue
            if attendance.attendance_clock_in_date != old_date:
                continue
            if from_date and target_date < from_date:
                continue
            if to_date and target_date > to_date:
                continue

            activities_for_attendance = list(
                AttendanceActivity._base_manager.filter(
                    employee_id_id=attendance.employee_id_id,
                    attendance_date=old_date,
                ).order_by("id")
            )
            # A consistently shifted row must have at least one activity and
            # every activity date must be either the saved date or the local
            # creation date. This covers rows where clock_in_date was already
            # corrected while attendance_date was not.
            if not activities_for_attendance or any(
                activity.clock_in_date not in (old_date, target_date)
                for activity in activities_for_attendance
            ):
                continue

            if any(
                self._is_legitimate_overnight(activity, attendance)
                for activity in activities_for_attendance
            ):
                skipped_attendance_overnight.append(attendance)
                continue

            if Attendance._base_manager.filter(
                employee_id_id=attendance.employee_id_id,
                attendance_date=target_date,
            ).exists():
                skipped_attendance_collision.append(attendance)
                continue

            target_day = EmployeeShiftDay.objects.filter(
                day=target_date.strftime("%A").lower()
            ).first()
            attendance_repairs.append(
                (attendance, target_date, target_day, activities_for_attendance)
            )

        mode = "Applying" if options["apply"] else "Dry run"
        eligible_count = (
            len(repairs) + len(repairs_without_attendance) + len(attendance_repairs)
        )
        self.stdout.write(
            f"{mode}: {eligible_count} eligible attendance date repair(s)."
        )

        for activity, target_attendance in repairs:
            self.stdout.write(
                "  Activity {activity_id}: {old_date} -> {new_date}"
                .format(
                    activity_id=activity.pk,
                    old_date=activity.attendance_date,
                    new_date=target_attendance.attendance_date,
                )
            )

        for activity, _target_day in repairs_without_attendance:
            self.stdout.write(
                "  Activity {activity_id}: {old_date} -> {new_date}".format(
                    activity_id=activity.pk,
                    old_date=activity.attendance_date,
                    new_date=activity.clock_in_date,
                )
            )

        for attendance, target_date, _target_day, _activities in attendance_repairs:
            self.stdout.write(
                "  Attendance {attendance_id}: {old_date} -> {new_date}".format(
                    attendance_id=attendance.pk,
                    old_date=attendance.attendance_date,
                    new_date=target_date,
                )
            )

        if options["apply"] and eligible_count:
            affected_attendances = {}
            with transaction.atomic():
                for activity, target_attendance in repairs:
                    activity.attendance_date = target_attendance.attendance_date
                    if target_attendance.attendance_day:
                        activity.shift_day = target_attendance.attendance_day
                    activity.save(update_fields=["attendance_date", "shift_day"])
                    affected_attendances[target_attendance.pk] = target_attendance

                for activity, target_day in repairs_without_attendance:
                    activity.attendance_date = activity.clock_in_date
                    update_fields = ["attendance_date"]
                    if target_day:
                        activity.shift_day = target_day
                        update_fields.append("shift_day")
                    activity.save(update_fields=update_fields)

                for attendance, target_date, target_day, activities_for_attendance in attendance_repairs:
                    old_date = attendance.attendance_date
                    for activity in activities_for_attendance:
                        activity.attendance_date = target_date
                        update_fields = ["attendance_date"]
                        if activity.clock_in_date == old_date:
                            activity.clock_in_date = target_date
                            update_fields.append("clock_in_date")
                        if activity.clock_out_date == old_date:
                            activity.clock_out_date = target_date
                            update_fields.append("clock_out_date")
                        if target_day:
                            activity.shift_day = target_day
                            update_fields.append("shift_day")
                        activity.save(update_fields=update_fields)

                    attendance_fields = {"attendance_date": target_date}
                    if attendance.attendance_clock_in_date == old_date:
                        attendance_fields["attendance_clock_in_date"] = target_date
                    if attendance.attendance_clock_out_date == old_date:
                        attendance_fields["attendance_clock_out_date"] = target_date
                    if target_day:
                        attendance_fields["attendance_day"] = target_day
                    Attendance._base_manager.filter(pk=attendance.pk).update(
                        **attendance_fields
                    )
                    attendance.attendance_date = target_date
                    affected_attendances[attendance.pk] = attendance

                for attendance in affected_attendances.values():
                    Attendance._base_manager.filter(pk=attendance.pk).update(
                        attendance_worked_hour=calculate_worked_hours(
                            attendance.employee_id,
                            attendance.attendance_date,
                        )
                    )

            self.stdout.write(
                self.style.SUCCESS(
                    f"Applied {eligible_count} attendance date repair(s)."
                )
            )

        self.stdout.write(
            f"Skipped {len(skipped_overnight)} legitimate overnight activity row(s)."
        )
        self.stdout.write(
            f"Skipped {len(skipped_without_target)} row(s) without a matching attendance date."
        )
        self.stdout.write(
            f"Skipped {len(skipped_attendance_overnight)} legitimate overnight attendance row(s)."
        )
        self.stdout.write(
            f"Skipped {len(skipped_attendance_collision)} attendance row(s) because the target date already exists."
        )

    @staticmethod
    def _parse_option_date(value, option_name):
        if not value:
            return None
        parsed = parse_date(value)
        if parsed is None:
            raise CommandError(f"{option_name} must use YYYY-MM-DD format.")
        return parsed

    @staticmethod
    def _is_night_shift(attendance):
        if not attendance:
            return False
        try:
            return bool(attendance.is_night_shift())
        except (AttributeError, TypeError, ValueError):
            return False

    @classmethod
    def _is_legitimate_overnight(cls, activity, *attendances):
        if not activity or not activity.attendance_date or not activity.clock_in_date:
            return False
        if activity.clock_in_date <= activity.attendance_date:
            return False

        night_attendances = []
        schedules = []
        for attendance in attendances:
            if not cls._is_night_shift(attendance):
                continue
            night_attendances.append(attendance)
            try:
                schedule = get_effective_attendance_schedule(attendance)
            except (AttributeError, TypeError, ValueError):
                schedule = None
            if schedule and getattr(schedule, "end_time", None):
                schedules.append(schedule)

        if not night_attendances:
            return False
        if not schedules:
            return True

        return any(activity.clock_in <= schedule.end_time for schedule in schedules)

    @staticmethod
    def _local_created_date(instance):
        created_at = getattr(instance, "created_at", None)
        if not created_at:
            return None
        if timezone.is_naive(created_at):
            created_at = timezone.make_aware(
                created_at, timezone.get_default_timezone()
            )
        return timezone.localtime(
            created_at, timezone.get_default_timezone()
        ).date()
