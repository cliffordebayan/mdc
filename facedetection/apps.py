from django.apps import AppConfig


class FacedetectionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "facedetection"

    def ready(self):
        from django.urls import clear_url_caches, include, path

        from horilla.urls import urlpatterns

        urlpatterns.append(
            path("api/facedetection/", include("facedetection.urls")),
        )
        clear_url_caches()
        super().ready()
