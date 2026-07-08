import math

from django.db import migrations

# BIR revised withholding tax table, daily, effective January 1, 2023
# onwards (TRAIN law, RA 10963, RR 8-2018 as amended).
# (min_income, max_income, base_tax, excess_rate)
#
# IMPORTANT: verify these bracket cutoffs, base tax amounts, and rates
# against the current official BIR withholding tax table before trusting
# this in production.
DAILY_BRACKETS = [
    (0, 684.99, 0.0, 0.0),
    (685, 1095.99, 0.0, 15.0),
    (1096, 2191.99, 61.65, 20.0),
    (2192, 5478.99, 280.85, 25.0),
    (5479, 21917.99, 1102.60, 30.0),
    (21918, None, 6034.30, 35.0),
]


def seed_daily(apps, schema_editor):
    BIRWithholdingTax = apps.get_model("payroll", "BIRWithholdingTax")
    for min_income, max_income, base_tax, excess_rate in DAILY_BRACKETS:
        BIRWithholdingTax.objects.create(
            frequency="daily",
            min_income=min_income,
            max_income=max_income if max_income is not None else math.inf,
            base_tax=base_tax,
            excess_rate=excess_rate,
        )


def remove_daily(apps, schema_editor):
    BIRWithholdingTax = apps.get_model("payroll", "BIRWithholdingTax")
    BIRWithholdingTax.objects.filter(frequency="daily").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0022_add_daily_pay_frequency"),
    ]

    operations = [
        migrations.RunPython(seed_daily, remove_daily),
    ]
