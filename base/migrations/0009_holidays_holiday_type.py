from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("base", "0008_payrollgroup_two_periods"),
    ]

    operations = [
        migrations.AddField(
            model_name="holidays",
            name="holiday_type",
            field=models.CharField(
                choices=[
                    ("unclassified", "Unclassified"),
                    ("regular", "Regular Holiday"),
                    ("special", "Special Holiday"),
                ],
                default="unclassified",
                max_length=20,
                verbose_name="Holiday Type",
            ),
        ),
    ]
