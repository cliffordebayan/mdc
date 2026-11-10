from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0021_attendance_approval_metadata"),
    ]

    operations = [
        migrations.AddField(
            model_name="historicalattendance",
            name="approval_status",
            field=models.CharField(
                choices=[
                    ("none", "Not requested"),
                    ("pending", "Pending"),
                    ("approved", "Approved"),
                    ("rejected", "Rejected"),
                ],
                db_index=True,
                default="none",
                max_length=12,
                verbose_name="Approval Status",
            ),
        ),
        migrations.AddField(
            model_name="historicalattendance",
            name="approval_decided_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="historicalattendance",
            name="approval_decision_note",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="historicalattendance",
            name="approval_decided_by",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="+",
                to="employee.employee",
                verbose_name="Approval Decided By",
            ),
        ),
    ]
