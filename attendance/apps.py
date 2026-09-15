"""
This module defines the configuration for the 'attendance' app within the Horilla HRMS project.
"""

from django.apps import AppConfig

from horilla.horilla_settings import APP_URLS


class AttendanceConfig(AppConfig):
    """
    Configures the 'attendance' app and performs additional setup during the app's
    initialization. This includes appending the 'attendance' URL patterns to the
    project's main urlpatterns. Automatic punch-out is handled by the background
    scheduler instead of running on every web request.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "attendance"

    def ready(self):
        from django.urls import include, path

        from attendance import scheduler, signals
        from horilla.horilla_settings import APPS
        from horilla.urls import urlpatterns

        APPS.append("attendance")
        urlpatterns.append(
            path("attendance/", include("attendance.urls")),
        )
        APP_URLS.append("attendance.urls")

        super().ready()
