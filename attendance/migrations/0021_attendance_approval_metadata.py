from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def mark_existing_pending_requests(apps, schema_editor):
    Attendance = apps.get_model("attendance", "Attendance")
    Attendance.objects.filter(is_validate_request=True).update(
        approval_status="pending"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0020_attendanceportalshiftchangerequest"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="attendance",
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
            model_name="attendance",
            name="approval_decided_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="attendance",
            name="approval_decision_note",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="attendance",
            name="approval_decided_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="decided_attendance_requests",
                to="employee.employee",
                verbose_name="Approval Decided By",
            ),
        ),
        migrations.RunPython(mark_existing_pending_requests, migrations.RunPython.noop),
    ]
