from django.db import migrations, models
import django.db.models.deletion
from django.db.models import Q

import base.models


class Migration(migrations.Migration):

    dependencies = [
        ("base", "0014_employeeshift_owner_employee"),
        ("employee", "0020_alter_bank_modified_by"),
    ]

    operations = [
        migrations.AddField(
            model_name="shiftrequest",
            name="request_source",
            field=models.CharField(
                choices=[("web", "Web"), ("portal", "Portal")],
                db_index=True,
                default="web",
                max_length=12,
                verbose_name="Request Source",
            ),
        ),
        migrations.AddField(
            model_name="shiftrequest",
            name="requested_day",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="shift_requests",
                to="base.employeeshiftday",
                verbose_name="Requested Recurring Day",
            ),
        ),
        migrations.AddField(
            model_name="shiftrequest",
            name="requested_start_time",
            field=models.TimeField(
                blank=True,
                null=True,
                verbose_name="Requested Start Time",
            ),
        ),
        migrations.AddField(
            model_name="shiftrequest",
            name="requested_end_time",
            field=models.TimeField(
                blank=True,
                null=True,
                verbose_name="Requested End Time",
            ),
        ),
        migrations.AddField(
            model_name="shiftrequest",
            name="requested_minimum_working_hour",
            field=models.CharField(
                blank=True,
                max_length=10,
                null=True,
                validators=[base.models.validate_time_format],
                verbose_name="Requested Minimum Working Hours",
            ),
        ),
        migrations.AddField(
            model_name="shiftrequest",
            name="reporting_manager_id",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="shift_requests_as_reporting_manager",
                to="employee.employee",
                verbose_name="Reporting Manager",
            ),
        ),
        migrations.AddField(
            model_name="shiftrequest",
            name="approved_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="approved_shift_requests",
                to="employee.employee",
                verbose_name="Approved By",
            ),
        ),
        migrations.AddField(
            model_name="shiftrequest",
            name="approved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="shiftrequest",
            name="decision_note",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="shiftrequest",
            name="legacy_portal_id",
            field=models.PositiveBigIntegerField(
                blank=True,
                editable=False,
                null=True,
                unique=True,
            ),
        ),
        migrations.AddConstraint(
            model_name="shiftrequest",
            constraint=models.UniqueConstraint(
                condition=Q(
                    request_source="portal",
                    approved=False,
                    canceled=False,
                ),
                fields=("employee_id", "requested_day"),
                name="uniq_pending_portal_shift_request",
            ),
        ),
    ]
