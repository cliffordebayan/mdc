import json
import importlib
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase
from django.urls import resolve
from django.urls.exceptions import Resolver404

from attendance.forms import AttendanceActivityExportForm, AttendanceExportForm
from attendance.models import Attendance
from attendance.views.clock_in_out import clock_out_attendance_and_activity
from attendance.views.portal import public_clock_in, public_clock_out, verify_pin
from attendance.views import views as attendance_views
from attendance.views.views import _delete_blocked_message, build_my_attendance_activity_meta


def attach_session(request):
    middleware = SessionMiddleware(lambda req: HttpResponse())
    middleware.process_request(request)
    return request


class ClockOutAttendanceAndActivityTests(SimpleTestCase):
    def _setup_clock_out_mocks(self):
        employee = MagicMock()
        today = date(2026, 4, 10)
        out_dt = datetime(2026, 4, 10, 17, 0, 0)

        attendance_activity = MagicMock()
        attendance_activity.attendance_date = today

        first_activity = MagicMock()
        second_activity = MagicMock()

        activities_qs = MagicMock()
        open_qs = MagicMock()
        open_qs.exists.return_value = True
        open_qs.last.return_value = attendance_activity

        activities_qs.filter.side_effect = lambda **kwargs: (
            open_qs if kwargs == {"clock_out__isnull": True} else [first_activity, second_activity]
        )

        attendance = MagicMock()
        attendance_qs = MagicMock()
        attendance_qs.order_by.return_value = [attendance]

        return {
            "employee": employee,
            "today": today,
            "out_dt": out_dt,
            "attendance": attendance,
            "activities_qs": activities_qs,
            "attendance_qs": attendance_qs,
            "first_activity": first_activity,
            "second_activity": second_activity,
        }

    @patch("attendance.views.clock_in_out.attendance_validate", return_value=True)
    @patch("attendance.views.clock_in_out.overtime_calculation", return_value="00:00")
    @patch("attendance.views.clock_in_out.format_time", return_value="08:00")
    @patch("attendance.views.clock_in_out.activity_datetime")
    @patch("attendance.views.clock_in_out.Attendance")
    @patch("attendance.views.clock_in_out.AttendanceActivity")
    def test_clock_out_auto_validate_true_keeps_existing_behavior(
        self,
        attendance_activity_model,
        attendance_model,
        activity_datetime_mock,
        _format_time_mock,
        _overtime_mock,
        attendance_validate_mock,
    ):
        ctx = self._setup_clock_out_mocks()
        attendance_activity_model.objects.filter.return_value.order_by.return_value = ctx["activities_qs"]
        attendance_model.objects.filter.return_value = ctx["attendance_qs"]
        activity_datetime_mock.side_effect = [
            (datetime(2026, 4, 10, 9, 0, 0), datetime(2026, 4, 10, 12, 0, 0)),
            (datetime(2026, 4, 10, 13, 0, 0), datetime(2026, 4, 10, 18, 0, 0)),
        ]

        result = clock_out_attendance_and_activity(
            employee=ctx["employee"],
            date_today=ctx["today"],
            now="17:00",
            out_datetime=ctx["out_dt"],
            auto_validate=True,
        )

        self.assertEqual(result, ctx["attendance"])
        attendance_validate_mock.assert_called_once_with(ctx["attendance"])
        self.assertTrue(ctx["attendance"].attendance_validated)

    @patch("attendance.views.clock_in_out.attendance_validate")
    @patch("attendance.views.clock_in_out.overtime_calculation", return_value="00:00")
    @patch("attendance.views.clock_in_out.format_time", return_value="08:00")
    @patch("attendance.views.clock_in_out.activity_datetime")
    @patch("attendance.views.clock_in_out.Attendance")
    @patch("attendance.views.clock_in_out.AttendanceActivity")
    def test_clock_out_auto_validate_false_forces_not_validated(
        self,
        attendance_activity_model,
        attendance_model,
        activity_datetime_mock,
        _format_time_mock,
        _overtime_mock,
        attendance_validate_mock,
    ):
        ctx = self._setup_clock_out_mocks()
        attendance_activity_model.objects.filter.return_value.order_by.return_value = ctx["activities_qs"]
        attendance_model.objects.filter.return_value = ctx["attendance_qs"]
        activity_datetime_mock.side_effect = [
            (datetime(2026, 4, 10, 9, 0, 0), datetime(2026, 4, 10, 12, 0, 0)),
            (datetime(2026, 4, 10, 13, 0, 0), datetime(2026, 4, 10, 18, 0, 0)),
        ]

        result = clock_out_attendance_and_activity(
            employee=ctx["employee"],
            date_today=ctx["today"],
            now="17:00",
            out_datetime=ctx["out_dt"],
            auto_validate=False,
        )

        self.assertEqual(result, ctx["attendance"])
        attendance_validate_mock.assert_not_called()
        self.assertFalse(ctx["attendance"].attendance_validated)


class PortalClockOutTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @patch("attendance.views.portal._reverse_geocode", return_value="Test Address")
    @patch("attendance.views.portal.clock_out_attendance_and_activity")
    @patch("attendance.views.portal.get_real_now", return_value=datetime(2026, 4, 10, 17, 0, 0))
    @patch("attendance.views.portal.AttendanceActivity")
    @patch("attendance.views.portal.Employee")
    @patch("attendance.views.portal._ip_is_allowed", return_value=True)
    def test_portal_clock_out_calls_helper_with_auto_validate_false(
        self,
        _ip_allowed_mock,
        employee_model,
        attendance_activity_model,
        _now_mock,
        clock_out_helper_mock,
        _reverse_geocode_mock,
    ):
        request = self.factory.post(
            "/attendance/portal/clock-out/",
            {"employee_id": "1", "latitude": "14.6", "longitude": "121.0"},
        )
        attach_session(request)
        request.session["portal_pin_verification"] = {
            "employee_id": "1",
            "verified_at": datetime.now().timestamp(),
        }

        employee = MagicMock()
        employee.id = 1
        employee.get_full_name.return_value = "Test Employee"
        employee_model.objects.get.return_value = employee

        open_activity_qs = MagicMock()
        open_activity = MagicMock()
        open_activity.location_verified = True
        open_activity.clock_out = datetime(2026, 4, 10, 17, 0, 0)
        open_activity_qs.order_by.return_value.last.return_value = open_activity
        attendance_activity_model.objects.filter.return_value = open_activity_qs

        attendance = MagicMock()
        attendance.attendance_date = date(2026, 4, 10)
        attendance.attendance_worked_hour = "08:00"
        attendance.attendance_validated = False
        clock_out_helper_mock.return_value = attendance

        response = public_clock_out(request)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertTrue(payload["success"])
        clock_out_helper_mock.assert_called_once()
        self.assertFalse(clock_out_helper_mock.call_args.kwargs["auto_validate"])

    @patch("attendance.views.portal._reverse_geocode", return_value="Clock Out Address")
    @patch("attendance.views.portal.clock_out_attendance_and_activity")
    @patch("attendance.views.portal.get_real_now", return_value=datetime(2026, 4, 10, 17, 0, 0))
    @patch("attendance.views.portal.AttendanceActivity")
    @patch("attendance.views.portal.Employee")
    @patch("attendance.views.portal._ip_is_allowed", return_value=True)
    def test_portal_clock_out_updates_open_activity_record_locations(
        self,
        _ip_allowed_mock,
        employee_model,
        attendance_activity_model,
        _now_mock,
        clock_out_helper_mock,
        _reverse_geocode_mock,
    ):
        request = self.factory.post(
            "/attendance/portal/clock-out/",
            {"employee_id": "1", "latitude": "14.6", "longitude": "121.0"},
        )
        attach_session(request)
        request.session["portal_pin_verification"] = {
            "employee_id": "1",
            "verified_at": datetime.now().timestamp(),
        }

        employee = MagicMock()
        employee.id = 1
        employee.get_full_name.return_value = "Test Employee"
        employee_model.objects.get.return_value = employee

        open_activity_qs = MagicMock()
        open_activity = MagicMock()
        open_activity.location_verified = True
        open_activity.clock_out = datetime(2026, 4, 10, 17, 0, 0)
        open_activity.clock_in_gps_address = "Clock In Address"
        open_activity.clock_in_latitude = 14.5001
        open_activity.clock_in_longitude = 120.9001
        open_activity.clock_out_gps_address = None
        open_activity.clock_out_latitude = None
        open_activity.clock_out_longitude = None
        open_activity_qs.order_by.return_value.last.return_value = open_activity
        attendance_activity_model.objects.filter.return_value = open_activity_qs

        attendance = MagicMock()
        attendance.attendance_date = date(2026, 4, 10)
        attendance.attendance_worked_hour = "08:00"
        clock_out_helper_mock.return_value = attendance

        response = public_clock_out(request)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertTrue(payload["success"])
        open_activity.refresh_from_db.assert_called_once()
        self.assertEqual(open_activity.clock_in_gps_address, "Clock In Address")
        self.assertEqual(open_activity.clock_out_gps_address, "Clock Out Address")
        self.assertEqual(open_activity.clock_out_latitude, 14.6)
        self.assertEqual(open_activity.clock_out_longitude, 121.0)

    @patch("attendance.views.portal.clock_out_attendance_and_activity")
    @patch("attendance.views.portal.get_real_now", return_value=datetime(2026, 4, 10, 17, 0, 0))
    @patch("attendance.views.portal.AttendanceActivity")
    @patch("attendance.views.portal.Employee")
    @patch("attendance.views.portal._ip_is_allowed", return_value=True)
    def test_portal_clock_out_invalid_coordinates_preserve_clock_in_location(
        self,
        _ip_allowed_mock,
        employee_model,
        attendance_activity_model,
        _now_mock,
        clock_out_helper_mock,
    ):
        request = self.factory.post(
            "/attendance/portal/clock-out/",
            {"employee_id": "1", "latitude": "invalid", "longitude": "invalid"},
        )
        attach_session(request)
        request.session["portal_pin_verification"] = {
            "employee_id": "1",
            "verified_at": datetime.now().timestamp(),
        }

        employee = MagicMock()
        employee.id = 1
        employee.get_full_name.return_value = "Test Employee"
        employee_model.objects.get.return_value = employee

        open_activity_qs = MagicMock()
        open_activity = MagicMock()
        open_activity.location_verified = True
        open_activity.clock_out = datetime(2026, 4, 10, 17, 0, 0)
        open_activity.clock_in_gps_address = "Clock In Address"
        open_activity.clock_in_latitude = 14.5001
        open_activity.clock_in_longitude = 120.9001
        open_activity.clock_out_gps_address = None
        open_activity.clock_out_latitude = None
        open_activity.clock_out_longitude = None
        open_activity_qs.order_by.return_value.last.return_value = open_activity
        attendance_activity_model.objects.filter.return_value = open_activity_qs

        attendance = MagicMock()
        attendance.attendance_date = date(2026, 4, 10)
        attendance.attendance_worked_hour = "08:00"
        clock_out_helper_mock.return_value = attendance

        response = public_clock_out(request)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertTrue(payload["success"])
        self.assertEqual(open_activity.clock_in_gps_address, "Clock In Address")
        self.assertEqual(open_activity.clock_in_latitude, 14.5001)
        self.assertEqual(open_activity.clock_in_longitude, 120.9001)
        self.assertIsNone(open_activity.clock_out_gps_address)
        self.assertTrue(open_activity.location_verified)


class PortalPinVerificationTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @patch("attendance.views.portal.Employee")
    @patch("attendance.views.portal._ip_is_allowed", return_value=True)
    def test_verify_pin_success_sets_session(
        self,
        _ip_allowed_mock,
        employee_model,
    ):
        request = self.factory.post(
            "/attendance/portal/verify-pin/",
            {"employee_id": "1", "pin": "123456"},
        )
        attach_session(request)

        employee = MagicMock()
        employee.id = 1
        employee.employee_work_info = MagicMock(pin="123456")
        employee_model.objects.get.return_value = employee

        response = verify_pin(request)
        payload = json.loads(response.content)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["attempts_remaining"], 3)
        self.assertEqual(
            request.session["portal_pin_verification"]["employee_id"],
            "1",
        )

    @patch("attendance.views.portal.Employee")
    @patch("attendance.views.portal._ip_is_allowed", return_value=True)
    def test_verify_pin_wrong_pin_tracks_attempts_and_resets_on_third(
        self,
        _ip_allowed_mock,
        employee_model,
    ):
        request = self.factory.post(
            "/attendance/portal/verify-pin/",
            {"employee_id": "1", "pin": "000000"},
        )
        attach_session(request)

        employee = MagicMock()
        employee.id = 1
        employee.employee_work_info = MagicMock(pin="123456")
        employee_model.objects.get.return_value = employee

        first = json.loads(verify_pin(request).content)
        second = json.loads(verify_pin(request).content)
        third = json.loads(verify_pin(request).content)

        self.assertFalse(first["success"])
        self.assertEqual(first["attempts_remaining"], 2)
        self.assertFalse(second["success"])
        self.assertEqual(second["attempts_remaining"], 1)
        self.assertFalse(third["success"])
        self.assertEqual(third["attempts_remaining"], 0)
        self.assertTrue(third["reset_required"])
        self.assertNotIn("1", request.session.get("portal_pin_attempts", {}))

    @patch("attendance.views.portal.Employee")
    @patch("attendance.views.portal._ip_is_allowed", return_value=True)
    def test_verify_pin_rejects_non_six_digit_pin(
        self,
        _ip_allowed_mock,
        employee_model,
    ):
        request = self.factory.post(
            "/attendance/portal/verify-pin/",
            {"employee_id": "1", "pin": "12345"},
        )
        attach_session(request)

        response = verify_pin(request)
        payload = json.loads(response.content)

        self.assertFalse(payload["success"])
        self.assertEqual(payload["attempts_remaining"], 3)
        self.assertIn("exactly 6 digits", payload["message"])
        employee_model.objects.get.assert_not_called()

    @patch("attendance.views.portal.Employee")
    @patch("attendance.views.portal._ip_is_allowed", return_value=True)
    def test_verify_pin_rejects_missing_stored_pin(
        self,
        _ip_allowed_mock,
        employee_model,
    ):
        request = self.factory.post(
            "/attendance/portal/verify-pin/",
            {"employee_id": "1", "pin": "123456"},
        )
        attach_session(request)

        employee = MagicMock()
        employee.id = 1
        employee.employee_work_info = MagicMock(pin=None)
        employee_model.objects.get.return_value = employee

        response = verify_pin(request)
        payload = json.loads(response.content)

        self.assertFalse(payload["success"])
        self.assertIn("not configured", payload["message"])

    @patch("attendance.views.portal.Employee")
    @patch("attendance.views.portal._ip_is_allowed", return_value=True)
    def test_verify_pin_rejects_invalid_stored_pin_format(
        self,
        _ip_allowed_mock,
        employee_model,
    ):
        request = self.factory.post(
            "/attendance/portal/verify-pin/",
            {"employee_id": "1", "pin": "123456"},
        )
        attach_session(request)

        employee = MagicMock()
        employee.id = 1
        employee.employee_work_info = MagicMock(pin="12AB56")
        employee_model.objects.get.return_value = employee

        response = verify_pin(request)
        payload = json.loads(response.content)

        self.assertFalse(payload["success"])
        self.assertIn("invalid", payload["message"])


class PortalPinGateTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @patch("attendance.views.portal._ip_is_allowed", return_value=True)
    def test_clock_out_requires_verified_pin(
        self,
        _ip_allowed_mock,
    ):
        request = self.factory.post("/attendance/portal/clock-out/", {"employee_id": "1"})
        attach_session(request)

        response = public_clock_out(request)
        payload = json.loads(response.content)

        self.assertFalse(payload["success"])
        self.assertIn("PIN verification required", payload["message"])

    @patch("attendance.views.portal._ip_is_allowed", return_value=True)
    def test_clock_out_rejects_mismatched_pin_session(
        self,
        _ip_allowed_mock,
    ):
        request = self.factory.post("/attendance/portal/clock-out/", {"employee_id": "1"})
        attach_session(request)
        request.session["portal_pin_verification"] = {
            "employee_id": "2",
            "verified_at": datetime.now().timestamp(),
        }

        response = public_clock_out(request)
        payload = json.loads(response.content)

        self.assertFalse(payload["success"])
        self.assertIn("does not match", payload["message"])

    @patch("attendance.views.portal._ip_is_allowed", return_value=True)
    def test_clock_out_rejects_expired_pin_session(
        self,
        _ip_allowed_mock,
    ):
        request = self.factory.post("/attendance/portal/clock-out/", {"employee_id": "1"})
        attach_session(request)
        request.session["portal_pin_verification"] = {
            "employee_id": "1",
            "verified_at": (datetime.now() - timedelta(minutes=5)).timestamp(),
        }

        response = public_clock_out(request)
        payload = json.loads(response.content)

        self.assertFalse(payload["success"])
        self.assertIn("PIN verification required", payload["message"])

    @patch("attendance.views.portal._ip_is_allowed", return_value=True)
    def test_clock_out_allows_flow_after_valid_pin_session(
        self,
        _ip_allowed_mock,
    ):
        request = self.factory.post("/attendance/portal/clock-out/", {"employee_id": "1"})
        attach_session(request)
        request.session["portal_pin_verification"] = {
            "employee_id": "1",
            "verified_at": datetime.now().timestamp(),
        }

        response = public_clock_out(request)
        payload = json.loads(response.content)

        self.assertFalse(payload["success"])
        self.assertIn("Location is required", payload["message"])

    @patch("attendance.views.portal._ip_is_allowed", return_value=True)
    def test_clock_in_requires_verified_pin(
        self,
        _ip_allowed_mock,
    ):
        request = self.factory.post("/attendance/portal/clock-in/", {"employee_id": "1"})
        attach_session(request)

        response = public_clock_in(request)
        payload = json.loads(response.content)

        self.assertFalse(payload["success"])
        self.assertIn("PIN verification required", payload["message"])

    @patch("attendance.views.portal._ip_is_allowed", return_value=True)
    def test_clock_in_allows_flow_after_valid_pin_session(
        self,
        _ip_allowed_mock,
    ):
        request = self.factory.post("/attendance/portal/clock-in/", {"employee_id": "1"})
        attach_session(request)
        request.session["portal_pin_verification"] = {
            "employee_id": "1",
            "verified_at": datetime.now().timestamp(),
        }

        response = public_clock_in(request)
        payload = json.loads(response.content)

        self.assertFalse(payload["success"])
        self.assertIn("Location is required", payload["message"])


class PortalUrlRoutingTests(SimpleTestCase):
    def test_portal_urls_resolve(self):
        self.assertEqual(resolve("/attendance/portal/").url_name, "public-portal")
        self.assertEqual(
            resolve("/attendance/portal/employee-lookup/").url_name,
            "portal-employee-lookup",
        )
        self.assertEqual(
            resolve("/attendance/portal/verify-pin/").url_name,
            "portal-verify-pin",
        )
        self.assertEqual(
            resolve("/attendance/portal/clock-in/").url_name,
            "portal-clock-in",
        )
        self.assertEqual(
            resolve("/attendance/portal/clock-out/").url_name,
            "portal-clock-out",
        )
        self.assertEqual(
            resolve("/attendance/portal/server-time/").url_name,
            "portal-server-time",
        )

    def test_self_service_urls_do_not_resolve_after_hard_cutover(self):
        with self.assertRaises(Resolver404):
            resolve("/attendance/self-service/")
        with self.assertRaises(Resolver404):
            resolve("/attendance/self-service/employee-lookup/")
        with self.assertRaises(Resolver404):
            resolve("/attendance/self-service/clock-in/")
        with self.assertRaises(Resolver404):
            resolve("/attendance/self-service/clock-out/")
        with self.assertRaises(Resolver404):
            resolve("/attendance/self-service/server-time/")


class AttendanceDeleteTests(SimpleTestCase):
    @patch("attendance.models.HorillaModel.delete")
    @patch("attendance.models.AttendanceActivity")
    @patch("attendance.models.AttendanceLateComeEarlyOut")
    def test_delete_removes_related_late_early_and_activities(
        self,
        late_early_model,
        activity_model,
        horilla_delete_mock,
    ):
        attendance = Attendance(attendance_date=date(2026, 4, 10))
        employee = MagicMock()
        overtime_qs = MagicMock()
        overtime_qs.exists.return_value = False
        employee.employee_overtime.filter.return_value = overtime_qs
        attendance.employee_id_id = 1
        attendance._state.fields_cache["employee_id"] = employee

        attendance.delete()

        late_early_model.objects.filter.assert_called_once_with(attendance_id=attendance)
        activity_model.objects.filter.assert_called_once_with(
            attendance_date=attendance.attendance_date,
            employee_id=employee,
        )
        horilla_delete_mock.assert_called_once()

    @patch("attendance.models.HorillaModel.delete")
    @patch("attendance.models.AttendanceActivity")
    @patch("attendance.models.AttendanceLateComeEarlyOut")
    def test_delete_updates_overtime_when_available(
        self,
        _late_early_model,
        _activity_model,
        _horilla_delete_mock,
    ):
        attendance = Attendance(attendance_date=date(2026, 4, 10))
        employee = MagicMock()
        overtime_qs = MagicMock()
        overtime_qs.exists.return_value = True
        overtime_instance = MagicMock()
        overtime_qs.first.return_value = overtime_instance
        employee.employee_overtime.filter.return_value = overtime_qs
        attendance.employee_id_id = 1
        attendance._state.fields_cache["employee_id"] = employee
        attendance.update_ot = MagicMock()

        attendance.delete()

        attendance.update_ot.assert_called_once_with(overtime_instance)


class DeleteBlockedMessageTests(SimpleTestCase):
    def test_delete_blocked_message_includes_related_model_names(self):
        protected_one = MagicMock()
        protected_one._meta.verbose_name = "work record"
        protected_two = MagicMock()
        protected_two._meta.verbose_name = "late come early out"

        message = _delete_blocked_message([protected_one, protected_two])

        self.assertIn("Deletion blocked by related records:", message)
        self.assertIn("Work record", message)
        self.assertIn("Late come early out", message)


class AttendanceDeleteAuthorizationTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _build_request(self, path):
        request = self.factory.post(path)
        employee = MagicMock()
        employee.is_active = True

        user = MagicMock()
        user.is_authenticated = True
        user.is_active = True
        user.employee_get = employee
        user.has_perm.return_value = False

        request.user = user
        request.session = {}
        return request

    @patch("employee.models.EmployeeWorkInformation.objects.filter")
    @patch("attendance.views.views.messages.success")
    @patch("attendance.views.views.HorillaRedirect", return_value=HttpResponse(status=302))
    @patch("attendance.views.views.Attendance.objects.get")
    def test_reporting_manager_can_access_attendance_delete(
        self,
        attendance_get_mock,
        _horilla_redirect_mock,
        _messages_success_mock,
        reporting_manager_filter_mock,
    ):
        request = self._build_request("/attendance/attendance-delete/1/")
        reporting_manager_filter_mock.return_value.exists.return_value = True
        attendance = MagicMock()
        attendance.attendance_date = date(2026, 4, 10)
        attendance.employee_id.employee_overtime.filter.return_value.last.return_value = None
        attendance_get_mock.return_value = attendance

        response = attendance_views.attendance_delete(request, 1)

        self.assertEqual(response.status_code, 302)
        attendance_get_mock.assert_called_once_with(id=1)

    @patch("horilla.decorators.handle_no_permission", return_value=HttpResponse(status=403))
    @patch("employee.models.EmployeeWorkInformation.objects.filter")
    @patch("attendance.views.views.Attendance.objects.get")
    def test_non_manager_without_permission_cannot_access_attendance_delete(
        self,
        attendance_get_mock,
        reporting_manager_filter_mock,
        _no_permission_mock,
    ):
        request = self._build_request("/attendance/attendance-delete/1/")
        reporting_manager_filter_mock.return_value.exists.return_value = False

        response = attendance_views.attendance_delete(request, 1)

        self.assertEqual(response.status_code, 403)
        attendance_get_mock.assert_not_called()

    @patch("employee.models.EmployeeWorkInformation.objects.filter")
    @patch("attendance.views.views.AttendanceOverTime.objects.filter")
    @patch("attendance.views.views.Attendance.objects.filter")
    def test_reporting_manager_can_access_bulk_delete(
        self,
        attendance_filter_mock,
        overtime_filter_mock,
        reporting_manager_filter_mock,
    ):
        request = self._build_request("/attendance/attendance-bulk-delete")
        request.POST = request.POST.copy()
        request.POST.setlist("ids", ["1", "2"])
        reporting_manager_filter_mock.return_value.exists.return_value = True

        attendances_qs = MagicMock()
        attendances_qs.values_list.return_value = []
        attendances_qs.__iter__.return_value = iter([])
        attendance_filter_mock.return_value = attendances_qs

        overtime_qs = MagicMock()
        overtime_qs.in_bulk.return_value = {}
        overtime_filter_mock.return_value = overtime_qs

        response = attendance_views.attendance_bulk_delete(request)

        self.assertEqual(response.status_code, 302)
        attendance_filter_mock.assert_called_once()

    @patch("employee.models.EmployeeWorkInformation.objects.filter")
    @patch("attendance.views.views.messages.success")
    @patch("attendance.views.views.HorillaRedirect", return_value=HttpResponse(status=302))
    @patch("attendance.views.views.Attendance.objects.get")
    def test_delete_still_executes_when_overtime_row_missing(
        self,
        attendance_get_mock,
        _redirect_mock,
        _messages_success_mock,
        reporting_manager_filter_mock,
    ):
        request = self._build_request("/attendance/attendance-delete/1/")
        reporting_manager_filter_mock.return_value.exists.return_value = True

        attendance = MagicMock()
        attendance.attendance_date = date(2026, 4, 10)
        attendance.attendance_overtime_approve = False
        attendance.employee_id.employee_overtime.filter.return_value.last.return_value = None
        attendance_get_mock.return_value = attendance

        response = attendance_views.attendance_delete(request, 1)

        self.assertEqual(response.status_code, 302)
        attendance.delete.assert_called_once()


class AttendanceLocationMigrationTests(SimpleTestCase):
    @staticmethod
    def _run_migration(apps):
        migration_module = importlib.import_module(
            "attendance.migrations.0005_attendanceactivity_separate_clock_locations"
        )
        migration_module.backfill_separate_clock_locations(apps, None)

    def test_backfill_closed_activity_sets_clock_out_location_only(self):
        activity = SimpleNamespace(
            clock_out=datetime(2026, 4, 10, 17, 0).time(),
            latitude=14.601,
            longitude=121.001,
            gps_address="Closed Activity Address",
            clock_in_latitude=None,
            clock_in_longitude=None,
            clock_in_gps_address=None,
            clock_out_latitude=None,
            clock_out_longitude=None,
            clock_out_gps_address=None,
            save=MagicMock(),
        )

        activities_qs = MagicMock()
        activities_qs.iterator.return_value = iter([activity])
        model = SimpleNamespace(objects=MagicMock())
        model.objects.filter.return_value = activities_qs
        apps = MagicMock()
        apps.get_model.return_value = model

        self._run_migration(apps)

        self.assertIsNone(activity.clock_in_latitude)
        self.assertIsNone(activity.clock_in_longitude)
        self.assertIsNone(activity.clock_in_gps_address)
        self.assertEqual(activity.clock_out_latitude, 14.601)
        self.assertEqual(activity.clock_out_longitude, 121.001)
        self.assertEqual(activity.clock_out_gps_address, "Closed Activity Address")
        activity.save.assert_called_once()

    def test_backfill_open_activity_sets_clock_in_location_only(self):
        activity = SimpleNamespace(
            clock_out=None,
            latitude=14.602,
            longitude=121.002,
            gps_address="Open Activity Address",
            clock_in_latitude=None,
            clock_in_longitude=None,
            clock_in_gps_address=None,
            clock_out_latitude=None,
            clock_out_longitude=None,
            clock_out_gps_address=None,
            save=MagicMock(),
        )

        activities_qs = MagicMock()
        activities_qs.iterator.return_value = iter([activity])
        model = SimpleNamespace(objects=MagicMock())
        model.objects.filter.return_value = activities_qs
        apps = MagicMock()
        apps.get_model.return_value = model

        self._run_migration(apps)

        self.assertEqual(activity.clock_in_latitude, 14.602)
        self.assertEqual(activity.clock_in_longitude, 121.002)
        self.assertEqual(activity.clock_in_gps_address, "Open Activity Address")
        self.assertIsNone(activity.clock_out_latitude)
        self.assertIsNone(activity.clock_out_longitude)
        self.assertIsNone(activity.clock_out_gps_address)
        activity.save.assert_called_once()


class AttendanceActivityMetaBuilderTests(SimpleTestCase):
    @patch("attendance.views.views.AttendanceActivity")
    def test_build_meta_returns_separate_in_and_out_location_data(
        self,
        attendance_activity_model,
    ):
        attendance_row = SimpleNamespace(
            id=1,
            employee_id_id=101,
            attendance_date=date(2026, 4, 10),
        )
        paginated_attendances = SimpleNamespace(object_list=[attendance_row])

        activities = [
            SimpleNamespace(
                employee_id_id=101,
                attendance_date=date(2026, 4, 10),
                clock_in_selfie=None,
                clock_out_selfie=None,
                clock_in_gps_address="Clock In Address",
                clock_out_gps_address=None,
                clock_in_latitude=14.501,
                clock_in_longitude=120.901,
                clock_out_latitude=None,
                clock_out_longitude=None,
                gps_address="Legacy In Address",
                latitude=14.501,
                longitude=120.901,
                clock_out=None,
            ),
            SimpleNamespace(
                employee_id_id=101,
                attendance_date=date(2026, 4, 10),
                clock_in_selfie=None,
                clock_out_selfie=None,
                clock_in_gps_address=None,
                clock_out_gps_address="Clock Out Address",
                clock_in_latitude=None,
                clock_in_longitude=None,
                clock_out_latitude=14.601,
                clock_out_longitude=121.001,
                gps_address="Legacy Out Address",
                latitude=14.601,
                longitude=121.001,
                clock_out=datetime(2026, 4, 10, 17, 0).time(),
            ),
        ]

        attendance_activity_model.objects.filter.return_value.order_by.return_value = (
            activities
        )

        result = build_my_attendance_activity_meta(paginated_attendances)
        meta = result[1]

        self.assertEqual(meta["check_in_location"], "Clock In Address")
        self.assertEqual(meta["check_out_location"], "Clock Out Address")
        self.assertEqual(
            meta["check_in_maps_url"],
            "https://www.google.com/maps?q=14.501,120.901",
        )
        self.assertEqual(
            meta["check_out_maps_url"],
            "https://www.google.com/maps?q=14.601,121.001",
        )
        self.assertEqual(meta["location"], "Clock Out Address")
        self.assertEqual(
            meta["maps_url"],
            "https://www.google.com/maps?q=14.601,121.001",
        )


class AttendanceExportFormFieldTests(SimpleTestCase):
    def test_attendance_export_form_includes_separate_location_fields(self):
        form = AttendanceExportForm()
        choices = {value for value, _ in form.fields["selected_fields"].choices}

        self.assertIn("check_in_location", choices)
        self.assertIn("check_in_maps", choices)
        self.assertIn("check_out_location", choices)
        self.assertIn("check_out_maps", choices)
        self.assertIn("location", choices)
        self.assertIn("maps", choices)

    def test_activity_export_form_includes_separate_location_fields(self):
        form = AttendanceActivityExportForm()
        choices = {value for value, _ in form.fields["selected_fields"].choices}

        self.assertIn("clock_in_gps_address", choices)
        self.assertIn("clock_in_maps_url", choices)
        self.assertIn("clock_out_gps_address", choices)
        self.assertIn("clock_out_maps_url", choices)
        self.assertIn("gps_address", choices)
        self.assertIn("maps_url", choices)
