from django.db import migrations

# Pag-IBIG (HDMF) contribution rates per Pag-IBIG Fund Circular No. 460-2024:
# employee rate 1% at/below 1,500 monthly compensation, else 2%; employer
# rate always 2%; contribution base capped at 10,000.
# Verify against the current Pag-IBIG circular before trusting in production.


def seed_pagibig_settings(apps, schema_editor):
    PagibigSettings = apps.get_model("payroll", "PagibigSettings")
    if not PagibigSettings.objects.exists():
        PagibigSettings.objects.create(
            threshold_amount=1500.0,
            employee_rate_below_threshold=1.0,
            employee_rate_above_threshold=2.0,
            employer_rate=2.0,
            contribution_cap=10000.0,
        )


def remove_pagibig_settings(apps, schema_editor):
    PagibigSettings = apps.get_model("payroll", "PagibigSettings")
    PagibigSettings.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0012_add_pagibig_settings"),
    ]

    operations = [
        migrations.RunPython(seed_pagibig_settings, remove_pagibig_settings),
    ]
