import os
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from .models import GoogleDriveBackup
from .scheduler import google_drive_backup


class GoogleDriveBackupRunnerTests(TestCase):
    def create_backup(self, **overrides):
        values = {
            "gdrive_folder_id": "folder-id",
            "access_token": "access-token",
            "backup_db": True,
            "backup_media": False,
            "interval": True,
            "seconds": 3600,
            "active": False,
        }
        values.update(overrides)
        return GoogleDriveBackup.objects.create(**values)

    @patch("horilla_backup.scheduler.upload_file")
    @patch("horilla_backup.scheduler.dump_postgres_db")
    def test_manual_backup_runs_when_automation_is_stopped(self, dump_db, upload):
        backup = self.create_backup()
        dump_db.side_effect = lambda **kwargs: Path(kwargs["output_file"]).write_bytes(
            b"database-backup"
        )

        result = google_drive_backup(force=True)

        self.assertTrue(result["success"])
        backup.refresh_from_db()
        self.assertFalse(backup.active)
        dump_db.assert_called_once()
        upload.assert_called_once()
        uploaded_path = upload.call_args.args[0]
        self.assertFalse(os.path.exists(uploaded_path))

    @patch("horilla_backup.scheduler.upload_file")
    @patch("horilla_backup.scheduler.dump_postgres_db")
    def test_scheduled_backup_skips_when_automation_is_stopped(self, dump_db, upload):
        self.create_backup()

        result = google_drive_backup()

        self.assertFalse(result["success"])
        self.assertIn("stopped", result["message"])
        dump_db.assert_not_called()
        upload.assert_not_called()

    @patch("horilla_backup.scheduler.upload_file")
    @patch("horilla_backup.scheduler.dump_postgres_db")
    def test_backup_requires_google_authorization(self, dump_db, upload):
        self.create_backup(access_token=None)

        result = google_drive_backup(force=True)

        self.assertFalse(result["success"])
        self.assertIn("authorization", result["message"])
        dump_db.assert_not_called()
        upload.assert_not_called()

    def test_backup_requires_a_selected_backup_type(self):
        self.create_backup(backup_db=False, backup_media=False)

        result = google_drive_backup(force=True)

        self.assertFalse(result["success"])
        self.assertIn("at least one", result["message"])


class GdriveSetupTemplateTests(SimpleTestCase):
    def test_template_exposes_post_backup_now_action(self):
        template_path = (
            Path(__file__).parent / "templates" / "backup" / "gdrive_setup_form.html"
        )
        template = template_path.read_text(encoding="utf-8")

        self.assertIn('method="post"', template)
        self.assertIn('name="backup_now"', template)
        self.assertIn("{% url 'gdrive' %}", template)
        self.assertIn("Backup Now", template)
