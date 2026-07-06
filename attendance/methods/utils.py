"""
utils.py

This module is used write custom methods
"""

import calendar
from datetime import datetime, time, timedelta

import pandas as pd
from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import models
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.utils import timezone as django_timezone
from django.utils.translation import gettext_lazy as _

from base.methods import get_pagination
from base.models import WEEK_DAYS, CompanyLeaves, EmployeeShiftSchedule, Holidays
from employee.models import Employee
from horilla.horilla_settings import HORILLA_DATE_FORMATS, HORILLA_TIME_FORMATS

MONTH_MAPPING = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

WEEKDAY_SHIFT_FALLBACK_DAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
)


def format_time(seconds):
    """
    this method is used to formate seconds to H:M and return it
    args:
        seconds : seconds
    """

    hour = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds = int((seconds % 3600) % 60)
    return f"{hour:02d}:{minutes:02d}"


def strtime_seconds(time):
    """
    this method is used reconvert time in H:M formate string back to seconds and return it
    args:
        time : time in H:M format
    """

    ftr = [3600, 60, 1]
    return sum(a * b for a, b in zip(ftr, map(int, time.split(":"))))


def clock_time_seconds(value):
    """
    Convert a stored clock time value to seconds from midnight.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return strtime_seconds(":".join(value.split(":")[:2]))
    return strtime_seconds(value.strftime("%H:%M"))


def get_diff_obj(first_instance, other_instance, exclude_fields=None):
    """
    Compare the fields of two instances and identify the changes.

    Args:
        first_instance: The first instance to compare.
        other_instance: The second instance to compare.
        exclude_fields: A list of field names to exclude from comparison (optional).

    Returns:
        A dictionary of changed fields with their old and new values.
    """
    difference = {}

    fields_to_compare = first_instance._meta.fields

    if exclude_fields:
        fields_to_compare = [
            field for field in fields_to_compare if field.name not in exclude_fields
        ]

    for field in fields_to_compare:
        old_value = getattr(first_instance, field.name)
        new_value = getattr(other_instance, field.name)

        if old_value != new_value:
            difference[field.name] = (old_value, new_value)

    return difference


def get_diff_dict(first_dict, other_dict, model=None):
    """
    Compare two dictionaries and identify differing key-value pairs.

    Args:
        first_dict: The first dictionary to compare.
        other_dict: The second dictionary to compare.
        model: The model class (optional, for verbose names and type-specific formatting)

    Returns:
        A dictionary of differing keys with their old and new values.
    """
    difference = {}

    for key, value in first_dict.items():
        other_value = other_dict.get(key)
        if value == other_value:
            continue  # Skip if values are the same

        if not model:
            difference[key] = (value, other_value)
            continue

        # Fetch the model field metadata
        field = model._meta.get_field(key)
        verbose_key = field.verbose_name

        # Handle specific field types
        if isinstance(field, models.DateField):
            value = (
                datetime.strptime(value, "%Y-%m-%d").strftime("%d %b %Y")
                if value and value != "None"
                else value
            )
            other_value = (
                datetime.strptime(other_value, "%Y-%m-%d").strftime("%d %b %Y")
                if other_value and other_value != "None"
                else other_value
            )
        elif isinstance(field, models.TimeField):

            def format_time(val):
                if val and val != "None":
                    val += ":00" if len(val.split(":")) == 2 else ""
                    return datetime.strptime(val, "%H:%M:%S").strftime("%I:%M %p")
                return val

            value = format_time(value)
            other_value = format_time(other_value)
        elif isinstance(field, models.ForeignKey):
            value = (
                field.related_model.objects.get(id=value)
                if value and str(value).isdigit()
                else value
            )
            other_value = (
                field.related_model.objects.get(id=other_value)
                if other_value and str(other_value).isdigit()
                else other_value
            )

        # Add the difference
        difference[verbose_key] = (value, other_value)

    return difference


def employee_exists(request):
    """
    This method return the employee instance and work info if not exists return None instead
    """
    employee, employee_work_info = None, None
    try:
        employee = request.user.employee_get
        employee_work_info = employee.employee_work_info
    finally:
        return (employee, employee_work_info)


def shift_schedule_with_weekday_fallback(day, shift):
    """
    Return the shift schedule for the given day, falling back to the first
    configured Monday-Friday schedule for the same shift when the exact day is
    not configured.
    """
    if not shift:
        return None

    schedule = None
    if day:
        schedule = EmployeeShiftSchedule.objects.filter(
            shift_id=shift, day=day
        ).first()
    if schedule:
        return schedule

    return EmployeeShiftSchedule.objects.filter(
        shift_id=shift,
        day__day__in=WEEKDAY_SHIFT_FALLBACK_DAYS,
    ).first()


def shift_schedule_today(day, shift):
    """
    This function is used to find shift schedules for the day,
    it will returns min hour,start time seconds  end time seconds
    args:
        shift   : shift instance
        day     : shift day object
    """
    schedule_today = shift_schedule_with_weekday_fallback(day, shift)
    start_time_sec, end_time_sec, minimum_hour = 0, 0, "00:00"
    if schedule_today:
        minimum_hour = schedule_today.minimum_working_hour
        start_time_sec = strtime_seconds(schedule_today.start_time.strftime("%H:%M"))
        end_time_sec = strtime_seconds(schedule_today.end_time.strftime("%H:%M"))
    return (minimum_hour, start_time_sec, end_time_sec)


def calculate_worked_hours(employee, attendance_date):
    """
    Return total worked time for closed work activities on an attendance date.
    Break and lunch activities are intentionally excluded from worked hours.
    """

    from attendance.models import AttendanceActivity

    duration = 0
    attendance_activities = AttendanceActivity.objects.filter(
        employee_id=employee,
        attendance_date=attendance_date,
        activity_type="work",
        clock_out__isnull=False,
    )
    for attendance_activity in attendance_activities:
        in_datetime, out_datetime = activity_datetime(attendance_activity)
        difference = out_datetime - in_datetime
        duration += difference.days * 24 * 3600 + difference.seconds
    return format_time(duration)


def _local_naive_datetime(value):
    if not isinstance(value, datetime):
        return None
    if django_timezone.is_aware(value):
        return django_timezone.localtime(value).replace(tzinfo=None)
    return value


def _activity_start_datetime(activity):
    started_at = _local_naive_datetime(getattr(activity, "in_datetime", None))
    if started_at:
        return started_at

    clock_in_date = getattr(activity, "clock_in_date", None)
    clock_in = getattr(activity, "clock_in", None)
    if clock_in_date and clock_in:
        return datetime.combine(clock_in_date, clock_in)
    return None


def _activity_end_datetime(activity, current_time=None):
    clock_out_date = getattr(activity, "clock_out_date", None)
    clock_out = getattr(activity, "clock_out", None)
    if clock_out_date and clock_out:
        return datetime.combine(clock_out_date, clock_out)

    ended_at = _local_naive_datetime(getattr(activity, "out_datetime", None))
    if ended_at and clock_out:
        return ended_at

    if clock_out is None and current_time is not None:
        return _local_naive_datetime(current_time)
    return None


def _schedule_end_datetime(schedule, attendance_date):
    if not schedule or not attendance_date or not getattr(schedule, "end_time", None):
        return None

    start_time = getattr(schedule, "start_time", None)
    end_time = schedule.end_time
    is_night_shift = getattr(schedule, "is_night_shift", False)
    if start_time and start_time > end_time:
        is_night_shift = True
    end_date = attendance_date + timedelta(days=1) if is_night_shift else attendance_date
    return datetime.combine(end_date, end_time)


def calculate_schedule_end_overtime(
    schedule,
    work_activities,
    attendance_date,
    current_time=None,
):
    """
    Calculate overtime as work time after the scheduled shift end.
    """

    shift_end = _schedule_end_datetime(schedule, attendance_date)
    if not shift_end:
        return "00:00"

    last_clock_out = None
    for activity in work_activities or []:
        if getattr(activity, "activity_type", "work") != "work":
            continue
        ended_at = _activity_end_datetime(activity, current_time)
        if not ended_at:
            continue
        if last_clock_out is None or ended_at > last_clock_out:
            last_clock_out = ended_at

    if not last_clock_out or last_clock_out <= shift_end:
        return "00:00"

    return format_time(int((last_clock_out - shift_end).total_seconds()))


def schedule_end_overtime_calculation(
    attendance,
    work_activities=None,
    schedule=None,
    current_time=None,
):
    if not attendance:
        return "00:00"

    if schedule is None:
        schedule = shift_schedule_with_weekday_fallback(
            getattr(attendance, "attendance_day", None),
            getattr(attendance, "shift_id", None),
        )
    if work_activities is None:
        from attendance.models import AttendanceActivity

        work_activities = AttendanceActivity.objects.filter(
            employee_id=attendance.employee_id,
            attendance_date=attendance.attendance_date,
            activity_type="work",
        )
    return calculate_schedule_end_overtime(
        schedule,
        work_activities,
        attendance.attendance_date,
        current_time,
    )


def _mark_late_come(attendance, schedule):
    now_sec = clock_time_seconds(attendance.attendance_clock_in)
    if (
        now_sec is None
        or not schedule
        or not schedule.start_time
        or not schedule.end_time
    ):
        return

    start_time = strtime_seconds(schedule.start_time.strftime("%H:%M"))
    end_time = strtime_seconds(schedule.end_time.strftime("%H:%M"))
    mid_day_sec = strtime_seconds("12:00")
    should_create = False
    if start_time > end_time and start_time != end_time:
        should_create = now_sec < mid_day_sec or now_sec > start_time
    else:
        should_create = start_time < now_sec

    if should_create:
        from attendance.models import AttendanceLateComeEarlyOut

        AttendanceLateComeEarlyOut.objects.get_or_create(
            type="late_come",
            attendance_id=attendance,
            defaults={"employee_id": attendance.employee_id},
        )


def _mark_early_out(attendance, schedule):
    now_sec = clock_time_seconds(attendance.attendance_clock_out)
    if (
        now_sec is None
        or not schedule
        or not schedule.start_time
        or not schedule.end_time
    ):
        return

    start_time = strtime_seconds(schedule.start_time.strftime("%H:%M"))
    end_time = strtime_seconds(schedule.end_time.strftime("%H:%M"))
    mid_day_sec = strtime_seconds("12:00")
    should_create = False
    if start_time > end_time:
        should_create = (now_sec < mid_day_sec and now_sec < end_time) or (
            now_sec >= mid_day_sec
        )
    else:
        should_create = end_time > now_sec

    if should_create:
        from attendance.models import AttendanceLateComeEarlyOut

        AttendanceLateComeEarlyOut.objects.get_or_create(
            type="early_out",
            attendance_id=attendance,
            defaults={"employee_id": attendance.employee_id},
        )


def _duration_seconds(value):
    if value in (None, ""):
        return 0
    if isinstance(value, int):
        return value
    return strtime_seconds(str(value))


def _persist_recalculated_attendance(attendance):
    from attendance.models import Attendance

    update_values = {
        "attendance_day": attendance.attendance_day,
        "attendance_clock_in": attendance.attendance_clock_in,
        "attendance_clock_in_date": attendance.attendance_clock_in_date,
        "attendance_clock_out": attendance.attendance_clock_out,
        "attendance_clock_out_date": attendance.attendance_clock_out_date,
        "minimum_hour": attendance.minimum_hour,
        "attendance_worked_hour": attendance.attendance_worked_hour,
        "attendance_overtime": attendance.attendance_overtime,
        "attendance_validated": attendance.attendance_validated,
        "attendance_overtime_approve": attendance.attendance_overtime_approve,
        "at_work_second": attendance.at_work_second,
        "overtime_second": attendance.overtime_second,
        "approved_overtime_second": attendance.approved_overtime_second,
    }
    if getattr(attendance, "pk", None):
        Attendance.objects.filter(pk=attendance.pk).update(**update_values)
    else:
        attendance.save()


def _approved_leave_exclude_condition(employee, attendance_date):
    exclude_condition = Q()
    if not apps.is_installed("leave") or not hasattr(employee, "leaverequest_set"):
        return exclude_condition

    approved_leave_requests = employee.leaverequest_set.filter(
        start_date__lte=attendance_date,
        end_date__gte=attendance_date,
        status="approved",
    )

    for leave in approved_leave_requests:
        exclude_condition |= Q(attendance_date__range=(leave.start_date, leave.end_date))
    return exclude_condition


def _recalculate_overtime_account(employee, attendance_date):
    from attendance.models import Attendance, AttendanceOverTime

    month = attendance_date.strftime("%B").lower()
    year = attendance_date.year
    month_attendances = Attendance.objects.filter(
        employee_id=employee,
        attendance_date__month=attendance_date.month,
        attendance_date__year=year,
    )

    validated_attendances = month_attendances.filter(attendance_validated=True).exclude(
        _approved_leave_exclude_condition(employee, attendance_date)
    )
    worked_seconds = 0
    minimum_seconds = 0
    for attendance in validated_attendances:
        required_seconds = _duration_seconds(attendance.minimum_hour)
        at_work_seconds = attendance.at_work_second
        if at_work_seconds is None:
            at_work_seconds = _duration_seconds(attendance.attendance_worked_hour)
        worked_seconds += min(required_seconds, at_work_seconds)
        minimum_seconds += required_seconds

    approved_overtime_seconds = 0
    for attendance in month_attendances.filter(attendance_overtime_approve=True):
        approved_overtime_seconds += attendance.approved_overtime_second or 0

    overtime_account, _created = AttendanceOverTime.objects.get_or_create(
        employee_id=employee,
        month=month,
        year=year,
    )
    overtime_account.worked_hours = format_time(worked_seconds)
    overtime_account.pending_hours = format_time(minimum_seconds - worked_seconds)
    overtime_account.overtime = format_time(approved_overtime_seconds)
    overtime_account.save()


def recalculate_attendance_for_shift(shift):
    """
    Recalculate persisted attendance summaries and late/early records for a shift.
    """

    from attendance.models import (
        Attendance,
        AttendanceActivity,
        AttendanceLateComeEarlyOut,
    )
    from base.context_processors import enable_late_come_early_out_tracking
    from base.models import EmployeeShiftDay

    if not shift:
        return 0

    tracking_enabled = enable_late_come_early_out_tracking(None).get("tracking")
    recalculated_count = 0
    overtime_accounts_to_refresh = set()
    attendances = Attendance.objects.filter(shift_id=shift).select_related(
        "employee_id", "shift_id", "attendance_day"
    )

    for attendance in attendances.iterator():
        if not attendance.attendance_day and attendance.attendance_date:
            attendance.attendance_day = EmployeeShiftDay.objects.filter(
                day=attendance.attendance_date.strftime("%A").lower()
            ).first()

        schedule = shift_schedule_with_weekday_fallback(
            attendance.attendance_day,
            attendance.shift_id,
        )
        minimum_hour = schedule.minimum_working_hour if schedule else "00:00"
        minimum_hour = attendance_day_checking(
            str(attendance.attendance_date),
            minimum_hour,
        )

        work_activities = AttendanceActivity.objects.filter(
            employee_id=attendance.employee_id,
            attendance_date=attendance.attendance_date,
            activity_type="work",
        ).order_by("clock_in_date", "clock_in", "id")
        has_work_activities = work_activities.exists()
        has_any_clock_out = work_activities.filter(clock_out__isnull=False).exists()

        # Collect all event timestamps (both clock_in and clock_out) to find the
        # true first and last recorded times for the day. This ensures that if an
        # employee clocks in after their last clock-out and forgets to clock out,
        # that later clock-in is still captured as the effective clock-out time.
        all_event_times = []
        for activity in work_activities:
            if activity.clock_in and activity.clock_in_date:
                all_event_times.append((activity.clock_in_date, activity.clock_in))
            if activity.clock_out and activity.clock_out_date:
                all_event_times.append((activity.clock_out_date, activity.clock_out))
        all_event_times.sort()

        if all_event_times:
            first_date, first_time = all_event_times[0]
            attendance.attendance_clock_in = first_time
            attendance.attendance_clock_in_date = first_date

            if has_any_clock_out:
                last_date, last_time = all_event_times[-1]
                attendance.attendance_clock_out = last_time
                attendance.attendance_clock_out_date = last_date
            else:
                attendance.attendance_clock_out = None
                attendance.attendance_clock_out_date = None
        elif has_work_activities:
            attendance.attendance_clock_out = None
            attendance.attendance_clock_out_date = None

        attendance.minimum_hour = minimum_hour
        if has_work_activities:
            attendance.attendance_worked_hour = calculate_worked_hours(
                attendance.employee_id,
                attendance.attendance_date,
            )
        else:
            attendance.attendance_worked_hour = (
                attendance.attendance_worked_hour or "00:00"
            )
        attendance.attendance_overtime = schedule_end_overtime_calculation(
            attendance,
            work_activities=work_activities,
            schedule=schedule,
        )
        attendance.at_work_second = _duration_seconds(attendance.attendance_worked_hour)
        attendance.overtime_second = _duration_seconds(attendance.attendance_overtime)
        attendance.approved_overtime_second = (
            attendance.overtime_second
            if attendance.attendance_overtime_approve
            else 0
        )

        AttendanceLateComeEarlyOut.objects.filter(attendance_id=attendance).delete()
        if tracking_enabled:
            _mark_late_come(attendance, schedule)
            _mark_early_out(attendance, schedule)

        _persist_recalculated_attendance(attendance)
        if getattr(attendance, "pk", None):
            overtime_accounts_to_refresh.add(
                (attendance.employee_id, attendance.attendance_date)
            )
        recalculated_count += 1

    for employee, attendance_date in overtime_accounts_to_refresh:
        _recalculate_overtime_account(employee, attendance_date)

    return recalculated_count


def overtime_calculation(attendance):
    """
    This method is used to calculate overtime of the attendance after shift end.
    args:
        attendance : attendance instance
    """

    return schedule_end_overtime_calculation(attendance)


def is_reportingmanger(request, instance):
    """
    if the instance have employee id field then you can use this method to know the
    request user employee is the reporting manager of the instance
    args :
        request : request
        instance : an object or instance of any model contain employee_id foreign key field
    """

    manager = request.user.employee_get
    try:
        employee_workinfo_manager = (
            instance.employee_id.employee_work_info.reporting_manager_id
        )
    except Exception:
        return HttpResponse("This Employee Dont Have any work information")
    return manager == employee_workinfo_manager


def validate_hh_mm_ss_format(value):
    timeformat = "%H:%M:%S"
    try:
        validtime = datetime.strptime(value, timeformat)
        return validtime.time()  # Return the time object if needed
    except ValueError as e:
        raise ValidationError(_("Invalid format, it should be HH:MM:SS format"))


def validate_time_format(value):
    """
    this method is used to validate the format of duration like fields.
    """
    if value.count(":") == 2:
        # If the format is "H:MM:SS", check if it can be reduced to "HH:MM"
        # Django's DurationField internally converts it to a timedelta object, it becomes "0:00:00"
        value = ":".join(value.split(":")[:2])

    if len(value) > 6:
        raise ValidationError(_("Invalid format, it should be HH:MM format"))
    try:
        hour, minute = value.split(":")
        if len(hour) > 3 or len(minute) > 2:
            raise ValidationError(_("Invalid time"))
        hour = int(hour)
        minute = int(minute)
        if len(str(hour)) > 3 or len(str(minute)) > 2 or minute not in range(60):
            raise ValidationError(_("Invalid time, excepted MM:SS"))
    except ValueError as error:
        raise ValidationError(_("Invalid format")) from error


def attendance_date_validate(date):
    """
    Validates if the provided date is not a future date.

    :param date: The date to validate.
    :raises ValidationError: If the provided date is in the future.
    """
    today = datetime.today().date()
    if not date:
        raise ValidationError(_("Check date format."))
    elif date > today:
        raise ValidationError(_("You cannot choose a future date."))


def activity_datetime(attendance_activity):
    """
    This method is used to convert clock-in and clock-out of activity as datetime object
    args:
        attendance_activity : attendance activity instance
    """

    # in
    in_year = attendance_activity.clock_in_date.year
    in_month = attendance_activity.clock_in_date.month
    in_day = attendance_activity.clock_in_date.day
    in_hour = attendance_activity.clock_in.hour
    in_minute = attendance_activity.clock_in.minute
    # out
    out_year = attendance_activity.clock_out_date.year
    out_month = attendance_activity.clock_out_date.month
    out_day = attendance_activity.clock_out_date.day
    out_hour = attendance_activity.clock_out.hour
    out_minute = attendance_activity.clock_out.minute
    return datetime(in_year, in_month, in_day, in_hour, in_minute), datetime(
        out_year, out_month, out_day, out_hour, out_minute
    )


def get_week_start_end_dates(week):
    """
    This method is use to return the start and end date of the week
    """
    # Parse the ISO week date
    year, week_number = map(int, week.split("-W"))

    # Get the date of the first day of the week
    start_date = datetime.strptime(f"{year}-W{week_number}-1", "%Y-W%W-%w").date()

    # Calculate the end date by adding 6 days to the start date
    end_date = start_date + timedelta(days=6)

    return start_date, end_date


def get_month_start_end_dates(year_month):
    """
    This method is use to return the start and end date of the month
    """
    # split year and month separately
    year, month = map(int, year_month.split("-"))
    # Get the first day of the month
    start_date = datetime(year, month, 1).date()

    # Get the last day of the month
    _, last_day = calendar.monthrange(year, month)
    end_date = datetime(year, month, last_day).date()

    return start_date, end_date


def worked_hour_data(labels, records):
    """
    To find all the worked hours
    """
    data = {
        "label": "Worked Hours",
        "backgroundColor": "rgba(75, 192, 192, 0.6)",
    }
    dept_records = []
    for dept in labels:
        total_sum = records.filter(
            employee_id__employee_work_info__department_id__department=dept
        ).aggregate(total_sum=Sum("hour_account_second"))["total_sum"]
        dept_records.append(total_sum / 3600 if total_sum else 0)
    data["data"] = dept_records
    return data


def pending_hour_data(labels, records):
    """
    To find all the pending hours
    """
    data = {
        "label": "Pending Hours",
        "backgroundColor": "rgba(255, 99, 132, 0.6)",
    }
    dept_records = []
    for dept in labels:
        total_sum = records.filter(
            employee_id__employee_work_info__department_id__department=dept
        ).aggregate(total_sum=Sum("hour_pending_second"))["total_sum"]
        dept_records.append(total_sum / 3600 if total_sum else 0)
    data["data"] = dept_records
    return data


def get_employee_last_name(attendance):
    """
    This method is used to return the last name
    """
    if attendance.employee_id.employee_last_name:
        return attendance.employee_id.employee_last_name
    return ""


def attendance_day_checking(attendance_date, minimum_hour):
    # Convert the string to a datetime object
    attendance_datetime = datetime.strptime(attendance_date, "%Y-%m-%d")

    # Extract name of the day
    attendance_day = attendance_datetime.strftime("%A")

    # Taking all holidays into a list
    leaves = []
    holidays = Holidays.objects.all()
    for holi in holidays:
        start_date = holi.start_date
        end_date = holi.end_date

        # Convert start_date and end_date to datetime objects
        start_date = datetime.strptime(str(start_date), "%Y-%m-%d")
        end_date = datetime.strptime(str(end_date), "%Y-%m-%d")

        # Add dates in between start date and end date including both
        current_date = start_date
        while current_date <= end_date:
            leaves.append(current_date.strftime("%Y-%m-%d"))
            current_date += timedelta(days=1)

    # Checking attendance date is in holiday list, if found making the minimum hour to 00:00
    for leave in leaves:
        if str(leave) == str(attendance_date):
            minimum_hour = "00:00"
            break

    # Making a dictonary contains week day value and leave day pairs
    company_leaves = {}
    company_leave = CompanyLeaves.objects.all()
    for com_leave in company_leave:
        a = dict(WEEK_DAYS).get(com_leave.based_on_week_day)
        b = com_leave.based_on_week
        company_leaves[b] = a

    # Checking the attendance date is in which week
    week_in_month = str(((attendance_datetime.day - 1) // 7 + 1) - 1)

    # Checking the attendance date is in the company leave or not
    for pairs in company_leaves.items():
        # For all weeks based_on_week is None
        if str(pairs[0]) == "None":
            if str(pairs[1]) == str(attendance_day):
                minimum_hour = "00:00"
                break
        # Checking with based_on_week and attendance_date week
        if str(pairs[0]) == week_in_month:
            if str(pairs[1]) == str(attendance_day):
                minimum_hour = "00:00"
                break
    return minimum_hour


def paginator_qry(qryset, page_number):
    """
    This method is used to paginate queryset
    """
    paginator = Paginator(qryset, get_pagination())
    qryset = paginator.get_page(page_number)
    return qryset


def monthly_leave_days(month, year):
    leave_dates = []
    holidays = Holidays.objects.filter(start_date__month=month, start_date__year=year)
    leave_dates += list(holidays.values_list("start_date", flat=True))

    company_leaves = CompanyLeaves.objects.all()
    for company_leave in company_leaves:
        year = year
        month = month
        based_on_week = company_leave.based_on_week
        based_on_week_day = company_leave.based_on_week_day
        if based_on_week != None:
            calendar.setfirstweekday(6)
            month_calendar = calendar.monthcalendar(year, month)
            weeks = month_calendar[int(based_on_week)]
            weekdays_in_weeks = [day for day in weeks if day != 0]
            for day in weekdays_in_weeks:
                date_name = datetime.strptime(
                    f"{year}-{month:02}-{day:02}", "%Y-%m-%d"
                ).date()
                if (
                    date_name.weekday() == int(based_on_week_day)
                    and date_name not in leave_dates
                ):
                    leave_dates.append(date_name)
        else:
            calendar.setfirstweekday(0)
            month_calendar = calendar.monthcalendar(year, month)
            for week in month_calendar:
                if week[int(based_on_week_day)] != 0:
                    date_name = datetime.strptime(
                        f"{year}-{month:02}-{week[int(based_on_week_day)]:02}",
                        "%Y-%m-%d",
                    ).date()
                    if date_name not in leave_dates:
                        leave_dates.append(date_name)
    return leave_dates


def validate_time_in_minutes(value):
    """
    this method is used to validate the format of duration like fields.
    """
    if len(value) > 5:
        raise ValidationError(_("Invalid format, it should be MM:SS format"))
    try:
        minutes, sec = value.split(":")
        if len(minutes) > 2 or len(sec) > 2:
            raise ValidationError(_("Invalid time, excepted MM:SS"))
        minutes = int(minutes)
        sec = int(sec)
        if minutes not in range(60) or sec not in range(60):
            raise ValidationError(_("Invalid time, excepted MM:SS"))
    except ValueError as e:
        raise ValidationError(_("Invalid format,  excepted MM:SS")) from e


class Request:
    """
    Represents a request for clock-in or clock-out.

    Attributes:
    - user: The user associated with the request.
    - date: The date of the request.
    - time: The time of the request.
    - path: The path associated with the request (default: "/").
    - session: The session data associated with the request (default: {"title": None}).
    """

    def __init__(
        self,
        user,
        date,
        time,
        datetime,
    ) -> None:
        self.user = user
        self.path = "/"
        self.session = {"title": None}
        self.date = date
        self.time = time
        self.datetime = datetime
        self.META = META()


class META:
    """
    Provides access to HTTP metadata keys.
    """

    @classmethod
    def keys(cls):
        """
        Retrieve the list of available HTTP metadata keys.

        Returns:
            list: A list of HTTP metadata keys.
        """
        return ["HTTP_HX_REQUEST"]


def parse_time(time_str):
    if isinstance(time_str, time):  # Check if it's already a time object
        return time_str

    if isinstance(time_str, str):
        for format_str in HORILLA_TIME_FORMATS.values():
            try:
                return datetime.strptime(time_str, format_str).time()
            except ValueError:
                continue
    return None


def parse_date(date_str, error_key, activity):
    try:
        return pd.to_datetime(date_str).date()
    except (pd.errors.ParserError, ValueError):
        activity[error_key] = f"Invalid date format for {error_key.split()[-1]}"
        return None


def parse_datetime(date_str, time_str):
    return (
        datetime.strptime(f"{date_str} {time_str[:5]}", "%Y-%m-%d %H:%M")
        if date_str and time_str
        else None
    )


def get_date(date):
    if isinstance(date, datetime):
        return date
    elif isinstance(date, str):
        for format_name, format_str in HORILLA_DATE_FORMATS.items():
            try:
                return datetime.strptime(date, format_str)
            except ValueError:
                continue
    return None


def sort_activity_dicts(activity_dicts):

    for activity in activity_dicts:
        activity["Date In"] = get_date(activity["Date In"])

    # Filter out any entries where the date could not be parsed
    activity_dicts = [
        activity for activity in activity_dicts if activity["Date In"] is not None
    ]
    sorted_activity_dicts = sorted(activity_dicts, key=lambda x: x["Date In"])
    return sorted_activity_dicts
