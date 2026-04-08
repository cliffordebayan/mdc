"""
self_service.py

Public employee self-service clock in/out views.
No authentication required - suitable for kiosk-style access.
Employees identify themselves by badge ID or name.
"""

import logging
from datetime import date, datetime, timedelta

from django.conf import settings
from django.utils import timezone

from django.contrib.auth.models import User
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from attendance.methods.utils import (
    format_time,
    shift_schedule_today,
    strtime_seconds,
)
from attendance.models import (
    Attendance,
    AttendanceActivity,
    AttendanceGeneralSetting,
)
from attendance.views.clock_in_out import (
    clock_in_attendance_and_activity,
    clock_out_attendance_and_activity,
)
from base.models import Company, EmployeeShiftDay
from employee.models import Employee

logger = logging.getLogger(__name__)


def public_self_service(request):
    """
    Render the public self-service clock in/out page.
    No authentication required.
    """
    server_now = timezone.now()
    return render(
        request,
        "attendance/self_service/self_service.html",
        {
            "server_time_iso": server_now.isoformat(),
            "TIME_ZONE": settings.TIME_ZONE,
        },
    )


@csrf_exempt
@require_http_methods(["POST"])
def employee_lookup(request):
    """
    Search for employees by badge_id or name.
    Returns JSON list of matching employees.

    POST params:
        query (str): Search string (name or badge ID)

    Returns:
        JSON: [
            {
                "id": int,
                "badge_id": str,
                "name": str,
                "avatar": str,
                "is_clocked_in": bool
            }
        ]
    """
    try:
        # Handle both POST data and FormData
        query = request.POST.get("query", "").strip()

        logger.info(f"Employee lookup query: {query}, POST data: {request.POST}")

        if not query or len(query) < 2:
            return JsonResponse({"success": True, "results": []})

        # Search by badge_id, first name, or last name
        employees = Employee.objects.filter(
            Q(badge_id__icontains=query)
            | Q(employee_first_name__icontains=query)
            | Q(employee_last_name__icontains=query),
            is_active=True,
        )[:10]

        results = []
        for emp in employees:
            # Check if employee is currently clocked in
            is_clocked_in = AttendanceActivity.objects.filter(
                employee_id=emp, clock_out__isnull=True
            ).exists()

            results.append({
                "id": emp.id,
                "badge_id": emp.badge_id or "",
                "name": emp.get_full_name(),
                "avatar": emp.get_avatar(),
                "is_clocked_in": is_clocked_in,
            })

        return JsonResponse({"success": True, "results": results})
    except Exception as e:
        logger.error(f"Employee lookup error: {str(e)}", exc_info=True)
        return JsonResponse(
            {"success": False, "message": f"Search error: {str(e)}"}, status=200
        )


@csrf_exempt
@require_http_methods(["POST"])
def public_clock_in(request):
    """
    Clock in an employee with selfie photo and GPS location.

    POST params:
        employee_id (int): Employee ID
        selfie (file): Selfie image file
        latitude (float): GPS latitude
        longitude (float): GPS longitude

    Returns:
        JSON: {
            "success": bool,
            "message": str,
            "employee_name": str,
            "clock_in_time": str
        }
    """
    try:
        # Get employee ID from POST data
        employee_id = request.POST.get("employee_id")

        logger.info(f"Clock in request - employee_id: {employee_id}, POST keys: {list(request.POST.keys())}, FILES keys: {list(request.FILES.keys())}")

        if not employee_id:
            return JsonResponse(
                {"success": False, "message": "Employee ID required"}, status=200
            )

        # Require GPS location
        latitude = request.POST.get("latitude")
        longitude = request.POST.get("longitude")
        if not latitude or not longitude:
            return JsonResponse(
                {"success": False, "message": "Location is required to clock in. Please enable location access and try again."},
                status=200,
            )

        # Find employee
        try:
            employee = Employee.objects.get(id=employee_id, is_active=True)
        except Employee.DoesNotExist:
            logger.warning(f"Employee not found: {employee_id}")
            return JsonResponse(
                {"success": False, "message": "Employee not found"}, status=200
            )

        # Check if employee has work info
        work_info = getattr(employee, 'employee_work_info', None)

        if not work_info or work_info is None:
            logger.warning(f"No work info for employee: {employee_id}")
            return JsonResponse(
                {"success": False, "message": "Employee work information not configured"},
                status=200,
            )

        # Check if already clocked in
        if AttendanceActivity.objects.filter(
            employee_id=employee, clock_out__isnull=True
        ).exists():
            logger.info(f"Employee already clocked in: {employee_id}")
            return JsonResponse(
                {"success": False, "message": f"{employee.get_full_name()} is already clocked in"},
                status=200,
            )

        # Get shift and schedule info
        shift = work_info.shift_id

        if not shift:
            logger.warning(f"No shift configured for employee: {employee_id}")
            return JsonResponse(
                {"success": False, "message": "Employee shift not configured"}, status=200
            )

        # Get current date and time
        date_today = date.today()
        datetime_now = datetime.now()
        day_name = date_today.strftime("%A").lower()

        try:
            day = EmployeeShiftDay.objects.get(day=day_name)
        except EmployeeShiftDay.DoesNotExist:
            logger.error(f"Shift day configuration error for {day_name}")
            return JsonResponse(
                {"success": False, "message": "Shift day configuration error"}, status=200
            )

        # Get shift schedule
        now_str = datetime_now.strftime("%H:%M")
        now_sec = strtime_seconds(now_str)
        mid_day_sec = strtime_seconds("12:00")
        minimum_hour, start_time_sec, end_time_sec = shift_schedule_today(
            day=day, shift=shift
        )

        # Handle night shift
        attendance_date = date_today
        if start_time_sec > end_time_sec:  # Night shift
            if mid_day_sec > now_sec:
                # Before noon - belongs to yesterday's shift
                date_yesterday = date_today - timedelta(days=1)
                day_yesterday = date_yesterday.strftime("%A").lower()

                try:
                    day = EmployeeShiftDay.objects.get(day=day_yesterday)
                except EmployeeShiftDay.DoesNotExist:
                    logger.error(f"Shift day configuration error for {day_yesterday}")
                    return JsonResponse(
                        {"success": False, "message": "Shift day configuration error"},
                        status=200,
                    )

                minimum_hour, start_time_sec, end_time_sec = shift_schedule_today(
                    day=day, shift=shift
                )
                attendance_date = date_yesterday

        # Call the business logic function
        try:
            attendance = clock_in_attendance_and_activity(
                employee=employee,
                date_today=date_today,
                attendance_date=attendance_date,
                day=day,
                now=now_str,
                shift=shift,
                minimum_hour=minimum_hour,
                start_time=start_time_sec,
                end_time=end_time_sec,
                in_datetime=datetime_now,
            )
        except Exception as e:
            logger.error(f"Clock in error for {employee.id}: {str(e)}", exc_info=True)
            return JsonResponse(
                {"success": False, "message": f"Clock in failed: {str(e)}"}, status=200
            )

        # Get the newly created activity and ensure location_verified is set
        activity = AttendanceActivity.objects.filter(
            employee_id=employee, clock_out__isnull=True
        ).last()

        if not activity:
            logger.error(f"Activity not created for employee {employee.id}")
            return JsonResponse(
                {"success": False, "message": "Activity creation failed"}, status=200
            )

        # Ensure location_verified has a value (required field)
        if activity.location_verified is None:
            activity.location_verified = False
            activity.save()

        # Save selfie photo
        if "selfie" in request.FILES:
            activity.clock_in_selfie = request.FILES["selfie"]

        # Save GPS location
        try:
            latitude = request.POST.get("latitude")
            longitude = request.POST.get("longitude")
            if latitude and longitude:
                activity.latitude = float(latitude)
                activity.longitude = float(longitude)
                activity.location_verified = True
            else:
                activity.location_verified = False
        except (ValueError, TypeError):
            activity.location_verified = False

        activity.save()

        return JsonResponse({
            "success": True,
            "message": f"{employee.get_full_name()} clocked in successfully",
            "employee_name": employee.get_full_name(),
            "clock_in_time": datetime_now.strftime("%I:%M %p"),
        })

    except Exception as e:
        logger.error(f"Public clock in error: {str(e)}")
        return JsonResponse(
            {"success": False, "message": f"Error: {str(e)}"}, status=500
        )


@csrf_exempt
@require_http_methods(["POST"])
def public_clock_out(request):
    """
    Clock out an employee with selfie photo and GPS location.

    POST params:
        employee_id (int): Employee ID
        selfie (file): Selfie image file
        latitude (float): GPS latitude
        longitude (float): GPS longitude

    Returns:
        JSON: {
            "success": bool,
            "message": str,
            "employee_name": str,
            "clock_out_time": str,
            "worked_hours": str
        }
    """
    try:
        # Get employee ID from POST data
        employee_id = request.POST.get("employee_id")

        logger.info(f"Clock out request - employee_id: {employee_id}")

        if not employee_id:
            return JsonResponse(
                {"success": False, "message": "Employee ID required"}, status=200
            )

        # Require GPS location
        latitude = request.POST.get("latitude")
        longitude = request.POST.get("longitude")
        if not latitude or not longitude:
            return JsonResponse(
                {"success": False, "message": "Location is required to clock out. Please enable location access and try again."},
                status=200,
            )

        # Find employee
        try:
            employee = Employee.objects.get(id=employee_id, is_active=True)
        except Employee.DoesNotExist:
            logger.warning(f"Clock out - Employee not found: {employee_id}")
            return JsonResponse(
                {"success": False, "message": "Employee not found"}, status=200
            )

        # Check if employee is clocked in
        open_activity = AttendanceActivity.objects.filter(
            employee_id=employee, clock_out__isnull=True
        ).last()

        if not open_activity:
            logger.info(f"Clock out - Employee not clocked in: {employee_id}")
            return JsonResponse(
                {"success": False, "message": f"{employee.get_full_name()} is not clocked in"},
                status=200,
            )

        # Get current date and time
        date_today = date.today()
        datetime_now = datetime.now()
        now_str = datetime_now.strftime("%H:%M")

        # Call the business logic function
        try:
            attendance = clock_out_attendance_and_activity(
                employee=employee,
                date_today=date_today,
                now=now_str,
                out_datetime=datetime_now,
            )
        except Exception as e:
            logger.error(f"Clock out error for {employee.id}: {str(e)}", exc_info=True)
            return JsonResponse(
                {"success": False, "message": f"Clock out failed: {str(e)}"}, status=200
            )

        if not attendance:
            logger.warning(f"No active attendance found for employee {employee.id}")
            return JsonResponse(
                {"success": False, "message": "No active attendance found"}, status=200
            )

        # Get the just-closed activity and update it
        closed_activity = AttendanceActivity.objects.filter(
            employee_id=employee, attendance_date=attendance.attendance_date
        ).order_by("-id").first()

        # Ensure location_verified has a value (required field)
        if closed_activity and closed_activity.location_verified is None:
            closed_activity.location_verified = False

        if closed_activity and closed_activity.clock_out:
            # Save selfie photo
            if "selfie" in request.FILES:
                closed_activity.clock_out_selfie = request.FILES["selfie"]

            # Save GPS location
            try:
                latitude = request.POST.get("latitude")
                longitude = request.POST.get("longitude")
                if latitude and longitude:
                    closed_activity.latitude = float(latitude)
                    closed_activity.longitude = float(longitude)
                    closed_activity.location_verified = True
                else:
                    closed_activity.location_verified = False
            except (ValueError, TypeError):
                closed_activity.location_verified = False

            closed_activity.save()

        return JsonResponse({
            "success": True,
            "message": f"{employee.get_full_name()} clocked out successfully",
            "employee_name": employee.get_full_name(),
            "clock_out_time": datetime_now.strftime("%I:%M %p"),
            "worked_hours": attendance.attendance_worked_hour or "00:00",
        })

    except Exception as e:
        logger.error(f"Public clock out error: {str(e)}", exc_info=True)
        return JsonResponse(
            {"success": False, "message": f"Error: {str(e)}"}, status=200
        )
