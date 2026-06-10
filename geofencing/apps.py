from django.apps import AppConfig


class GeofencingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "geofencing"

    def ready(self):
        from django.urls import clear_url_caches, include, path

        from horilla.horilla_settings import APPS
        from horilla.urls import urlpatterns

        APPS.append("geofencing")
        urlpatterns.append(
            path("api/geofencing/", include("geofencing.urls")),
        )
        clear_url_caches()
        super().ready()

