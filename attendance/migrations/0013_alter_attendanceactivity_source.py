from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0012_attendanceportalmultipunchemployee"),
    ]

    operations = [
        migrations.AlterField(
            model_name="attendanceactivity",
            name="source",
            field=models.CharField(
                choices=[
                    ("website", "Website"),
                    ("import", "Import"),
                    ("mobile_app", "Mobile App"),
                ],
                default="website",
                max_length=10,
                verbose_name="Source",
            ),
        ),
    ]
