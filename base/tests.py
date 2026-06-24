import json
import shutil
import tempfile
from datetime import time
from unittest.mock import patch

from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.mail import EmailMessage
from django.test import RequestFactory
from django.test import TestCase, override_settings
from django.urls import reverse

from base.backends import ConfiguredEmailBackend
from base.forms import CompanyForm
from base.models import (
    Company,
    DynamicEmailConfiguration,
    EmployeeShift,
    EmployeeShiftDay,
    EmployeeShiftSchedule,
)
from employee.forms import EmployeeForm
from employee.models import Employee
from horilla.horilla_middlewares import _thread_locals
from recruitment.forms import CandidateCreationForm


class PwaManifestViewTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._temp_media = tempfile.mkdtemp(prefix="pwa-manifest-test-")
        cls._override = override_settings(MEDIA_ROOT=cls._temp_media)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        shutil.rmtree(cls._temp_media, ignore_errors=True)
        super().tearDownClass()

    def test_manifest_returns_hq_company_name_and_icon(self):
        icon = SimpleUploadedFile("logo.png", b"logo-bytes", content_type="image/png")
        company = Company.objects.create(
            company="Acme Corp",
            hq=True,
            address="HQ Address",
            country="PH",
            state="NCR",
            city="Manila",
            zip="1000",
            icon=icon,
        )

        response = self.client.get(reverse("pwa-manifest"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response["Content-Type"].startswith("application/manifest+json")
        )
        payload = json.loads(response.content)
        self.assertEqual(payload["name"], "Acme Corp")
        self.assertEqual(payload["short_name"], "Acme Corp")
        self.assertEqual(payload["icons"][0]["src"], company.icon.url)

    def test_manifest_falls_back_to_horilla_defaults_without_hq(self):
        response = self.client.get(reverse("pwa-manifest"))

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["name"], "Horilla")
        self.assertEqual(payload["short_name"], "Horilla")
        self.assertEqual(
            payload["icons"][0]["src"], "/static/favicons/apple-touch-icon.png"
        )


class PhilippinesPlaceholderFormTests(TestCase):
    def test_company_form_uses_province_and_ph_address_placeholders(self):
        form = CompanyForm()

        self.assertEqual(form.fields["state"].label, "Province")
        self.assertEqual(form.fields["zip"].label, "Zip Code")
        self.assertEqual(
            form.fields["address"].widget.attrs.get("placeholder"),
            "e.g. Brgy. 1, Laoag City, Ilocos Norte",
        )
        self.assertEqual(
            form.fields["city"].widget.attrs.get("placeholder"), "e.g. Laoag City"
        )
        self.assertEqual(form.fields["zip"].widget.attrs.get("placeholder"), "e.g. 2900")

    def test_employee_form_uses_ph_contact_placeholders(self):
        form = EmployeeForm()

        self.assertEqual(form.fields["email"].widget.attrs.get("placeholder"), "example@mail.com")
        self.assertEqual(
            form.fields["phone"].widget.attrs.get("placeholder"), "+63 917 123 4567"
        )
        self.assertEqual(
            form.fields["emergency_contact"].widget.attrs.get("placeholder"),
            "+63 917 123 4567",
        )
        self.assertEqual(form.fields["state"].label, "Province")
        self.assertEqual(
            form.fields["city"].widget.attrs.get("placeholder"), "e.g. Laoag City"
        )
        self.assertEqual(form.fields["zip"].widget.attrs.get("placeholder"), "e.g. 2900")

    def test_candidate_creation_form_uses_ph_placeholders(self):
        form = CandidateCreationForm()

        self.assertEqual(
            form.fields["email"].widget.attrs.get("placeholder"), "example@mail.com"
        )
        self.assertEqual(
            form.fields["mobile"].widget.attrs.get("placeholder"), "+63 917 123 4567"
        )
        self.assertEqual(form.fields["state"].label, "Province")
        self.assertEqual(form.fields["zip"].widget.attrs.get("placeholder"), "e.g. 2900")


class DynamicEmailBackendSenderTests(TestCase):
    def setUp(self):
        cache.clear()
        _thread_locals.request = None
        self.factory = RequestFactory()
        self.config = DynamicEmailConfiguration.objects.create(
            host="smtp.example.com",
            port=587,
            from_email="smtp@example.com",
            username="smtp@example.com",
            display_name="MDC",
            password="secret",
            use_tls=True,
            use_ssl=False,
            use_dynamic_display_name=True,
        )

    def tearDown(self):
        cache.clear()
        _thread_locals.request = None
        super().tearDown()

    def _set_request_user(self, user):
        request = self.factory.get("/")
        request.user = user
        _thread_locals.request = request

    def test_employee_display_name_keeps_configured_smtp_sender(self):
        user = User.objects.create_user(
            username="manager",
            email="manager@example.com",
            password="test",
        )
        Employee.objects.create(
            employee_user_id=user,
            employee_first_name="Manager",
            employee_last_name="User",
            email="manager@example.com",
            phone="09170000000",
            gender="male",
            is_active=True,
        )
        self._set_request_user(user)

        message = EmailMessage("Subject", "Body", to=["to@example.com"])

        self.assertEqual(message.from_email, "Manager User <smtp@example.com>")
        self.assertEqual(message.reply_to, ["Manager User <manager@example.com>"])

    def test_user_without_employee_profile_uses_configured_sender(self):
        user = User.objects.create_user(
            username="staff",
            email="staff@example.com",
            password="test",
        )
        self._set_request_user(user)

        message = EmailMessage("Subject", "Body", to=["to@example.com"])

        self.assertEqual(message.from_email, "MDC <smtp@example.com>")
        self.assertEqual(message.reply_to, [])
        self.assertEqual(
            ConfiguredEmailBackend().dynamic_from_email_with_display_name,
            "MDC <smtp@example.com>",
        )


@override_settings(
    MIDDLEWARE=[
        middleware
        for middleware in django_settings.MIDDLEWARE
        if middleware != "base.middleware.ForcePasswordChangeMiddleware"
    ]
)
class EmployeeShiftScheduleSettingsTests(TestCase):
    def setUp(self):
        _thread_locals.request = None
        self.user = User.objects.create_superuser(
            username="schedule-admin",
            email="",
            password="test",
        )
        self.employee = Employee.objects.create(
            employee_user_id=self.user,
            employee_first_name="Schedule",
            employee_last_name="Admin",
            email="schedule-admin@example.com",
            phone="09170000000",
            gender="male",
            is_active=True,
        )
        self.client.force_login(self.user)

        self.shift_day = EmployeeShiftDay.objects.create(day="monday")
        self.shift = EmployeeShift.objects.create(
            employee_shift="Regular",
            weekly_full_time="40:00",
            full_time="200:00",
        )
        self.schedule = EmployeeShiftSchedule.objects.create(
            day=self.shift_day,
            shift_id=self.shift,
            minimum_working_hour="08:00",
            start_time=time(9, 0),
            end_time=time(17, 0),
        )

    def tearDown(self):
        _thread_locals.request = None
        super().tearDown()

    def _schedule_payload(self, **overrides):
        payload = {
            "day": str(self.shift_day.id),
            "shift_id": str(self.shift.id),
            "minimum_working_hour": "07:30",
            "start_time": "08:00",
            "end_time": "16:30",
            "auto_punch_out_time": "",
        }
        payload.update(overrides)
        return payload

    def test_update_persists_time_fields_and_refreshes_schedule_view(self):
        response = self.client.post(
            reverse("employee-shift-schedule-update", args=[self.schedule.id]),
            self._schedule_payload(),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers["HX-Refresh"], "true")
        self.assertNotIn("HX-Redirect", response.headers)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.start_time, time(8, 0))
        self.assertEqual(self.schedule.end_time, time(16, 30))
        self.assertEqual(self.schedule.minimum_working_hour, "07:30")

        refreshed_response = self.client.get(reverse("employee-shift-schedule-view"))
        self.assertContains(refreshed_response, "08:00 - 16:30")
        self.assertContains(refreshed_response, "07:30")

    def test_invalid_auto_checkout_keeps_modal_open_with_errors(self):
        response = self.client.post(
            reverse("employee-shift-schedule-update", args=[self.schedule.id]),
            self._schedule_payload(is_auto_punch_out_enabled="on"),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("HX-Redirect", response.headers)
        self.assertNotIn("HX-Refresh", response.headers)
        self.assertContains(
            response, "Please correct the errors below and save again."
        )
        self.assertContains(
            response,
            "Automatic punch out time is required when automatic punch out is enabled.",
        )
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.start_time, time(9, 0))
        self.assertEqual(self.schedule.minimum_working_hour, "08:00")

    def test_disabling_auto_checkout_does_not_block_time_updates(self):
        self.schedule.is_auto_punch_out_enabled = True
        self.schedule.auto_punch_out_time = time(17, 30)
        self.schedule.save()

        response = self.client.post(
            reverse("employee-shift-schedule-update", args=[self.schedule.id]),
            self._schedule_payload(auto_punch_out_time=""),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers["HX-Refresh"], "true")
        self.assertNotIn("HX-Redirect", response.headers)
        self.schedule.refresh_from_db()
        self.assertFalse(self.schedule.is_auto_punch_out_enabled)
        self.assertIsNone(self.schedule.auto_punch_out_time)
        self.assertEqual(self.schedule.start_time, time(8, 0))
        self.assertEqual(self.schedule.minimum_working_hour, "07:30")

    @patch("attendance.methods.utils.recalculate_attendance_for_shift")
    def test_update_still_persists_when_attendance_recalculation_fails(
        self, recalculate_mock
    ):
        recalculate_mock.side_effect = RuntimeError("existing attendance issue")

        response = self.client.post(
            reverse("employee-shift-schedule-update", args=[self.schedule.id]),
            self._schedule_payload(),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers["HX-Refresh"], "true")
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.start_time, time(8, 0))
        self.assertEqual(self.schedule.end_time, time(16, 30))
        self.assertEqual(self.schedule.minimum_working_hour, "07:30")

    def test_schedule_view_shows_start_end_and_minimum_working_time(self):
        response = self.client.get(reverse("employee-shift-schedule-view"))

        self.assertContains(response, "09:00 - 17:00")
        self.assertContains(response, "Minimum")
        self.assertContains(response, "08:00")
