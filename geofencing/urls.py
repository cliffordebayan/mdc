from django.urls import path

from .views import (
    GeoFencingEmployeeLocationCheckAPIView,
    GeoFencingSetupGetPostAPIView,
    GeoFencingSetupPutDeleteAPIView,
    GeoFencingSetUpPermissionCheck,
    geo_location_add_form,
    geo_location_config,
    geo_location_edit,
)

urlpatterns = [
    path("setup/", GeoFencingSetupGetPostAPIView.as_view()),
    path("setup/<int:pk>/", GeoFencingSetupPutDeleteAPIView.as_view()),
    path("setup-check/", GeoFencingSetUpPermissionCheck.as_view()),
    path("location-check/", GeoFencingEmployeeLocationCheckAPIView.as_view()),
    path("config/", geo_location_config, name="geo-config"),
    path("config/add/", geo_location_add_form, name="geo-config-add"),
    path("config/<int:pk>/edit/", geo_location_edit, name="geo-config-edit"),
]
