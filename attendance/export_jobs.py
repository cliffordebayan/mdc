import json
import threading
import traceback
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone


EXPORT_JOB_TTL_HOURS = 24


def export_jobs_dir():
    path = Path(settings.MEDIA_ROOT) / "export_jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cleanup_export_jobs():
    cutoff = timezone.now() - timedelta(hours=EXPORT_JOB_TTL_HOURS)
    for path in export_jobs_dir().glob("*"):
        try:
            if timezone.datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.get_current_timezone()
            ) < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            continue


def create_export_job(user_id, filename):
    cleanup_export_jobs()
    job_id = uuid.uuid4().hex
    payload = {
        "id": job_id,
        "user_id": user_id,
        "status": "queued",
        "percent": 0,
        "message": "Queued",
        "filename": filename,
        "error": "",
        "created_at": timezone.now().isoformat(),
        "updated_at": timezone.now().isoformat(),
    }
    write_job_status(job_id, payload)
    return job_id


def status_path(job_id):
    return export_jobs_dir() / f"{job_id}.json"


def file_path(job_id):
    return export_jobs_dir() / f"{job_id}.xlsx"


def read_job_status(job_id):
    path = status_path(job_id)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as status_file:
            return json.load(status_file)
    except (OSError, json.JSONDecodeError):
        return None


def write_job_status(job_id, payload):
    payload["updated_at"] = timezone.now().isoformat()
    path = status_path(job_id)
    tmp_path = path.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as status_file:
        json.dump(payload, status_file)
    tmp_path.replace(path)


def update_job(job_id, **changes):
    payload = read_job_status(job_id) or {"id": job_id}
    payload.update(changes)
    write_job_status(job_id, payload)
    return payload


def job_progress(job_id):
    def progress(percent, message):
        percent = max(0, min(100, int(percent)))
        update_job(job_id, status="running", percent=percent, message=str(message))

    return progress


def start_export_job(job_id, worker):
    def run():
        close_old_connections()
        try:
            update_job(job_id, status="running", percent=1, message="Preparing export")
            worker(file_path(job_id), job_progress(job_id))
            update_job(job_id, status="complete", percent=100, message="Export ready")
        except Exception as exc:  # pragma: no cover - traceback text varies by runtime
            update_job(
                job_id,
                status="failed",
                percent=100,
                message="Export failed",
                error=f"{exc}\n{traceback.format_exc()}",
            )
        finally:
            close_old_connections()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread

