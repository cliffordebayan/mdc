"""
attendance/sidebar.py
"""

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from base.templatetags.basefilters import is_reportingmanager

MENU = _("Attendance")
IMG_SRC = "images/ui/attendances.svg"


SUBMENUS = [
    {
        "menu": _("Dashboard"),
        "redirect": reverse("attendance-dashboard"),
        "accessibility": "attendance.sidebar.dashboard_accessibility",
    },
    {
        "menu": _("My Attendance"),
        "redirect": reverse("view-my-attendance"),
    },
    {
        "menu": _("Attendances"),
        "redirect": reverse("attendance-view"),
        "accessibility": "attendance.sidebar.attendances_accessibility",
    },
    {
        "menu": _("Attendance Activities"),
        "redirect": reverse("attendance-activity-view"),
    },
    # {
    #     "menu": _("Work Record"),
    #     "redirect": reverse("work-records"),
    #     "accessibility": "attendance.sidebar.work_record_accessibility",
    # },
    {
        "menu": _("Attendance Request"),
        "redirect": reverse("request-attendance-view"),
    },
    {
        "menu": _("Portal"),
        "redirect": reverse("public-portal"),
    },
]


def attendances_accessibility(request, submenu, user_perms, *args, **kwargs):
    """
    Check if the user has permission to view attendance or is a reporting manager.
    """
    return request.user.has_perm("attendance.view_attendance") or is_reportingmanager(
        request.user
    )


def work_record_accessibility(request, submenu, user_perms, *args, **kwargs):
    """
    Check if the user has permission to view attendance or is a reporting manager.
    """
    return request.user.has_perm("attendance.view_attendance") or is_reportingmanager(
        request.user
    )


def dashboard_accessibility(request, submenu, user_perms, *args, **kwargs):
    """
    Check if the user has permission to view attendance or is a reporting manager.
    """
    return request.user.has_perm("attendance.view_attendance") or is_reportingmanager(
        request.user
    )
