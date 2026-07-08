"""
Module: payroll.sss_calc

This module contains a function for calculating the SSS (Philippine Social
Security System) contribution for an employee based on their basic pay,
looked up against the SSS Contribution bracket table.
"""

from payroll.models.tax_models import SSSContribution


def calculate_sss_contribution(**kwargs):
    """Calculate the SSS employee and employer contribution shares for the
    given basic pay.

    Args:
        basic_pay (float): The basic pay amount for the period.

    Returns:
        dict: employee_share (deducted from net pay) and employer_share
        (informational only, not deducted).
    """
    basic_pay = float(kwargs["basic_pay"])
    bracket = SSSContribution.objects.filter(
        range_from__lte=basic_pay, range_to__gte=basic_pay
    ).first()
    if not bracket:
        return {"employee_share": 0, "employer_share": 0}
    return {
        "employee_share": bracket.employee_total,
        "employer_share": bracket.employer_total,
    }
