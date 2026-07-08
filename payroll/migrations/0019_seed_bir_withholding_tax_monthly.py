import math

from django.db import migrations

# BIR revised withholding tax table, monthly, effective January 1, 2023
# onwards (TRAIN law, RA 10963, RR 8-2018 as amended).
# (min_income, max_income, base_tax, excess_rate)
#
# IMPORTANT: verify these bracket cutoffs, base tax amounts, and rates
# against the current official BIR withholding tax table before trusting
# this in production.
MONTHLY_BRACKETS = [
    (0, 20832.99, 0.0, 0.0),
    (20833, 33332.99, 0.0, 15.0),
    (33333, 66666.99, 1875.0, 20.0),
    (66667, 166666.99, 8541.80, 25.0),
    (166667, 666666.99, 33541.80, 30.0),
    (666667, None, 183541.80, 35.0),
]


def seed_monthly(apps, schema_editor):
    BIRWithholdingTax = apps.get_model("payroll", "BIRWithholdingTax")
    for min_income, max_income, base_tax, excess_rate in MONTHLY_BRACKETS:
        BIRWithholdingTax.objects.create(
            frequency="monthly",
            min_income=min_income,
            max_income=max_income if max_income is not None else math.inf,
            base_tax=base_tax,
            excess_rate=excess_rate,
        )


def remove_monthly(apps, schema_editor):
    BIRWithholdingTax = apps.get_model("payroll", "BIRWithholdingTax")
    BIRWithholdingTax.objects.filter(frequency="monthly").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0018_seed_bir_withholding_tax_semi_monthly"),
    ]

    operations = [
        migrations.RunPython(seed_monthly, remove_monthly),
    ]
