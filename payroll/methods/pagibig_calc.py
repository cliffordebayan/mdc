"""
Module: payroll.pagibig_calc

This module contains a function for calculating the Pag-IBIG / HDMF
(Home Development Mutual Fund) contribution for an employee based on their
basic pay, using the configured PagibigSettings rates.
"""

from payroll.models.tax_models import PagibigSettings


def calculate_pagibig_contribution(**kwargs):
    """Calculate the Pag-IBIG employee and employer contribution shares
    for the given basic pay.

    The employee rate tier is chosen from the actual (uncapped) basic pay
    against the threshold, but the rate is then applied to the capped
    contribution base.

    Args:
        basic_pay (float): The basic pay amount for the period.

    Returns:
        dict: employee_share (deducted from net pay) and employer_share
        (informational only, not deducted).
    """
    basic_pay = float(kwargs["basic_pay"])
    settings = PagibigSettings.objects.first()
    if not settings:
        return {"employee_share": 0, "employer_share": 0}

    capped_base = min(basic_pay, settings.contribution_cap)
    employee_rate = (
        settings.employee_rate_below_threshold
        if basic_pay <= settings.threshold_amount
        else settings.employee_rate_above_threshold
    )
    return {
        "employee_share": capped_base * employee_rate / 100,
        "employer_share": capped_base * settings.employer_rate / 100,
    }
