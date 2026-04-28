import json
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from base.forms import CompanyForm
from base.models import Company
from employee.forms import EmployeeForm
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
