import math

from django.db import migrations

# BIR revised withholding tax table, weekly, effective January 1, 2023
# onwards (TRAIN law, RA 10963, RR 8-2018 as amended).
# (min_income, max_income, base_tax, excess_rate)
#
# IMPORTANT: verify these bracket cutoffs, base tax amounts, and rates
# against the current official BIR withholding tax table before trusting
# this in production.
WEEKLY_BRACKETS = [
    (0, 4807.99, 0.0, 0.0),
    (4808, 7691.99, 0.0, 15.0),
    (7692, 15384.99, 432.60, 20.0),
    (15385, 38461.99, 1957.60, 25.0),
    (38462, 153845.99, 7706.90, 30.0),
    (153846, None, 42151.70, 35.0),
]


def seed_weekly(apps, schema_editor):
    BIRWithholdingTax = apps.get_model("payroll", "BIRWithholdingTax")
    for min_income, max_income, base_tax, excess_rate in WEEKLY_BRACKETS:
        BIRWithholdingTax.objects.create(
            frequency="weekly",
            min_income=min_income,
            max_income=max_income if max_income is not None else math.inf,
            base_tax=base_tax,
            excess_rate=excess_rate,
        )


def remove_weekly(apps, schema_editor):
    BIRWithholdingTax = apps.get_model("payroll", "BIRWithholdingTax")
    BIRWithholdingTax.objects.filter(frequency="weekly").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0023_seed_bir_withholding_tax_daily"),
    ]

    operations = [
        migrations.RunPython(seed_weekly, remove_weekly),
    ]
