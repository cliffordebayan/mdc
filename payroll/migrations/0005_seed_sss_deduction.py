from django.db import migrations


def seed_sss_deduction(apps, schema_editor):
    Deduction = apps.get_model("payroll", "Deduction")
    if not Deduction.objects.filter(is_sss=True).exists():
        Deduction.objects.create(
            title="SSS Contribution",
            is_sss=True,
            include_active_employees=True,
            is_pretax=False,
            is_tax=False,
            is_fixed=True,
            amount=0,
            update_compensation=None,
        )


def remove_sss_deduction(apps, schema_editor):
    Deduction = apps.get_model("payroll", "Deduction")
    Deduction.objects.filter(is_sss=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0004_add_is_sss_to_deduction"),
    ]

    operations = [
        migrations.RunPython(seed_sss_deduction, remove_sss_deduction),
    ]
