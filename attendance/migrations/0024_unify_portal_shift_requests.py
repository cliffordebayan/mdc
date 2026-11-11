from datetime import datetime

from django.db import migrations
from django.utils import timezone


def forwards(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    portal_model = apps.get_model("attendance", "AttendancePortalShiftChangeRequest")
    shift_request_model = apps.get_model("base", "ShiftRequest")
    work_info_model = apps.get_model("employee", "EmployeeWorkInformation")

    for portal_request in portal_model._base_manager.using(db_alias).all().iterator():
        work_info = (
            work_info_model._base_manager.using(db_alias)
            .filter(employee_id=portal_request.employee_id_id)
            .first()
        )
        if not work_info or not work_info.shift_id_id:
            raise RuntimeError(
                "Cannot migrate portal shift request %s: employee %s has no shift."
                % (portal_request.pk, portal_request.employee_id_id)
            )

        created_at = portal_request.created_at or timezone.now()
        if isinstance(created_at, datetime):
            requested_date = created_at.date()
        else:
            requested_date = created_at

        shift_request_model._base_manager.using(db_alias).create(
            employee_id_id=portal_request.employee_id_id,
            shift_id_id=work_info.shift_id_id,
            previous_shift_id_id=work_info.shift_id_id,
            requested_date=requested_date,
            requested_till=None,
            description=portal_request.reason,
            is_permanent_shift=True,
            approved=portal_request.status == "approved",
            canceled=portal_request.status == "rejected",
            request_source="portal",
            requested_day_id=portal_request.day_id,
            requested_start_time=portal_request.start_time,
            requested_end_time=portal_request.end_time,
            requested_minimum_working_hour=portal_request.minimum_working_hour,
            reporting_manager_id_id=portal_request.reporting_manager_id_id,
            approved_by_id=portal_request.approved_by_id,
            approved_at=portal_request.approved_at,
            decision_note=portal_request.decision_note,
            legacy_portal_id=portal_request.pk,
            is_active=portal_request.is_active,
            created_by_id=portal_request.created_by_id,
            modified_by_id=portal_request.modified_by_id,
        )


def backwards(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    portal_model = apps.get_model("attendance", "AttendancePortalShiftChangeRequest")
    shift_request_model = apps.get_model("base", "ShiftRequest")

    for shift_request in shift_request_model._base_manager.using(db_alias).filter(
        request_source="portal"
    ).iterator():
        status = (
            "approved"
            if shift_request.approved
            else "rejected"
            if shift_request.canceled
            else "pending"
        )
        portal_model._base_manager.using(db_alias).create(
            id=shift_request.legacy_portal_id,
            employee_id_id=shift_request.employee_id_id,
            reporting_manager_id_id=shift_request.reporting_manager_id_id,
            day_id=shift_request.requested_day_id,
            start_time=shift_request.requested_start_time,
            end_time=shift_request.requested_end_time,
            minimum_working_hour=shift_request.requested_minimum_working_hour
            or "00:00",
            reason=shift_request.description or "",
            status=status,
            approved_by_id=shift_request.approved_by_id,
            approved_at=shift_request.approved_at,
            decision_note=shift_request.decision_note,
            is_active=shift_request.is_active,
            created_by_id=shift_request.created_by_id,
            modified_by_id=shift_request.modified_by_id,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0023_pending_shift_change_constraint"),
        ("base", "0015_shiftrequest_portal_fields"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
        migrations.DeleteModel(name="AttendancePortalShiftChangeRequest"),
    ]
