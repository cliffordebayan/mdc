"""
admin.py

This page is used to register attendance models with admins site.
"""

from django.contrib import admin

from .models import (
    Attendance,
    AttendanceActivity,
    AttendanceLateComeEarlyOut,
    AttendanceOverTime,
    AttendancePortalMultiPunchEmployee,
    AttendancePortalShiftContext,
    AttendancePortalShiftOverride,
    AttendanceRequestComment,
    AttendanceScheduleOverride,
    AttendanceValidationCondition,
    GraceTime,
    WorkRecords,
)

# Register your models here.
admin.site.register(Attendance)
admin.site.register(AttendanceActivity)
admin.site.register(AttendanceOverTime)
admin.site.register(AttendanceLateComeEarlyOut)
admin.site.register(AttendancePortalMultiPunchEmployee)
admin.site.register(AttendancePortalShiftContext)
admin.site.register(AttendancePortalShiftOverride)
admin.site.register(AttendanceScheduleOverride)
admin.site.register(AttendanceValidationCondition)
admin.site.register(GraceTime)
admin.site.register(AttendanceRequestComment)
admin.site.register(WorkRecords)
