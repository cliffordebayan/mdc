import json
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from base.models import Company


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
