from django.urls import path

from .views import (
    GeoFencingEmployeeLocationCheckAPIView,
    GeoFencingSetupGetPostAPIView,
    GeoFencingSetupPutDeleteAPIView,
    GeoFencingSetUpPermissionCheck,
    geo_location_add_form,
    geo_location_config,
    geo_location_edit,
    geo_assign_add,
    geo_assign_save,
    geo_assign_edit,
    geo_assign_delete,
    geo_quick_add,
)

urlpatterns = [
    path("setup/", GeoFencingSetupGetPostAPIView.as_view()),
    path("setup/<int:pk>/", GeoFencingSetupPutDeleteAPIView.as_view()),
    path("setup-check/", GeoFencingSetUpPermissionCheck.as_view()),
    path("location-check/", GeoFencingEmployeeLocationCheckAPIView.as_view()),
    path("config/", geo_location_config, name="geo-config"),
    path("config/add/", geo_location_add_form, name="geo-config-add"),
    path("config/<int:pk>/edit/", geo_location_edit, name="geo-config-edit"),
    path("config/assign/add/", geo_assign_add, name="geo-assign-add"),
    path("config/assign/save/", geo_assign_save, name="geo-assign-save"),
    path("config/assign/<int:emp_id>/edit/", geo_assign_edit, name="geo-assign-edit"),
    path("config/assign/<int:emp_id>/delete/", geo_assign_delete, name="geo-assign-delete"),
    path("config/quick-add/", geo_quick_add, name="geo-quick-add"),
]
