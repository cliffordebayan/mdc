"""
Module: payroll.bir_tax_calc

This module contains a function for calculating the Philippine BIR
withholding tax for a given taxable income and payroll frequency, looked up
against the BIRWithholdingTax bracket table.

This is computed directly against the per-period taxable income (no
annualize-then-divide step), since the BIR table is already scoped per
payroll frequency.
"""

from payroll.models.tax_models import BIRWithholdingTax


def calculate_bir_withholding_tax(**kwargs):
    """Calculate the BIR withholding tax for the given per-period taxable
    income and payroll frequency.

    Args:
        taxable_income (float): The taxable income for the pay period.
        frequency (str): The payroll frequency ("weekly", "semi_monthly",
            or "monthly").

    Returns:
        float: The withholding tax amount for the period.
    """
    taxable_income = float(kwargs["taxable_income"])
    frequency = kwargs["frequency"]
    bracket = (
        BIRWithholdingTax.objects.filter(
            frequency=frequency,
            min_income__lte=taxable_income,
            max_income__gte=taxable_income,
        )
        .order_by("min_income")
        .first()
    )
    if not bracket:
        return 0

    return bracket.base_tax + (
        (taxable_income - bracket.min_income) * bracket.excess_rate / 100
    )
