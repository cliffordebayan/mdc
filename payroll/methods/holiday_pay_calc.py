"""
Module: payroll.holiday_pay_calc

This module calculates Philippine holiday pay premiums, ordinary rest-day
pay, and night differential pay for a given employee and payroll period.

Unlike SSS/PhilHealth/Pag-IBIG/BIR, these are earnings (added to gross pay),
not deductions.

All rates are configurable via the singleton payroll.HolidayPaySettings
(edited under Settings > Payroll > Holiday & Rest Day Pay), rather than
hardcoded, since the applicable percentages are set by DOLE and can change.

Important: the basic pay computation in payroll.methods.methods
(monthly_computation/daily_computation) excludes holiday dates from the
paid working-days count entirely (see base.methods.get_working_days,
which subtracts holiday_dates before counting total_working_days). That
means, under this codebase's current basic-pay logic, a holiday currently
contributes 0 to basic_pay whether or not the employee worked it. This
module therefore adds the *full* applicable percentage for each holiday
(not just the "extra" premium on top of an assumed-already-paid base day):
  - Regular holiday, worked: 200% of the daily rate (260% if it also falls
    on the employee's scheduled rest day).
  - Regular holiday, unworked but eligible (present the prior scheduled
    workday, or on approved leave covering the holiday): 100% of the daily
    rate (the "no work, still pay" rule).
  - Special (non-working) holiday, worked: 130% of the daily rate (150% if
    it also falls on the employee's scheduled rest day).
  - Special holiday, unworked: 0 (no work, no pay for special holidays).

Ordinary (non-holiday) rest days are NOT excluded from the paid
working-days count, so that day's regular pay is already included in
basic_pay. Working a scheduled rest day therefore adds only the
*premium* portion (default 30%) on top, not a full 130%.

Night differential (+10% by default for hours worked between 10:00 PM and
6:00 AM) is computed directly from AttendanceActivity clock in/out
datetimes, since EmployeeShift.is_night_shift is only a whole-shift flag
and too coarse for per-hour computation. The hourly rate is derived from
the daily rate assuming a standard 8-hour workday -- verify this
assumption against the employee's actual contracted hours before trusting
it in production.
"""

import datetime

from django.apps import apps

from base.methods import is_holiday
from base.models import EmployeeShiftSchedule
from horilla.methods import get_horilla_model_class
from payroll.methods.methods import get_daily_salary
from payroll.models.models import Contract
from payroll.models.tax_models import HolidayPaySettings

NIGHT_WINDOW_START = datetime.time(22, 0)
NIGHT_WINDOW_END = datetime.time(6, 0)
STANDARD_HOURS_PER_DAY = 8


def _get_holiday_pay_settings():
    """
    Retrieve the singleton holiday pay settings, falling back to an
    unsaved instance (which carries the model's field defaults) if none
    has been configured yet.
    """
    return HolidayPaySettings.objects.first() or HolidayPaySettings()


def _is_employee_rest_day(employee, check_date):
    """
    Check whether check_date falls on the employee's scheduled rest day,
    based on their shift's per-day-of-week schedule
    (base.models.EmployeeShiftSchedule.is_rest_day).
    """
    work_info = getattr(employee, "employee_work_info", None)
    shift = getattr(work_info, "shift_id", None)
    if not shift:
        return False

    day_name = check_date.strftime("%A").lower()
    schedule = EmployeeShiftSchedule.objects.filter(
        shift_id=shift, day__day=day_name
    ).first()
    return bool(schedule and schedule.is_rest_day)


def _is_eligible_for_unworked_holiday_pay(employee, holiday_date):
    """
    Check whether the employee is eligible for the "no work, still pay"
    regular-holiday-pay rule: present (validated attendance) on the
    scheduled workday immediately before the holiday, or on approved leave
    covering the holiday date.
    """
    Attendance = get_horilla_model_class(app_label="attendance", model="attendance")
    previous_date = holiday_date - datetime.timedelta(days=1)
    for _ in range(7):
        if not is_holiday(previous_date):
            break
        previous_date = previous_date - datetime.timedelta(days=1)

    present_prior_day = Attendance.objects.filter(
        employee_id=employee,
        attendance_date=previous_date,
        attendance_validated=True,
    ).exists()
    if present_prior_day:
        return True

    if apps.is_installed("leave"):
        LeaveRequest = get_horilla_model_class(app_label="leave", model="leaverequest")
        on_approved_leave = LeaveRequest.objects.filter(
            employee_id=employee,
            start_date__lte=holiday_date,
            end_date__gte=holiday_date,
            status="approved",
        ).exists()
        if on_approved_leave:
            return True

    return False


def calculate_holiday_pay(**kwargs):
    """
    Calculate the holiday pay premium and ordinary rest-day pay premium
    earned by the employee within the given period.

    Args:
        employee (Employee): The employee for whom to calculate holiday pay.
        start_date (date): The start date of the payroll period.
        end_date (date): The end date of the payroll period.

    Returns:
        dict: {"holiday_pay": float, "breakdown": [ {date, holiday_name,
        holiday_type, category, worked, rate, amount}, ... ]}. holiday_type
        is None for a plain rest-day (non-holiday) entry; category
        distinguishes each DOLE pay scenario (e.g. "regular_holiday_worked",
        "regular_holiday_rest_day_worked", "rest_day_worked").
    """
    employee = kwargs["employee"]
    start_date = kwargs["start_date"]
    end_date = kwargs["end_date"]

    if not apps.is_installed("attendance"):
        return {"holiday_pay": 0, "breakdown": []}

    contract = Contract.objects.filter(
        employee_id=employee, contract_status="active"
    ).first()
    if not contract:
        return {"holiday_pay": 0, "breakdown": []}

    Attendance = get_horilla_model_class(app_label="attendance", model="attendance")
    settings_instance = _get_holiday_pay_settings()

    breakdown = []
    total = 0.0
    current_date = start_date
    while current_date <= end_date:
        holiday = is_holiday(current_date)
        is_rest_day = _is_employee_rest_day(employee, current_date)

        if not holiday and not is_rest_day:
            current_date += datetime.timedelta(days=1)
            continue
        if holiday and holiday.holiday_type not in ("regular", "special"):
            current_date += datetime.timedelta(days=1)
            continue

        worked = Attendance.objects.filter(
            employee_id=employee,
            attendance_date=current_date,
            attendance_validated=True,
        ).exists()

        rate = None
        category = None
        holiday_type = holiday.holiday_type if holiday else None
        holiday_name = holiday.name if holiday else None

        if holiday and holiday.holiday_type == "regular":
            if worked and is_rest_day:
                rate = settings_instance.regular_holiday_rest_day_worked_rate
                category = "regular_holiday_rest_day_worked"
            elif worked:
                rate = settings_instance.regular_holiday_worked_rate
                category = "regular_holiday_worked"
            elif _is_eligible_for_unworked_holiday_pay(employee, current_date):
                rate = settings_instance.regular_holiday_unworked_rate
                category = "regular_holiday_unworked"
        elif holiday and holiday.holiday_type == "special":
            if worked and is_rest_day:
                rate = settings_instance.special_holiday_rest_day_worked_rate
                category = "special_holiday_rest_day_worked"
            elif worked:
                rate = settings_instance.special_holiday_worked_rate
                category = "special_holiday_worked"
            else:
                rate = settings_instance.special_holiday_unworked_rate
                category = "special_holiday_unworked"
        elif not holiday and is_rest_day and worked:
            rate = settings_instance.rest_day_worked_premium_rate
            category = "rest_day_worked"

        if rate:
            daily_rate = get_daily_salary(
                wage=contract.wage, wage_date=current_date
            )["day_wage"]
            amount = round(daily_rate * rate / 100, 2)
            if amount:
                total += amount
                breakdown.append(
                    {
                        "date": current_date.strftime("%Y-%m-%d"),
                        "holiday_name": holiday_name,
                        "holiday_type": holiday_type,
                        "category": category,
                        "worked": worked,
                        "rate": rate,
                        "amount": amount,
                    }
                )
        current_date += datetime.timedelta(days=1)

    return {"holiday_pay": round(total, 2), "breakdown": breakdown}


def calculate_night_differential(**kwargs):
    """
    Calculate the night differential pay earned by the employee within the
    given period, for hours worked between 10:00 PM and 6:00 AM.

    Args:
        employee (Employee): The employee for whom to calculate night
            differential.
        start_date (date): The start date of the payroll period.
        end_date (date): The end date of the payroll period.

    Returns:
        dict: {"night_differential": float, "night_hours": float}
    """
    employee = kwargs["employee"]
    start_date = kwargs["start_date"]
    end_date = kwargs["end_date"]

    if not apps.is_installed("attendance"):
        return {"night_differential": 0, "night_hours": 0}

    contract = Contract.objects.filter(
        employee_id=employee, contract_status="active"
    ).first()
    if not contract:
        return {"night_differential": 0, "night_hours": 0}

    AttendanceActivity = get_horilla_model_class(
        app_label="attendance", model="attendanceactivity"
    )
    activities = AttendanceActivity.objects.filter(
        employee_id=employee,
        attendance_date__range=(start_date, end_date),
        clock_in_date__isnull=False,
        clock_out_date__isnull=False,
        clock_out__isnull=False,
    )

    total_night_seconds = 0
    for activity in activities:
        # Use the naive clock_in_date/clock_in and clock_out_date/clock_out
        # wall-clock fields (as Attendance.duration() does), not
        # in_datetime/out_datetime, since those are timezone-converted to
        # UTC on save and would compare wall-clock hours against the wrong
        # calendar day/offset.
        clock_in = datetime.datetime.combine(activity.clock_in_date, activity.clock_in)
        clock_out = datetime.datetime.combine(
            activity.clock_out_date, activity.clock_out
        )
        day = clock_in.date()
        while day <= clock_out.date():
            window_start = datetime.datetime.combine(day, NIGHT_WINDOW_START)
            window_end = datetime.datetime.combine(
                day + datetime.timedelta(days=1), NIGHT_WINDOW_END
            )
            overlap_start = max(clock_in, window_start)
            overlap_end = min(clock_out, window_end)
            if overlap_end > overlap_start:
                total_night_seconds += (overlap_end - overlap_start).total_seconds()
            day += datetime.timedelta(days=1)

    night_hours = total_night_seconds / 3600
    daily_rate = get_daily_salary(wage=contract.wage, wage_date=start_date)["day_wage"]
    hourly_rate = daily_rate / STANDARD_HOURS_PER_DAY if daily_rate else 0
    settings_instance = _get_holiday_pay_settings()
    night_differential = round(
        night_hours * hourly_rate * settings_instance.night_differential_rate / 100, 2
    )

    return {
        "night_differential": night_differential,
        "night_hours": round(night_hours, 2),
    }
