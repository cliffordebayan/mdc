import logging
import os
import sys
import tempfile
from threading import Lock

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.base import ConflictingIdError
from django.db import close_old_connections
from django.utils import timezone

from horilla import settings

from .gdrive import upload_file
from .models import GoogleDriveBackup
from .pgdump import dump_postgres_db
from .zip import zip_folder

scheduler = BackgroundScheduler()
logger = logging.getLogger(__name__)

MANUAL_BACKUP_JOB_ID = "gdrive_backup_now"
backup_execution_lock = Lock()


def google_drive_backup(force=False):
    """Run one backup without allowing another backup to overlap it."""
    if not backup_execution_lock.acquire(blocking=False):
        message = "Google Drive backup skipped: another backup is already running."
        logger.warning(message)
        return {"success": False, "message": message}

    try:
        return _run_google_drive_backup(force=force)
    finally:
        backup_execution_lock.release()


def _run_google_drive_backup(force=False):
    """Run a Google Drive backup and return a result for UI and scheduled callers.

    Scheduled runs honor the active flag. A manually requested backup passes
    ``force=True`` so it can run even when scheduled automation is stopped.
    """
    google_drive = GoogleDriveBackup.objects.first()
    if google_drive is None:
        return {"success": False, "message": "Google Drive backup is not configured."}

    if not force and not google_drive.active:
        print("Google Drive backup skipped: backup is not active.")
        return {"success": False, "message": "Google Drive backup automation is stopped."}

    if not google_drive.access_token:
        message = "Google Drive backup cannot run because authorization is required."
        print(message)
        return {"success": False, "message": message}

    if not google_drive.backup_db and not google_drive.backup_media:
        return {
            "success": False,
            "message": "Select at least one backup type before running a backup.",
        }

    results = []
    errors = []
    folder_id = google_drive.gdrive_folder_id
    backup_timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")

    if google_drive.backup_db:
        db_backup_path = None
        try:
            db = settings.DATABASES["default"]
            with tempfile.NamedTemporaryFile(suffix=".dump", delete=False) as temp_file:
                db_backup_path = temp_file.name
            dump_postgres_db(
                db_name=db["NAME"],
                username=db["USER"],
                output_file=db_backup_path,
                password=db["PASSWORD"],
                host=db.get("HOST") or "localhost",
                port=db.get("PORT") or 5432,
            )
            if not os.path.exists(db_backup_path) or os.path.getsize(db_backup_path) == 0:
                raise FileNotFoundError("Database backup dump was not created.")
            upload_file(
                db_backup_path,
                google_drive,
                folder_id,
                file_name=f"database_backup_{backup_timestamp}.dump",
            )
            results.append("database")
        except Exception as error:
            errors.append(f"database: {error}")
            print(f"Database backup failed: {error}")
        finally:
            if db_backup_path and os.path.exists(db_backup_path):
                try:
                    os.remove(db_backup_path)
                except OSError:
                    pass

    if google_drive.backup_media:
        media_backup_path = None
        try:
            folder_to_zip = settings.MEDIA_ROOT
            if not os.path.exists(folder_to_zip):
                raise FileNotFoundError(f"Media folder does not exist: {folder_to_zip}")
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as temp_file:
                media_backup_path = temp_file.name
            zip_folder(folder_to_zip, media_backup_path)
            if not os.path.exists(media_backup_path):
                raise FileNotFoundError("Media backup archive was not created.")
            upload_file(
                media_backup_path,
                google_drive,
                folder_id,
                file_name=f"media_backup_{backup_timestamp}.zip",
            )
            results.append("media")
        except Exception as error:
            errors.append(f"media: {error}")
            print(f"Media backup failed: {error}")
        finally:
            if media_backup_path and os.path.exists(media_backup_path):
                try:
                    os.remove(media_backup_path)
                except OSError:
                    pass

    if errors:
        message = "Backup completed with errors: " + "; ".join(errors)
        if results:
            message += ". Successful: " + ", ".join(results) + "."
        return {"success": False, "message": message}

    return {"success": True, "message": "Google Drive backup completed successfully."}


def _run_backup_job(force=False):
    """Run a scheduled backup and keep failures inside the scheduler thread."""
    try:
        close_old_connections()
        result = google_drive_backup(force=force)
    except Exception:
        logger.exception("Google Drive backup job failed unexpectedly.")
        return
    finally:
        close_old_connections()

    if result["success"]:
        logger.info(result["message"])
    else:
        logger.error(result["message"])


def queue_gdrive_backup(force=True):
    """Queue a backup for the background scheduler and return immediately."""
    google_drive = GoogleDriveBackup.objects.first()
    if google_drive is None:
        return {"success": False, "message": "Google Drive backup is not configured."}

    if not force and not google_drive.active:
        return {
            "success": False,
            "message": "Google Drive backup automation is stopped.",
        }

    if not google_drive.access_token:
        return {
            "success": False,
            "message": "Google Drive backup cannot run because authorization is required.",
        }

    if not google_drive.backup_db and not google_drive.backup_media:
        return {
            "success": False,
            "message": "Select at least one backup type before running a backup.",
        }

    if backup_execution_lock.locked():
        return {
            "success": False,
            "message": "A Google Drive backup is already running.",
        }

    if not scheduler.running:
        scheduler.start()

    try:
        scheduler.add_job(
            _run_backup_job,
            trigger="date",
            kwargs={"force": force},
            id=MANUAL_BACKUP_JOB_ID,
            max_instances=1,
        )
    except ConflictingIdError:
        return {
            "success": False,
            "message": "A Google Drive backup is already queued.",
        }

    return {
        "success": True,
        "message": "Google Drive backup started in the background.",
    }


def start_gdrive_backup_job():
    """Start the scheduled Google Drive backup job when automation is active."""
    google_drive = GoogleDriveBackup.objects.first()
    if google_drive is None or not google_drive.active:
        stop_gdrive_backup_job()
        return

    try:
        scheduler.remove_job("gdrive_backup_job")
    except Exception:
        pass

    if google_drive.interval:
        scheduler.add_job(
            _run_backup_job,
            "interval",
            seconds=google_drive.seconds,
            id="gdrive_backup_job",
            max_instances=1,
            coalesce=True,
            kwargs={"force": False},
        )
    else:
        scheduler.add_job(
            _run_backup_job,
            trigger="cron",
            hour=google_drive.hour,
            minute=google_drive.minute,
            id="gdrive_backup_job",
            max_instances=1,
            coalesce=True,
            kwargs={"force": False},
        )

    if not scheduler.running:
        scheduler.start()


def stop_gdrive_backup_job():
    """Stop the scheduled Google Drive backup job if it exists."""
    try:
        scheduler.remove_job("gdrive_backup_job")
    except Exception:
        pass


if not any(
    cmd in sys.argv
    for cmd in ["makemigrations", "migrate", "compilemessages", "flush", "shell", "test"]
):
    try:
        start_gdrive_backup_job()
    except Exception:
        pass
