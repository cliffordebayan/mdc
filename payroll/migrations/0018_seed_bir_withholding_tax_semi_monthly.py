import math

from django.db import migrations

# BIR revised withholding tax table, semi-monthly, effective January 1, 2023
# onwards (TRAIN law, RA 10963, RR 8-2018 as amended).
# (min_income, max_income, base_tax, excess_rate)
#
# IMPORTANT: verify these bracket cutoffs, base tax amounts, and rates
# against the current official BIR withholding tax table before trusting
# this in production.
SEMI_MONTHLY_BRACKETS = [
    (0, 10416.99, 0.0, 0.0),
    (10417, 16666.99, 0.0, 15.0),
    (16667, 33332.99, 833.33, 20.0),
    (33333, 83332.99, 4166.67, 25.0),
    (83333, 333332.99, 16666.67, 30.0),
    (333333, None, 91666.67, 35.0),
]


def seed_semi_monthly(apps, schema_editor):
    BIRWithholdingTax = apps.get_model("payroll", "BIRWithholdingTax")
    for min_income, max_income, base_tax, excess_rate in SEMI_MONTHLY_BRACKETS:
        BIRWithholdingTax.objects.create(
            frequency="semi_monthly",
            min_income=min_income,
            max_income=max_income if max_income is not None else math.inf,
            base_tax=base_tax,
            excess_rate=excess_rate,
        )


def remove_semi_monthly(apps, schema_editor):
    BIRWithholdingTax = apps.get_model("payroll", "BIRWithholdingTax")
    BIRWithholdingTax.objects.filter(frequency="semi_monthly").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0017_add_bir_withholding_tax"),
    ]

    operations = [
        migrations.RunPython(seed_semi_monthly, remove_semi_monthly),
    ]
