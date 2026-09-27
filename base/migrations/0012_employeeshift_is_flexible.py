from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("base", "0011_rotatingshift_rotation_type_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="employeeshift",
            name="is_flexible",
            field=models.BooleanField(
                default=False,
                help_text="Allow clock in/out at any time without daily schedule requirements.",
                verbose_name="Flexible Shift",
            ),
        ),
    ]
