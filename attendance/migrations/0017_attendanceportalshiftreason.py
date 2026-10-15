from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

import attendance.methods.utils


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0016_attendance_notes"),
        ("employee", "0020_alter_bank_modified_by"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AttendancePortalShiftReason",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        blank=True,
                        null=True,
                        verbose_name="Created At",
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(default=True, verbose_name="Is Active"),
                ),
                (
                    "attendance_date",
                    models.DateField(
                        validators=[attendance.methods.utils.attendance_date_validate],
                        verbose_name="Attendance Date",
                    ),
                ),
                ("reason", models.TextField(verbose_name="Reason")),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="%(class)s_modified_by",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Modified By",
                    ),
                ),
                (
                    "employee_id",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="portal_shift_reasons",
                        to="employee.employee",
                        verbose_name="Employee",
                    ),
                ),
            ],
            options={
                "verbose_name": "Attendance Portal Shift Reason",
                "verbose_name_plural": "Attendance Portal Shift Reasons",
            },
        ),
        migrations.AddConstraint(
            model_name="attendanceportalshiftreason",
            constraint=models.UniqueConstraint(
                fields=("employee_id", "attendance_date"),
                name="uniq_portal_shift_reason_employee_date",
            ),
        ),
    ]
