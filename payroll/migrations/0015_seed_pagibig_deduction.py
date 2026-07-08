from django.db import migrations


def seed_pagibig_deduction(apps, schema_editor):
    Deduction = apps.get_model("payroll", "Deduction")
    if not Deduction.objects.filter(is_pagibig=True).exists():
        Deduction.objects.create(
            title="Pag-IBIG Contribution",
            is_pagibig=True,
            include_active_employees=True,
            is_pretax=False,
            is_tax=False,
            is_fixed=True,
            amount=0,
            update_compensation=None,
        )


def remove_pagibig_deduction(apps, schema_editor):
    Deduction = apps.get_model("payroll", "Deduction")
    Deduction.objects.filter(is_pagibig=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0014_add_is_pagibig_to_deduction"),
    ]

    operations = [
        migrations.RunPython(seed_pagibig_deduction, remove_pagibig_deduction),
    ]
