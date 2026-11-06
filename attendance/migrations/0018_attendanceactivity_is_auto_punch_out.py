from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0017_attendanceportalshiftreason"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendanceactivity",
            name="is_auto_punch_out",
            field=models.BooleanField(
                default=False,
                editable=False,
                verbose_name="Automatic Punch Out",
            ),
        ),
    ]
