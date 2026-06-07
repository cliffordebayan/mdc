from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0007_attendancegeneralsetting_portal_breaks"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendancegeneralsetting",
            name="portal_lunch_minutes",
            field=models.PositiveSmallIntegerField(
                default=60,
                verbose_name="Portal Lunch Minutes",
            ),
        ),
    ]
