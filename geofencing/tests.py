from django.contrib.auth.models import AnonymousUser, User
from django.core.paginator import Paginator
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase
from django.urls import reverse
from unittest.mock import patch

from attendance.views.portal import _enforced_assigned_geofences
from base.models import Department
from employee.models import Employee

from .models import GeoFencing
from .views import (
    _clear_employee_geofence_coverage,
    _delete_geofence,
    _geo_assignment_context,
    _sync_employee_geofence_coverage,
    geo_location_config,
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

    def test_employee_without_assignments_has_no_geofence_restriction(self):
        self.assertEqual(self._enforced_names(), set())

    def test_sync_coverage_assigns_only_selected_active_geofences(self):
        _sync_employee_geofence_coverage(self.employee, [self.main])

        self.assertEqual(
            set(self.employee.assigned_geofences.values_list("name", flat=True)),
            {"Main"},
        )
        self.assertFalse(self.main.excluded_employees.filter(pk=self.employee.pk).exists())
        self.assertEqual(self._enforced_names(), {"Main"})

    def test_sync_coverage_readds_previously_excluded_geofence(self):
        _sync_employee_geofence_coverage(self.employee, [self.main])
        _sync_employee_geofence_coverage(self.employee, [self.main, self.test])

        self.assertFalse(self.main.excluded_employees.filter(pk=self.employee.pk).exists())
        self.assertFalse(self.test.excluded_employees.filter(pk=self.employee.pk).exists())
        self.assertEqual(self._enforced_names(), {"Main", "Test"})

    def test_clear_coverage_removes_all_assignments(self):
        self.employee.assigned_geofences.set([self.main, self.test])

        _clear_employee_geofence_coverage(self.employee)

        self.assertFalse(self.employee.assigned_geofences.exists())
        self.assertFalse(
            self.inactive.excluded_employees.filter(pk=self.employee.pk).exists()
        )
        self.assertEqual(self._enforced_names(), set())

    def test_delete_geofence_detaches_only_that_geofence(self):
        self.employee.assigned_geofences.set([self.main, self.test])
        self.main.excluded_employees.add(self.employee)

        _delete_geofence(self.main)

        self.assertFalse(GeoFencing.objects.filter(pk=self.main.pk).exists())
        self.employee.refresh_from_db()
        self.assertEqual(
            set(self.employee.assigned_geofences.values_list("name", flat=True)),
            {"Test"},
        )
        self.assertTrue(GeoFencing.objects.filter(pk=self.test.pk).exists())


class EmployeeGeofenceAssignmentListTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.ada = self._create_employee(
            "Ada", "Lovelace", "EMP-001"
        )
        self.grace = self._create_employee(
            "Grace", "Hopper", "EMP-002"
        )
        self.alan = self._create_employee(
            "Alan", "Turing", "EMP-004"
        )
        self.department = Department(department="Engineering")
        Department.objects.bulk_create([self.department])
        self.alan.employee_work_info.department_id = self.department
        self.alan.employee_work_info.save(update_fields=["department_id"])
        self.inactive = self._create_employee(
            "Ada", "Inactive", "EMP-003", is_active=False
        )

    def _create_employee(self, first_name, last_name, employee_no, is_active=True):
        return Employee.objects.create(
            employee_first_name=first_name,
            employee_last_name=last_name,
            employee_no=employee_no,
            email=f"{employee_no.lower()}@example.com",
            phone=f"0917000{employee_no[-3:]}",
            gender="male",
            is_active=is_active,
        )

    def _context(self, params=None, per_page=2):
        request = self.factory.get("/attendance/settings/geo-fence/", params or {})
        request.user = AnonymousUser()
        with patch(
            "geofencing.views.paginator_qry",
            side_effect=lambda queryset, page: Paginator(queryset, per_page).get_page(page),
        ):
            return _geo_assignment_context(request)

    def test_search_matches_name_and_employee_number_but_excludes_inactive(self):
        context = self._context({"search": "ada lovelace"})
        self.assertEqual(
            list(context["employees"].object_list.values_list("pk", flat=True)),
            [self.ada.pk],
        )

        context = self._context({"search": "emp-002"})
        self.assertEqual(
            list(context["employees"].object_list.values_list("pk", flat=True)),
            [self.grace.pk],
        )

        context = self._context({"search": "ada inactive"})
        self.assertEqual(context["employees"].paginator.count, 0)

        context = self._context({"search": "engineering"})
        self.assertEqual(
            list(context["employees"].object_list.values_list("pk", flat=True)),
            [self.alan.pk],
        )

        context = self._context({"search": "a"})
        self.assertGreater(context["employees"].paginator.count, 0)

    def test_pagination_orders_employees_and_preserves_search_query(self):
        context = self._context({"search": "emp", "page": "2"})
        self.assertEqual(context["employees"].number, 2)
        self.assertEqual(context["employees"].paginator.num_pages, 2)
        self.assertEqual(context["pd"], "search=emp")

    def test_assignment_template_contains_htmx_search_and_pagination_controls(self):
        from pathlib import Path

        template = (
            Path(__file__).resolve().parent
            / "templates"
            / "geo_assignment_list.html"
        ).read_text(encoding="utf-8")

        self.assertIn("{% url 'geo-assignments' %}", template)
        self.assertIn('type="submit"', template)
        self.assertIn('{% trans "Search" %}', template)
        search_input = template[template.index('<input type="text"'):template.index('/>', template.index('<input type="text"'))]
        self.assertNotIn("hx-get", search_input)
        self.assertNotIn("hx-trigger", search_input)
        self.assertIn('min-height: 280px', template)
        self.assertNotIn('<div class="oh-404">', template)
        self.assertIn("{% trans \"First\" %}", template)
        self.assertIn("{% trans \"Previous\" %}", template)
        self.assertIn("{% trans \"Next\" %}", template)
        self.assertIn("{% trans \"Last\" %}", template)

    def test_geofence_delete_uses_htmx_form_with_csrf_token(self):
        from pathlib import Path

        template = (
            Path(__file__).resolve().parent
            / "templates"
            / "geo_config.html"
        ).read_text(encoding="utf-8")

        delete_form_start = template.index(
            '<form hx-post="{% url \'geo-config\' %}?action=delete&pk={{ geo.pk }}"'
        )
        delete_form_end = template.index("</form>", delete_form_start)
        delete_form = template[delete_form_start:delete_form_end]

        self.assertIn('hx-target="#geo"', delete_form)
        self.assertIn("hx-swap=\"innerHTML\"", delete_form)
        self.assertIn("hx-confirm=\"Delete geofence", delete_form)
        self.assertIn("{% csrf_token %}", delete_form)
        self.assertNotIn("geoDeleteConfirm", template)


class GeoFenceDeleteViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="geofence-delete-user",
            email="geofence-delete@example.com",
            password="password",
        )
        self.employee = Employee.objects.create(
            employee_first_name="Assigned",
            employee_last_name="Employee",
            email="assigned-geofence@example.com",
            phone="09170000002",
            gender="male",
            is_active=True,
        )
        self.deleted_geofence = GeoFencing.objects.create(
            name="Delete Me",
            latitude=14.6,
            longitude=121.0,
            radius_in_meters=100,
            start=True,
        )
        self.other_geofence = GeoFencing.objects.create(
            name="Keep Me",
            latitude=14.7,
            longitude=121.1,
            radius_in_meters=100,
            start=True,
        )
        self.employee.assigned_geofences.set(
            [self.deleted_geofence, self.other_geofence]
        )

    def test_delete_detaches_selected_fence_and_keeps_other_fence(self):
        config_url = reverse("geo-config")
        request = RequestFactory().post(
            f"{config_url}?action=delete&pk={self.deleted_geofence.pk}",
            {"csrfmiddlewaretoken": "test-csrf-token"},
        )
        request.user = self.user
        SessionMiddleware(lambda _request: None).process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)

        # Keep the permission decorator in the test while bypassing only the
        # global login middleware, which redirects users without an employee
        # profile before the view can run.
        with patch(
            "geofencing.views.paginator_qry",
            side_effect=lambda queryset, page: Paginator(queryset, 20).get_page(page),
        ):
            response = geo_location_config.__wrapped__(request)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            GeoFencing.objects.filter(pk=self.deleted_geofence.pk).exists()
        )
        self.employee.refresh_from_db()
        self.assertEqual(
            set(self.employee.assigned_geofences.values_list("name", flat=True)),
            {"Keep Me"},
        )
        self.assertTrue(
            GeoFencing.objects.filter(pk=self.other_geofence.pk).exists()
        )
