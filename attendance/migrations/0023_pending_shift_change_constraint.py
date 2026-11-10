from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0022_historicalattendance_approval_metadata"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="attendanceportalshiftchangerequest",
            constraint=models.UniqueConstraint(
                condition=Q(status="pending"),
                fields=("employee_id", "day"),
                name="uniq_pending_portal_shift_change",
            ),
        ),
    ]
