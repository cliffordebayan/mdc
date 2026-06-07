from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0005_attendanceactivity_separate_clock_locations"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendanceactivity",
            name="activity_type",
            field=models.CharField(
                default="work",
                max_length=20,
                verbose_name="Activity Type",
            ),
        ),
    ]
