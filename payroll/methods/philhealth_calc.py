"""
Module: payroll.philhealth_calc

This module contains a function for calculating the PhilHealth (Philippine
Health Insurance Corporation) premium contribution for an employee based on
their basic pay, using the configured PhilHealthSettings rate.
"""

from payroll.models.tax_models import PhilHealthSettings


def calculate_philhealth_contribution(**kwargs):
    """Calculate the PhilHealth employee and employer contribution shares
    for the given basic pay.

    Args:
        basic_pay (float): The basic pay amount for the period.

    Returns:
        dict: employee_share (deducted from net pay) and employer_share
        (informational only, not deducted).
    """
    basic_pay = float(kwargs["basic_pay"])
    settings = PhilHealthSettings.objects.first()
    if not settings:
        return {"employee_share": 0, "employer_share": 0}

    base = min(max(basic_pay, settings.floor_amount), settings.ceiling_amount)
    return {
        "employee_share": base * settings.employee_share_rate / 100,
        "employer_share": base * settings.employer_share_rate / 100,
    }
