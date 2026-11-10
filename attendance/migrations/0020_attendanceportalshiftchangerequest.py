from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings

import attendance.methods.utils


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0019_employee_owned_shift_backfill"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AttendancePortalShiftChangeRequest",
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
                    "start_time",
                    models.TimeField(verbose_name="Start Time"),
                ),
                (
                    "end_time",
                    models.TimeField(verbose_name="End Time"),
                ),
                (
                    "minimum_working_hour",
                    models.CharField(
                        default="00:00",
                        max_length=10,
                        validators=[attendance.methods.utils.validate_time_format],
                        verbose_name="Minimum Working Hours",
                    ),
                ),
                ("reason", models.TextField(verbose_name="Reason")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("approved", "Approved"),
                            ("rejected", "Rejected"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=12,
                    ),
                ),
                (
                    "approved_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                ("decision_note", models.TextField(blank=True, null=True)),
                (
                    "approved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="approved_portal_shift_changes",
                        to="employee.employee",
                        verbose_name="Approved By",
                    ),
                ),
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
                    "day",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="portal_shift_change_requests",
                        to="base.employeeshiftday",
                        verbose_name="Shift Day",
                    ),
                ),
                (
                    "employee_id",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="portal_shift_change_requests",
                        to="employee.employee",
                        verbose_name="Employee",
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
                    "reporting_manager_id",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="portal_shift_change_approvals",
                        to="employee.employee",
                        verbose_name="Reporting Manager",
                    ),
                ),
            ],
            options={
                "verbose_name": "Attendance Portal Shift Change Request",
                "verbose_name_plural": "Attendance Portal Shift Change Requests",
                "ordering": ["-id"],
            },
        ),
    ]
