from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("base", "0012_employeeshift_is_flexible"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="employeeshift",
            name="is_flexible",
        ),
    ]
