from collections import Counter, defaultdict
from datetime import timedelta

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F
from django.utils.dateparse import parse_date
from django.utils import timezone

from attendance.methods.utils import (
    calculate_worked_hours,
    get_effective_attendance_schedule,
    recalculate_attendance_for_day,
    shift_schedule_with_weekday_fallback,
)
from attendance.models import (
    Attendance,
    AttendanceActivity,
    AttendanceLateComeEarlyOut,
    AttendanceRequestComment,
    AttendanceScheduleOverride,
    WorkRecords,
)
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
        parser.add_argument(
            "--reconcile-from-activities",
            action="store_true",
            help=(
                "Rebuild attendance dates and times from employee-local activity "
                "events, merging redundant attendance rows."
            ),
        )
        parser.add_argument(
            "--move-activity-images",
            action="append",
            default=[],
            metavar="SOURCE_ID:TARGET_ID",
            help=(
                "Move clock-in/out images between activity IDs. Repeat the option "
                "for multiple transfers."
            ),
        )
        parser.add_argument(
            "--force-image-transfer",
            action="store_true",
            help="Allow an image transfer to overwrite an occupied target image field.",
        )

    def handle(self, *args, **options):
        from_date = self._parse_option_date(options.get("from_date"), "--from-date")
        to_date = self._parse_option_date(options.get("to_date"), "--to-date")
        if from_date and to_date and from_date > to_date:
            raise CommandError("--from-date cannot be later than --to-date.")

        if options.get("reconcile_from_activities"):
            self._reconcile_from_activities(
                from_date=from_date,
                to_date=to_date,
                employee_no=options.get("employee_no"),
                apply=options["apply"],
                image_specs=options.get("move_activity_images") or [],
                force_image_transfer=options.get("force_image_transfer", False),
            )
            return

        image_transfers = self._build_image_transfer_plans(
            options.get("move_activity_images") or [],
            employee_no=options.get("employee_no"),
            from_date=from_date,
            to_date=to_date,
            force=options.get("force_image_transfer", False),
        )

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
        image_field_count = sum(len(plan["fields"]) for plan in image_transfers)
        self.stdout.write(
            f"{mode}: {eligible_count + image_field_count} eligible attendance "
            "repair operation(s)."
        )

        for activity, target_attendance in repairs:
            self.stdout.write(
                "  Employee {employee_no} | Activity {activity_id}: "
                "{old_date} -> {new_date}"
                .format(
                    employee_no=self._employee_no(activity),
                    activity_id=activity.pk,
                    old_date=activity.attendance_date,
                    new_date=target_attendance.attendance_date,
                )
            )

        for activity, _target_day in repairs_without_attendance:
            self.stdout.write(
                "  Employee {employee_no} | Activity {activity_id}: "
                "{old_date} -> {new_date}".format(
                    employee_no=self._employee_no(activity),
                    activity_id=activity.pk,
                    old_date=activity.attendance_date,
                    new_date=activity.clock_in_date,
                )
            )

        for attendance, target_date, _target_day, _activities in attendance_repairs:
            self.stdout.write(
                "  Employee {employee_no} | Attendance {attendance_id}: "
                "{old_date} -> {new_date}".format(
                    employee_no=self._employee_no(attendance),
                    attendance_id=attendance.pk,
                    old_date=attendance.attendance_date,
                    new_date=target_date,
                )
            )

        for image_plan in image_transfers:
            self.stdout.write(
                "  Employee {employee_no} | Images {source_id} -> {target_id}: "
                "{fields}".format(
                    employee_no=image_plan["employee_no"],
                    source_id=image_plan["source"].pk,
                    target_id=image_plan["target"].pk,
                    fields=", ".join(image_plan["fields"]),
                )
            )

        if options["apply"] and (eligible_count or image_transfers):
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

                self._apply_image_transfers(image_transfers)

            self.stdout.write(
                self.style.SUCCESS(
                    f"Applied {eligible_count + image_field_count} attendance "
                    "repair operation(s)."
                )
            )

        self._write_skip_summary(
            "legitimate overnight activity row(s)", skipped_overnight
        )
        self._write_skip_summary(
            "row(s) without a matching attendance date", skipped_without_target
        )
        self._write_skip_summary(
            "legitimate overnight attendance row(s)", skipped_attendance_overnight
        )
        self._write_skip_summary(
            "attendance row(s) because the target date already exists",
            skipped_attendance_collision,
        )

    def _reconcile_from_activities(
        self,
        *,
        from_date,
        to_date,
        employee_no,
        apply,
        image_specs,
        force_image_transfer,
    ):
        image_transfers = self._build_image_transfer_plans(
            image_specs,
            employee_no=employee_no,
            from_date=from_date,
            to_date=to_date,
            force=force_image_transfer,
        )

        activity_queryset = (
            AttendanceActivity._base_manager.filter(clock_in_date__isnull=False)
            .select_related("employee_id", "shift_day")
            .order_by("employee_id_id", "clock_in_date", "clock_in", "id")
        )
        if employee_no:
            activity_queryset = activity_queryset.filter(
                employee_id__employee_no=employee_no
            )

        activities = list(activity_queryset)
        activity_targets = {}
        activities_by_id = {}
        activities_by_target = defaultdict(list)
        skipped_without_event_date = []
        for activity in activities:
            target_date = self._activity_reconciliation_date(activity)
            if not target_date:
                skipped_without_event_date.append(activity)
                continue
            if from_date and target_date < from_date:
                continue
            if to_date and target_date > to_date:
                continue
            activity_targets[activity.pk] = target_date
            activities_by_id[activity.pk] = activity
            activities_by_target[(activity.employee_id_id, target_date)].append(
                activity
            )

        employee_ids = {employee_id for employee_id, _date in activities_by_target}
        employees = {
            activity.employee_id_id: activity.employee_id
            for activity in activities_by_id.values()
        }
        all_attendances = list(
            Attendance._base_manager.filter(employee_id_id__in=employee_ids)
            .select_related(
                "employee_id", "attendance_day", "shift_id", "work_type_id"
            )
            .order_by("employee_id_id", "attendance_date", "id")
        )
        attendances_by_employee = defaultdict(list)
        for attendance in all_attendances:
            attendances_by_employee[attendance.employee_id_id].append(attendance)

        reconciliation_plans = []
        for (employee_id, target_date), grouped_activities in sorted(
            activities_by_target.items(),
            key=lambda item: (item[0][0], item[0][1]),
        ):
            employee = employees[employee_id]
            candidates = self._attendance_candidates(
                target_date,
                grouped_activities,
                attendances_by_employee[employee_id],
                activity_targets,
            )
            canonical = max(
                candidates,
                key=lambda attendance: self._attendance_candidate_score(
                    attendance,
                    target_date,
                    grouped_activities,
                    activity_targets,
                ),
                default=None,
            )
            duplicates = [
                attendance for attendance in candidates if attendance != canonical
            ]
            target_day = EmployeeShiftDay.objects.filter(
                day=target_date.strftime("%A").lower()
            ).first()
            reconciliation_plans.append(
                {
                    "employee": employee,
                    "target_date": target_date,
                    "target_day": target_day,
                    "activities": grouped_activities,
                    "canonical": canonical,
                    "duplicates": duplicates,
                    "blocked_reason": self._reconciliation_block_reason(
                        candidates
                    ),
                }
            )

        eligible_plans = [
            plan for plan in reconciliation_plans if not plan["blocked_reason"]
        ]
        eligible_activity_ids = {
            activity.pk
            for plan in eligible_plans
            for activity in plan["activities"]
        }
        changed_activity_count = sum(
            1
            for activity_id, activity in activities_by_id.items()
            if activity_id in eligible_activity_ids
            if activity.attendance_date != activity_targets[activity.pk]
        )
        # Reconciliation also refreshes clock-in/out fields and worked hours for
        # rows whose date already matches, so every activity-backed day is an
        # eligible attendance recalculation.
        changed_attendance_count = len(eligible_plans)
        image_field_count = sum(len(plan["fields"]) for plan in image_transfers)
        operation_count = (
            changed_activity_count
            + changed_attendance_count
            + sum(len(plan["duplicates"]) for plan in eligible_plans)
            + image_field_count
        )

        mode = "Applying" if apply else "Dry run"
        self.stdout.write(
            f"{mode}: {operation_count} eligible activity reconciliation operation(s)."
        )
        for plan in reconciliation_plans:
            if plan["blocked_reason"]:
                self.stdout.write(
                    "  Employee {employee_no} | Skipped {date}: {reason}".format(
                        employee_no=self._employee_no(plan["employee"]),
                        date=plan["target_date"],
                        reason=plan["blocked_reason"],
                    )
                )
                continue
            employee = plan["employee"]
            target_date = plan["target_date"]
            canonical = plan["canonical"]
            for activity in plan["activities"]:
                if activity.attendance_date != target_date:
                    self.stdout.write(
                        "  Employee {employee_no} | Activity {activity_id}: "
                        "{old_date} -> {new_date}".format(
                            employee_no=self._employee_no(activity),
                            activity_id=activity.pk,
                            old_date=activity.attendance_date,
                            new_date=target_date,
                        )
                    )
            if canonical is None:
                self.stdout.write(
                    "  Employee {employee_no} | Attendance create: {date}".format(
                        employee_no=self._employee_no(employee), date=target_date
                    )
                )
            elif canonical.attendance_date != target_date:
                self.stdout.write(
                    "  Employee {employee_no} | Attendance {attendance_id}: "
                    "{old_date} -> {new_date}".format(
                        employee_no=self._employee_no(canonical),
                        attendance_id=canonical.pk,
                        old_date=canonical.attendance_date,
                        new_date=target_date,
                    )
                )
            if canonical:
                preview_fields = self._attendance_fields_from_activities(
                    employee,
                    canonical,
                    target_date,
                    plan["target_day"],
                    plan["activities"],
                )
                changed_fields = []
                for field_name in (
                    "attendance_clock_in_date",
                    "attendance_clock_in",
                    "attendance_clock_out_date",
                    "attendance_clock_out",
                    "attendance_worked_hour",
                ):
                    old_value = getattr(canonical, field_name)
                    new_value = preview_fields[field_name]
                    if old_value != new_value:
                        changed_fields.append(
                            f"{field_name} {old_value} -> {new_value}"
                        )
                if changed_fields:
                    self.stdout.write(
                        "  Employee {employee_no} | Attendance {attendance_id} "
                        "refresh: {fields}".format(
                            employee_no=self._employee_no(canonical),
                            attendance_id=canonical.pk,
                            fields="; ".join(changed_fields),
                        )
                    )
            if canonical:
                for duplicate in plan["duplicates"]:
                    self.stdout.write(
                        "  Employee {employee_no} | Merge Attendance {source_id} "
                        "into {target_id} for {date}".format(
                            employee_no=self._employee_no(duplicate),
                            source_id=duplicate.pk,
                            target_id=canonical.pk,
                            date=target_date,
                        )
                    )

        for activity in skipped_without_event_date:
            self.stdout.write(
                "  Employee {employee_no} | Skipped Activity {activity_id}: "
                "missing local event date.".format(
                    employee_no=self._employee_no(activity), activity_id=activity.pk
                )
            )
        for image_plan in image_transfers:
            self.stdout.write(
                "  Employee {employee_no} | Images {source_id} -> {target_id}: "
                "{fields}".format(
                    employee_no=image_plan["employee_no"],
                    source_id=image_plan["source"].pk,
                    target_id=image_plan["target"].pk,
                    fields=", ".join(image_plan["fields"]),
                )
            )

        if apply and operation_count:
            with transaction.atomic():
                for activity_id in eligible_activity_ids:
                    target_date = activity_targets[activity_id]
                    activity = AttendanceActivity._base_manager.get(pk=activity_id)
                    target_day = EmployeeShiftDay.objects.filter(
                        day=target_date.strftime("%A").lower()
                    ).first()
                    update_fields = []
                    if activity.attendance_date != target_date:
                        activity.attendance_date = target_date
                        update_fields.append("attendance_date")
                    if target_day and activity.shift_day_id != target_day.pk:
                        activity.shift_day = target_day
                        update_fields.append("shift_day")
                    if update_fields:
                        activity.save(update_fields=update_fields)

                for plan in eligible_plans:
                    attendance = plan["canonical"]
                    target_date = plan["target_date"]
                    target_day = plan["target_day"]
                    employee = plan["employee"]
                    fields = self._attendance_fields_from_activities(
                        employee,
                        attendance,
                        target_date,
                        target_day,
                        plan["activities"],
                    )
                    if attendance is None:
                        attendance = Attendance._base_manager.create(**fields)
                    else:
                        Attendance._base_manager.filter(pk=attendance.pk).update(
                            **fields
                        )
                    for duplicate in plan["duplicates"]:
                        self._merge_attendance_dependencies(duplicate, attendance)
                        Attendance._base_manager.filter(pk=duplicate.pk).delete()
                    recalculate_attendance_for_day(
                        Attendance._base_manager.get(pk=attendance.pk)
                    )

                self._apply_image_transfers(image_transfers)

            self.stdout.write(
                self.style.SUCCESS(
                    f"Applied {operation_count} activity reconciliation operation(s)."
                )
            )

        self._write_skip_summary(
            "activity row(s) without a local event date", skipped_without_event_date
        )

    @staticmethod
    def _employee_no(instance):
        employee = getattr(instance, "employee_id", instance)
        employee_no = getattr(employee, "employee_no", None)
        return employee_no or f"ID-{getattr(employee, 'pk', 'unknown')}"

    def _activity_reconciliation_date(self, activity):
        source_attendance = (
            Attendance._base_manager.filter(
                employee_id_id=activity.employee_id_id,
                attendance_date=activity.attendance_date,
            )
            .select_related("attendance_day", "shift_id")
            .first()
        )
        if self._is_legitimate_overnight(activity, source_attendance):
            return activity.attendance_date
        if activity.in_datetime:
            if timezone.is_aware(activity.in_datetime):
                return timezone.localtime(
                    activity.in_datetime, timezone.get_default_timezone()
                ).date()
            return activity.in_datetime.date()
        return activity.clock_in_date

    @staticmethod
    def _attendance_candidates(
        target_date,
        activities,
        attendances,
        activity_targets,
    ):
        clock_pairs = {
            (activity.clock_in_date, activity.clock_in) for activity in activities
        }
        current_dates = {activity.attendance_date for activity in activities}
        candidates = []
        for attendance in attendances:
            if attendance.attendance_date == target_date:
                candidates.append(attendance)
                continue
            if (
                attendance.attendance_clock_in_date,
                attendance.attendance_clock_in,
            ) in clock_pairs:
                candidates.append(attendance)
                continue
            if attendance.attendance_date in current_dates:
                current_activity_targets = {
                    activity_targets.get(activity.pk)
                    for activity in activities
                    if activity.attendance_date == attendance.attendance_date
                }
                if current_activity_targets == {target_date}:
                    candidates.append(attendance)
        return candidates

    @staticmethod
    def _attendance_candidate_score(
        attendance,
        target_date,
        activities,
        activity_targets,
    ):
        exact_date = 100 if attendance.attendance_date == target_date else 0
        exact_clock = sum(
            10
            for activity in activities
            if attendance.attendance_clock_in_date == activity.clock_in_date
            and attendance.attendance_clock_in == activity.clock_in
        )
        activity_count = sum(
            1
            for activity in activities
            if activity_targets.get(activity.pk) == target_date
        )
        return exact_date + exact_clock + activity_count, -attendance.pk

    @staticmethod
    def _reconciliation_block_reason(attendances):
        """Keep groups with approvals or manual attendance fields for review."""
        manual_fields = (
            "attendance_validated",
            "attendance_overtime_approve",
            "is_validate_request",
            "is_validate_request_approved",
            "is_bulk_request",
            "request_description",
            "requested_data",
            "approved_by_id",
            "batch_attendance_id",
            "notes",
        )
        for attendance in attendances:
            for field_name in manual_fields:
                value = getattr(attendance, field_name, None)
                if value not in (None, False, "", {}):
                    return f"attendance {attendance.pk} has manual/approval data ({field_name})"
        return ""

    def _attendance_fields_from_activities(
        self,
        employee,
        attendance,
        target_date,
        target_day,
        activities,
    ):
        work_activities = [
            activity for activity in activities if activity.activity_type == "work"
        ] or list(activities)
        clock_in_events = []
        clock_out_events = []
        for activity in work_activities:
            if activity.clock_in_date and activity.clock_in:
                clock_in_events.append((activity.clock_in_date, activity.clock_in))
            if activity.clock_out_date and activity.clock_out:
                clock_out_events.append((activity.clock_out_date, activity.clock_out))
        clock_in_events.sort()
        clock_out_events.sort()
        first_event = (
            clock_in_events[0] if clock_in_events else (target_date, None)
        )
        last_event = clock_out_events[-1] if clock_out_events else (None, None)

        shift = getattr(attendance, "shift_id", None) if attendance else None
        work_type = getattr(attendance, "work_type_id", None) if attendance else None
        minimum_hour = getattr(attendance, "minimum_hour", None) if attendance else None
        if not shift:
            work_info = getattr(employee, "employee_work_info", None)
            shift = getattr(work_info, "shift_id", None)
            work_type = work_type or getattr(work_info, "work_type_id", None)
        if not minimum_hour:
            schedule = (
                get_effective_attendance_schedule(attendance) if attendance else None
            )
            if not schedule and shift and target_day:
                schedule = shift_schedule_with_weekday_fallback(target_day, shift)
            minimum_hour = getattr(schedule, "minimum_working_hour", None) or "00:00"

        return {
            "employee_id": employee,
            "attendance_date": target_date,
            "shift_id": shift,
            "work_type_id": work_type,
            "attendance_day": target_day,
            "attendance_clock_in_date": first_event[0],
            "attendance_clock_in": first_event[1],
            "attendance_clock_out_date": last_event[0],
            "attendance_clock_out": last_event[1],
            "attendance_worked_hour": calculate_worked_hours(
                employee, target_date
            ),
            "minimum_hour": minimum_hour,
        }

    @staticmethod
    def _merge_attendance_dependencies(source, target):
        AttendanceRequestComment._base_manager.filter(request_id=source).update(
            request_id=target
        )
        for penalty in list(
            AttendanceLateComeEarlyOut._base_manager.filter(attendance_id=source)
        ):
            if AttendanceLateComeEarlyOut._base_manager.filter(
                attendance_id=target, type=penalty.type
            ).exclude(pk=penalty.pk).exists():
                penalty.delete()
            else:
                AttendanceLateComeEarlyOut._base_manager.filter(pk=penalty.pk).update(
                    attendance_id=target
                )

        source_override = AttendanceScheduleOverride._base_manager.filter(
            attendance_id=source
        ).first()
        if source_override:
            if AttendanceScheduleOverride._base_manager.filter(
                attendance_id=target
            ).exists():
                AttendanceScheduleOverride._base_manager.filter(
                    pk=source_override.pk
                ).delete()
            else:
                AttendanceScheduleOverride._base_manager.filter(
                    pk=source_override.pk
                ).update(attendance_id=target)

        WorkRecords._base_manager.filter(attendance_id=source).update(
            attendance_id=target
        )

        leave_model = apps.get_model("leave", "CompensatoryLeaveRequest")
        if leave_model:
            for leave_request in leave_model._base_manager.filter(
                attendance_id=source
            ):
                leave_request.attendance_id.add(target)
                leave_request.attendance_id.remove(source)

    @staticmethod
    def _parse_image_spec(spec):
        try:
            source_id, target_id = (int(value) for value in spec.split(":", 1))
        except (TypeError, ValueError):
            raise CommandError(
                "--move-activity-images must use SOURCE_ID:TARGET_ID format."
            )
        if source_id == target_id:
            raise CommandError("Image source and target activity IDs must differ.")
        return source_id, target_id

    def _build_image_transfer_plans(
        self,
        specs,
        *,
        employee_no,
        from_date,
        to_date,
        force,
    ):
        plans = []
        for spec in specs:
            source_id, target_id = self._parse_image_spec(spec)
            try:
                source = AttendanceActivity._base_manager.select_related(
                    "employee_id"
                ).get(pk=source_id)
                target = AttendanceActivity._base_manager.select_related(
                    "employee_id"
                ).get(pk=target_id)
            except AttendanceActivity.DoesNotExist as exc:
                raise CommandError(
                    f"Image transfer activity not found: {source_id} or {target_id}."
                ) from exc
            if source.employee_id_id != target.employee_id_id:
                raise CommandError("Image source and target must belong to the same employee.")
            if employee_no and self._employee_no(source) != employee_no:
                raise CommandError(
                    f"Image transfer activity {source_id} does not belong to "
                    f"employee {employee_no}."
                )
            dates = [
                value
                for value in (source.attendance_date, target.attendance_date)
                if value
            ]
            if from_date and dates and min(dates) < from_date:
                raise CommandError("Image transfer source/target is outside --from-date.")
            if to_date and dates and max(dates) > to_date:
                raise CommandError("Image transfer source/target is outside --to-date.")

            fields = []
            for field_name in ("clock_in_selfie", "clock_out_selfie"):
                source_file = getattr(source, field_name)
                target_file = getattr(target, field_name)
                if source_file and target_file and not force:
                    raise CommandError(
                        f"Target activity {target_id} already has {field_name}; "
                        "use --force-image-transfer to overwrite it."
                    )
                if source_file:
                    fields.append(field_name)
            if fields:
                plans.append(
                    {
                        "source": source,
                        "target": target,
                        "fields": fields,
                        "employee_no": self._employee_no(source),
                    }
                )
        return plans

    @staticmethod
    def _apply_image_transfers(plans):
        for plan in plans:
            source = plan["source"]
            target = plan["target"]
            source_updates = {}
            target_updates = {}
            for field_name in plan["fields"]:
                source_file = getattr(source, field_name)
                target_updates[field_name] = source_file.name
                source_updates[field_name] = None
            AttendanceActivity._base_manager.filter(pk=target.pk).update(
                **target_updates
            )
            AttendanceActivity._base_manager.filter(pk=source.pk).update(
                **source_updates
            )

    def _write_skip_summary(self, label, instances):
        counts = Counter(self._employee_no(instance) for instance in instances)
        if not counts:
            self.stdout.write(f"Skipped 0 {label}.")
            return
        for employee_no, count in sorted(counts.items()):
            self.stdout.write(f"Skipped {count} {label} for employee {employee_no}.")

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
