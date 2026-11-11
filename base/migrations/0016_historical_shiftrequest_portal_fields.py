from django.db import migrations, models
import django.db.models.deletion

import base.models


class Migration(migrations.Migration):

    dependencies = [
        ("base", "0015_shiftrequest_portal_fields"),
        ("employee", "0020_alter_bank_modified_by"),
    ]

    operations = [
        migrations.AddField(
            model_name="historicalshiftrequest",
            name="request_source",
            field=models.CharField(
                choices=[("web", "Web"), ("portal", "Portal")],
                default="web",
                max_length=12,
                verbose_name="Request Source",
            ),
        ),
        migrations.AddField(
            model_name="historicalshiftrequest",
            name="requested_day",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="+",
                to="base.employeeshiftday",
                verbose_name="Requested Recurring Day",
            ),
        ),
        migrations.AddField(
            model_name="historicalshiftrequest",
            name="requested_start_time",
            field=models.TimeField(blank=True, null=True, verbose_name="Requested Start Time"),
        ),
        migrations.AddField(
            model_name="historicalshiftrequest",
            name="requested_end_time",
            field=models.TimeField(blank=True, null=True, verbose_name="Requested End Time"),
        ),
        migrations.AddField(
            model_name="historicalshiftrequest",
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
            model_name="historicalshiftrequest",
            name="reporting_manager_id",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="+",
                to="employee.employee",
                verbose_name="Reporting Manager",
            ),
        ),
        migrations.AddField(
            model_name="historicalshiftrequest",
            name="approved_by",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="+",
                to="employee.employee",
                verbose_name="Approved By",
            ),
        ),
        migrations.AddField(
            model_name="historicalshiftrequest",
            name="approved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="historicalshiftrequest",
            name="decision_note",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="historicalshiftrequest",
            name="legacy_portal_id",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
    ]
