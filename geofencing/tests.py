from django.test import TestCase

from attendance.views.portal import _enforced_assigned_geofences
from employee.models import Employee

from .models import GeoFencing
from .views import (
    _clear_employee_geofence_coverage,
    _sync_employee_geofence_coverage,
)


class EmployeeGeofenceCoverageTests(TestCase):
    def setUp(self):
        self.employee = Employee.objects.create(
            employee_first_name="Test",
            employee_last_name="Employee",
            email="geo-employee@example.com",
            phone="09170000001",
            gender="male",
            is_active=True,
        )
        self.main = GeoFencing.objects.create(
            name="Main",
            latitude=14.6,
            longitude=121.0,
            radius_in_meters=100,
            start=True,
        )
        self.test = GeoFencing.objects.create(
            name="Test",
            latitude=14.7,
            longitude=121.1,
            radius_in_meters=100,
            start=True,
        )
        self.inactive = GeoFencing.objects.create(
            name="Inactive",
            latitude=14.8,
            longitude=121.2,
            radius_in_meters=100,
            start=False,
        )

    def _enforced_names(self):
        return {
            geofence.name
            for geofence in _enforced_assigned_geofences(self.employee)
        }

    def test_employee_without_exclusions_sees_active_geofences(self):
        self.assertEqual(self._enforced_names(), {"Main", "Test"})

    def test_sync_coverage_excludes_unselected_active_geofences(self):
        _sync_employee_geofence_coverage(self.employee, [self.main])

        self.assertEqual(
            set(self.employee.assigned_geofences.values_list("name", flat=True)),
            {"Main"},
        )
        self.assertFalse(self.main.excluded_employees.filter(pk=self.employee.pk).exists())
        self.assertTrue(self.test.excluded_employees.filter(pk=self.employee.pk).exists())
        self.assertEqual(self._enforced_names(), {"Main"})

    def test_sync_coverage_readds_previously_excluded_geofence(self):
        _sync_employee_geofence_coverage(self.employee, [self.main])
        _sync_employee_geofence_coverage(self.employee, [self.main, self.test])

        self.assertFalse(self.main.excluded_employees.filter(pk=self.employee.pk).exists())
        self.assertFalse(self.test.excluded_employees.filter(pk=self.employee.pk).exists())
        self.assertEqual(self._enforced_names(), {"Main", "Test"})

    def test_clear_coverage_excludes_employee_from_all_active_geofences(self):
        self.employee.assigned_geofences.set([self.main, self.test])

        _clear_employee_geofence_coverage(self.employee)

        self.assertFalse(self.employee.assigned_geofences.exists())
        self.assertTrue(self.main.excluded_employees.filter(pk=self.employee.pk).exists())
        self.assertTrue(self.test.excluded_employees.filter(pk=self.employee.pk).exists())
        self.assertFalse(
            self.inactive.excluded_employees.filter(pk=self.employee.pk).exists()
        )
        self.assertEqual(self._enforced_names(), set())
