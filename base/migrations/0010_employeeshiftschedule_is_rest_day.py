from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("base", "0009_holidays_holiday_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="employeeshiftschedule",
            name="is_rest_day",
            field=models.BooleanField(default=False, verbose_name="Is Rest Day"),
        ),
    ]
