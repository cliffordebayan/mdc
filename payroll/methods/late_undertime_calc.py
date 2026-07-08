"""
Module: payroll.late_undertime_calc

This module calculates Late and Undertime pay reductions for a given
employee and payroll period, from the attendance app's
AttendanceLateComeEarlyOut records.

Like holiday pay and night differential (see payroll.methods.holiday_pay_calc),
these are computed directly during payslip generation rather than through the
generic Allowance/Deduction engine. Unlike holiday pay, Late and Undertime
*reduce* gross pay -- on a Philippine-style payslip they appear as negative
line items in the Earnings column, not as rows in the Deductions column.

The per-minute rate is derived from the daily rate assuming a standard
8-hour workday (the same STANDARD_HOURS_PER_DAY assumption used by the
night-differential calculation) -- verify this against the employee's
actual contracted hours before trusting it in production.
"""

from django.apps import apps

from horilla.methods import get_horilla_model_class
from payroll.methods.methods import get_daily_salary
from payroll.models.models import Contract

STANDARD_HOURS_PER_DAY = 8
STANDARD_MINUTES_PER_DAY = STANDARD_HOURS_PER_DAY * 60


def _duration_to_minutes(duration):
    """
    Parse a "HH:MM" duration string (as returned by
    AttendanceLateComeEarlyOut.get_late_early_duration()) into total minutes.
    """
    if not duration:
        return 0
    hours, minutes = duration.split(":")
    return int(hours) * 60 + int(minutes)


def calculate_late_undertime(**kwargs):
    """
    Calculate the Late and Undertime pay reductions earned (lost) by the
    employee within the given period.

    Args:
        employee (Employee): The employee for whom to calculate the amounts.
        start_date (date): The start date of the payroll period.
        end_date (date): The end date of the payroll period.

    Returns:
        dict: {"total": float (negative or zero), "late_amount": float,
        "late_minutes": float, "undertime_amount": float,
        "undertime_minutes": float}
    """
    employee = kwargs["employee"]
    start_date = kwargs["start_date"]
    end_date = kwargs["end_date"]

    empty_result = {
        "total": 0,
        "late_amount": 0,
        "late_minutes": 0,
        "undertime_amount": 0,
        "undertime_minutes": 0,
    }

    if not apps.is_installed("attendance"):
        return empty_result

    contract = Contract.objects.filter(
        employee_id=employee, contract_status="active"
    ).first()
    if not contract:
        return empty_result

    daily_rate = get_daily_salary(wage=contract.wage, wage_date=start_date)[
        "day_wage"
    ]
    if not daily_rate:
        return empty_result

    per_minute_rate = daily_rate / STANDARD_MINUTES_PER_DAY

    AttendanceLateComeEarlyOut = get_horilla_model_class(
        app_label="attendance", model="attendancelatecomeearlyout"
    )
    records = AttendanceLateComeEarlyOut.objects.filter(
        employee_id=employee,
        attendance_id__attendance_date__range=(start_date, end_date),
    )

    late_minutes = 0
    undertime_minutes = 0
    for record in records:
        minutes = _duration_to_minutes(record.get_late_early_duration())
        if record.type == "late_come":
            late_minutes += minutes
        elif record.type == "early_out":
            undertime_minutes += minutes

    late_amount = round(late_minutes * per_minute_rate, 2)
    undertime_amount = round(undertime_minutes * per_minute_rate, 2)

    return {
        "total": -(late_amount + undertime_amount),
        "late_amount": late_amount,
        "late_minutes": late_minutes,
        "undertime_amount": undertime_amount,
        "undertime_minutes": undertime_minutes,
    }
