from django.db import migrations


def seed_philhealth_deduction(apps, schema_editor):
    Deduction = apps.get_model("payroll", "Deduction")
    if not Deduction.objects.filter(is_philhealth=True).exists():
        Deduction.objects.create(
            title="PhilHealth Contribution",
            is_philhealth=True,
            include_active_employees=True,
            is_pretax=False,
            is_tax=False,
            is_fixed=True,
            amount=0,
            update_compensation=None,
        )


def remove_philhealth_deduction(apps, schema_editor):
    Deduction = apps.get_model("payroll", "Deduction")
    Deduction.objects.filter(is_philhealth=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0009_add_is_philhealth_to_deduction"),
    ]

    operations = [
        migrations.RunPython(seed_philhealth_deduction, remove_philhealth_deduction),
    ]
