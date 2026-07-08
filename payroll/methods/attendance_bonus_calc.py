"""
Module: payroll.attendance_bonus_calc

This module determines Perfect Attendance Bonus eligibility for a given
employee and payroll period: the employee must have zero
late-come/undertime occurrences (attendance.AttendanceLateComeEarlyOut) and
zero unpaid absence days within the period. The bonus amount itself is
configured on the singleton payroll.PerfectAttendanceBonusSettings.

Like holiday pay and Late/Undertime, this is computed directly during
payslip generation rather than through the generic Allowance engine, and is
additive to gross pay.
"""

from django.apps import apps

from horilla.methods import get_horilla_model_class
from payroll.models.tax_models import PerfectAttendanceBonusSettings


def calculate_perfect_attendance_bonus(**kwargs):
    """
    Determine Perfect Attendance Bonus eligibility and amount for the
    employee within the given period.

    Args:
        employee (Employee): The employee for whom to check eligibility.
        start_date (date): The start date of the payroll period.
        end_date (date): The end date of the payroll period.
        unpaid_days (float): Unpaid absence days already computed for the
            period (from payroll.methods.methods basic pay computation).

    Returns:
        dict: {"amount": float, "eligible": bool}
    """
    employee = kwargs["employee"]
    start_date = kwargs["start_date"]
    end_date = kwargs["end_date"]
    unpaid_days = kwargs.get("unpaid_days") or 0

    empty_result = {"amount": 0, "eligible": False}

    settings_instance = PerfectAttendanceBonusSettings.objects.first()
    if not settings_instance or not settings_instance.is_enabled:
        return empty_result

    if unpaid_days > 0:
        return empty_result

    if not apps.is_installed("attendance"):
        return empty_result

    AttendanceLateComeEarlyOut = get_horilla_model_class(
        app_label="attendance", model="attendancelatecomeearlyout"
    )
    has_infractions = AttendanceLateComeEarlyOut.objects.filter(
        employee_id=employee,
        attendance_id__attendance_date__range=(start_date, end_date),
    ).exists()
    if has_infractions:
        return empty_result

    return {"amount": settings_instance.bonus_amount, "eligible": True}
