from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0006_attendanceactivity_activity_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendancegeneralsetting",
            name="portal_break_limit",
            field=models.PositiveSmallIntegerField(
                default=2,
                verbose_name="Portal Break Limit",
            ),
        ),
        migrations.AddField(
            model_name="attendancegeneralsetting",
            name="portal_break_minutes",
            field=models.PositiveSmallIntegerField(
                default=15,
                verbose_name="Portal Break Minutes",
            ),
        ),
    ]
