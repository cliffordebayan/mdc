from django.db import migrations

# PhilHealth premium rate as of 2025 (rate frozen at 5% per suspension of the
# scheduled increase to 5.5%), split equally 2.5% employee / 2.5% employer,
# with a salary floor of 10,000 and ceiling of 100,000.
# Verify against the current PhilHealth Circular before trusting in production.


def seed_philhealth_settings(apps, schema_editor):
    PhilHealthSettings = apps.get_model("payroll", "PhilHealthSettings")
    if not PhilHealthSettings.objects.exists():
        PhilHealthSettings.objects.create(
            floor_amount=10000.0,
            ceiling_amount=100000.0,
            total_rate=5.0,
            employee_share_rate=2.5,
            employer_share_rate=2.5,
        )


def remove_philhealth_settings(apps, schema_editor):
    PhilHealthSettings = apps.get_model("payroll", "PhilHealthSettings")
    PhilHealthSettings.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0007_add_philhealth_settings"),
    ]

    operations = [
        migrations.RunPython(seed_philhealth_settings, remove_philhealth_settings),
    ]
