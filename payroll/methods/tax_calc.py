"""
Module: payroll.tax_calc

This module contains a function for calculating the withholding tax for an
employee based on their contract details and income information, using the
Philippine BIR withholding tax table.
"""

import logging

from payroll.methods.bir_tax_calc import calculate_bir_withholding_tax
from payroll.methods.payslip_calc import calculate_taxable_gross_pay
from payroll.models.models import Contract

logger = logging.getLogger(__name__)


def calculate_taxable_amount(**kwargs):
    """Calculate the BIR withholding tax for a given employee within a
    specific period.

    Args:
        employee (int): The ID of the employee.
        start_date (datetime.date): The start date of the period.
        end_date (datetime.date): The end date of the period.
        allowances (int): The number of allowances claimed by the employee.
        total_allowance (float): The total allowance amount.
        basic_pay (float): The basic pay amount.
        day_dict (dict): A dictionary containing specific day-related information.

    Returns:
        float: The withholding tax amount for the specified period.
    """
    employee = kwargs["employee"]
    contract = Contract.objects.filter(
        employee_id=employee, contract_status="active"
    ).first()

    work_info = getattr(employee, "employee_work_info", None)
    payroll_group = getattr(work_info, "payroll_group_id", None)
    frequency = getattr(payroll_group, "frequency", None) or (
        contract.pay_frequency if contract else None
    )
    if not frequency:
        return 0
    taxable_gross_pay = float(calculate_taxable_gross_pay(**kwargs)["taxable_gross_pay"])
    return calculate_bir_withholding_tax(
        taxable_income=taxable_gross_pay, frequency=frequency
    )
