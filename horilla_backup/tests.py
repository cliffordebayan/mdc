import os
import subprocess
import threading
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from django.conf import settings
from django.test import SimpleTestCase, TestCase

from .gdrive import upload_file
from .models import GoogleDriveBackup
from .pgdump import dump_postgres_db
from .scheduler import google_drive_backup, queue_gdrive_backup


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
        self.assertTrue(
            upload.call_args.kwargs["file_name"].startswith("database_backup_")
        )
        self.assertEqual(
            dump_db.call_args.kwargs["host"],
            settings.DATABASES["default"].get("HOST") or "localhost",
        )
        self.assertEqual(
            dump_db.call_args.kwargs["port"],
            settings.DATABASES["default"].get("PORT") or 5432,
        )

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

    @patch("horilla_backup.scheduler.scheduler")
    @patch("horilla_backup.scheduler.upload_file")
    @patch("horilla_backup.scheduler.dump_postgres_db")
    def test_manual_backup_is_queued_without_running_in_request(
        self, dump_db, upload, scheduler_mock
    ):
        self.create_backup()
        scheduler_mock.running = False

        result = queue_gdrive_backup(force=True)

        self.assertTrue(result["success"])
        self.assertIn("background", result["message"])
        scheduler_mock.start.assert_called_once_with()
        scheduler_mock.add_job.assert_called_once()
        self.assertEqual(
            scheduler_mock.add_job.call_args.args[0].__name__, "_run_backup_job"
        )
        self.assertEqual(
            scheduler_mock.add_job.call_args.kwargs["id"], "gdrive_backup_now"
        )
        self.assertEqual(
            scheduler_mock.add_job.call_args.kwargs["kwargs"], {"force": True}
        )
        dump_db.assert_not_called()
        upload.assert_not_called()

    @patch("horilla_backup.scheduler._run_google_drive_backup")
    def test_backup_does_not_overlap(self, run_backup):
        started = threading.Event()
        release = threading.Event()

        def slow_backup(**kwargs):
            started.set()
            release.wait(timeout=5)
            return {"success": True, "message": "done"}

        run_backup.side_effect = slow_backup
        first_result = []
        worker = threading.Thread(
            target=lambda: first_result.append(google_drive_backup(force=True))
        )
        worker.start()
        self.assertTrue(started.wait(timeout=5))

        second_result = google_drive_backup(force=True)
        release.set()
        worker.join(timeout=5)

        self.assertFalse(second_result["success"])
        self.assertIn("already running", second_result["message"])
        self.assertEqual(first_result, [{"success": True, "message": "done"}])

    @patch("horilla_backup.scheduler.close_old_connections")
    @patch("horilla_backup.scheduler.google_drive_backup")
    def test_background_job_logs_unexpected_failure(
        self, backup, close_old_connections_mock
    ):
        backup.side_effect = RuntimeError("network unavailable")

        from .scheduler import _run_backup_job

        with self.assertLogs("horilla_backup.scheduler", level="ERROR") as logs:
            _run_backup_job(force=True)

        self.assertIn("failed unexpectedly", logs.output[0])


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


class PostgresDumpTests(SimpleTestCase):
    @patch("horilla_backup.pgdump.subprocess.run")
    def test_dump_uses_configured_connection_and_isolates_password(self, run):
        original_password = os.environ.get("PGPASSWORD")
        with patch.dict(os.environ, {"PGPASSWORD": "old-password"}, clear=True):
            dump_postgres_db(
                db_name="horilla",
                username="postgres",
                output_file="backup.dump",
                password="new-password",
                host="db",
                port="5432",
            )

            command = run.call_args.args[0]
            self.assertEqual(command[0:6], ["pg_dump", "-h", "db", "-p", "5432", "-U"])
            self.assertEqual(run.call_args.kwargs["env"]["PGPASSWORD"], "new-password")
            self.assertEqual(os.environ.get("PGPASSWORD"), "old-password")

        self.assertEqual(os.environ.get("PGPASSWORD"), original_password)

    @patch("horilla_backup.pgdump.subprocess.run")
    def test_dump_raises_pg_dump_error(self, run):
        run.side_effect = subprocess.CalledProcessError(
            1, ["pg_dump"], stderr="could not connect to server"
        )

        with self.assertRaisesRegex(RuntimeError, "could not connect to server"):
            dump_postgres_db("horilla", "postgres", "backup.dump")

    @patch("horilla_backup.pgdump.subprocess.run")
    def test_dump_reports_missing_pg_dump(self, run):
        run.side_effect = FileNotFoundError("pg_dump")

        with self.assertRaisesRegex(RuntimeError, "pg_dump is not installed"):
            dump_postgres_db("horilla", "postgres", "backup.dump")


class GoogleDriveUploadTests(SimpleTestCase):
    @patch("horilla_backup.gdrive.MediaFileUpload")
    @patch("horilla_backup.gdrive.build")
    @patch("horilla_backup.gdrive.get_credentials_from_model")
    def test_upload_uses_explicit_file_name(self, get_credentials, build, media_upload):
        credentials = Mock(valid=True)
        get_credentials.return_value = credentials
        execute = build.return_value.files.return_value.create.return_value.execute
        execute.return_value = {"id": "drive-file-id"}

        with tempfile.NamedTemporaryFile() as backup_file:
            result = upload_file(
                backup_file.name,
                Mock(),
                "folder-id",
                file_name="database_backup_20261016_120000.dump",
            )

        self.assertEqual(result, "drive-file-id")
        create_kwargs = build.return_value.files.return_value.create.call_args.kwargs
        self.assertEqual(
            create_kwargs["body"]["name"], "database_backup_20261016_120000.dump"
        )
