"""Durability, long jobs, true process boundaries, concurrency and owner-only HTTP downloads."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from content_factory.api.dependencies import get_current_user
from content_factory.api.main import app
from content_factory.api.routers import readme_translate as route
from content_factory.api.utils import result_cache as cache
from content_factory.api.utils.translation_job_store import (
    TranslationJobStore,
    TranslationJobStoreError,
    translation_job_store,
)

ROOT = Path(__file__).resolve().parents[3]


def _child(code: str) -> str:
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    return subprocess.check_output([sys.executable, "-c", code], env=environment, text=True, timeout=30).strip()


def test_old_active_job_does_not_expire_and_keeps_progress() -> None:
    store = translation_job_store()
    store.update("long", {"user_id": "owner", "status": "in_progress", "job_type": "archive",
                          "created_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=5)})
    cache.set_translation_phase("long", "translate", 0.4)
    cache._translation_jobs.clear()
    job = cache.get_translation_job("long")
    assert job is not None and job["status"] == "in_progress" and job["progress"] == 0.4
    assert cache.get_translation_job_owner("long") == "owner"
    assert "executor_pid" not in job and "user_id" not in job


def test_completed_job_survives_a_new_python_process() -> None:
    _child("from content_factory.api.utils.result_cache import set_translation_job; "
           "set_translation_job('done', 'completed', user_id='owner', target_language='tg', "
           "source_format='yml', result_links={'yml':'check-list_tg.yml'}, translated_markdown='result')")
    cache._translation_jobs.clear()
    job = cache.get_translation_job("done")
    assert job is not None and job["status"] == "completed"
    assert job["result_links"] == {"yml": "check-list_tg.yml"}
    assert job["translated_markdown"] == "result" and cache.get_translation_job_owner("done") == "owner"


def test_exited_executor_becomes_interrupted_without_losing_context() -> None:
    _child("from content_factory.api.utils.result_cache import set_translation_job; "
           "set_translation_job('stopped','in_progress',user_id='owner',job_type='archive', "
           "source_filename='course.zip',progress=0.6)")
    job = cache.get_translation_job("stopped")
    assert job is not None and job["status"] == "failed"
    assert job["error_code"] == "TRANSLATION_INTERRUPTED" and job["phase"] == "interrupted"
    assert job["source_filename"] == "course.zip" and job["progress"] == 0.6
    assert cache.get_translation_job("stopped") == job
    assert cache.get_translation_job_owner("stopped") == "owner"


def test_polling_from_another_live_worker_does_not_interrupt_the_executor() -> None:
    cache.set_translation_job("live", "in_progress", user_id="owner")
    status = _child("from content_factory.api.utils.result_cache import get_translation_job; "
                    "print(get_translation_job('live')['status'])")
    assert status == "in_progress"


def test_progress_and_metadata_updates_do_not_overwrite_each_other() -> None:
    cache.set_translation_job("parallel", "in_progress", user_id="owner", source_filename="course.zip")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda number: cache.set_translation_phase("parallel", f"batch-{number}", number / 20), range(20)))
    cache.set_translation_job("parallel", "completed", result_links={"zip": "course_en.zip"})
    cache.set_translation_phase("parallel", "late-progress", 0.1)
    job = cache.get_translation_job("parallel")
    assert job is not None and job["status"] == "completed"
    assert job["phase"] != "late-progress" and job["result_links"] == {"zip": "course_en.zip"}
    assert job["source_filename"] == "course.zip" and cache.get_translation_job_owner("parallel") == "owner"


def test_owner_cannot_be_replaced() -> None:
    cache.set_translation_job("owned", "in_progress", user_id="owner")
    with pytest.raises(HTTPException) as error:
        cache.set_translation_job("owned", "completed", user_id="intruder")
    assert error.value.status_code == 503
    assert cache.get_translation_job_owner("owned") == "owner"
    job = cache.get_translation_job("owned")
    assert job is not None and job["status"] == "in_progress"


def test_memory_eviction_does_not_delete_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, "_translation_memory_limit", 2)
    cache._translation_jobs.clear()
    for number in range(4):
        cache.set_translation_job(f"old-{number}", "completed", user_id="owner", translated_markdown=str(number))
    assert len(cache._translation_jobs) == 2 and "old-0" not in cache._translation_jobs
    job = cache.get_translation_job("old-0")
    assert job is not None and job["translated_markdown"] == "0"


def test_failed_job_survives_cache_loss() -> None:
    cache.set_translation_job("failed", "failed", user_id="owner", error="Gateway unavailable", error_code="GATEWAY_ERROR")
    cache._translation_jobs.clear()
    job = cache.get_translation_job("failed")
    assert job is not None and job["error"] == "Gateway unavailable" and job["error_code"] == "GATEWAY_ERROR"


def test_unavailable_storage_never_returns_a_false_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    cache.set_translation_job("atomic", "in_progress", user_id="owner")
    def broken_write(connection: sqlite3.Connection, record: object) -> None:
        raise sqlite3.OperationalError("disk full")
    with monkeypatch.context() as patch:
        patch.setattr(TranslationJobStore, "_write", staticmethod(broken_write))
        with pytest.raises(HTTPException) as error:
            cache.set_translation_job("atomic", "completed", result_links={"zip": "result.zip"})
    assert error.value.status_code == 503
    job = cache.get_translation_job("atomic")
    assert job is not None and job["status"] == "in_progress"
    assert cache._translation_jobs["atomic"]["status"] == "in_progress"


def test_corrupt_snapshot_is_unavailable_not_missing() -> None:
    cache.set_translation_job("corrupt", "completed", user_id="owner")
    with sqlite3.connect(str(translation_job_store().path)) as connection:
        connection.execute("UPDATE translation_jobs SET payload=? WHERE request_id=?", ('{"version":999}', "corrupt"))
    with pytest.raises(HTTPException) as error:
        cache.get_translation_job("corrupt")
    assert error.value.status_code == 503


def test_artifact_paths_cannot_escape_the_request_directory() -> None:
    with pytest.raises(TranslationJobStoreError):
        translation_job_store().update("escape", {"status": "completed", "user_id": "owner", "result_links": {"zip": "../other.zip"}})


@pytest.mark.parametrize("kind", ["zip", "yml", "srt", "video"])
def test_http_status_and_download_recover_after_cache_loss(
    kind: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    request_id = "http-" + kind
    filename = "result." + ("mp4" if kind == "video" else kind)
    directory = tmp_path / "artifacts" / "translations" / request_id
    directory.mkdir(parents=True)
    content = ("durable result " + kind).encode()
    (directory / filename).write_bytes(content)
    monkeypatch.setattr(route, "STORAGE_DIR", str(tmp_path / "artifacts"))
    translation_job_store().update(request_id, {"status": "completed", "user_id": "owner",
        "created_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10),
        "result_links": {kind: filename}, "job_type": "archive" if kind == "zip" else "document"})
    cache._translation_jobs.clear()
    old_overrides = app.dependency_overrides.copy()
    try:
        app.dependency_overrides[get_current_user] = lambda: {"id": "owner"}
        client = TestClient(app)
        reply = client.get(f"/api/v1/translate/status/{request_id}")
        assert reply.status_code == 200 and reply.json()["status"] == "completed"
        assert "executor_pid" not in reply.json() and "user_id" not in reply.json()
        reply = client.get(f"/api/v1/translate/download/{request_id}", params={"type": kind})
        assert reply.status_code == 200 and reply.content == content
        app.dependency_overrides[get_current_user] = lambda: {"id": "other"}
        assert client.get(f"/api/v1/translate/status/{request_id}").status_code == 403
        assert client.get(f"/api/v1/translate/download/{request_id}", params={"type": kind}).status_code == 403
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old_overrides)


def test_interrupted_status_is_owner_only_and_updates_the_dashboard(monkeypatch: pytest.MonkeyPatch) -> None:
    _child("from content_factory.api.utils.result_cache import set_translation_job; "
           "set_translation_job('interrupted-http','in_progress',user_id='owner',job_type='archive')")
    updates: list[dict[str, object]] = []
    monkeypatch.setattr(route, "upsert_user_run", lambda **values: updates.append(values))
    job = route._translation_job_for_user("interrupted-http", {"id": "owner"})
    assert job["error_code"] == "TRANSLATION_INTERRUPTED" and updates[0]["status"] == "failed"
    with pytest.raises(HTTPException) as error:
        route._translation_job_for_user("interrupted-http", {"id": "other"})
    assert error.value.status_code == 403 and len(updates) == 1


def test_unknown_progress_does_not_create_a_phantom_job() -> None:
    cache.set_translation_phase("unknown", "translate")
    assert cache.get_translation_job("unknown") is None


def test_terminal_job_cannot_be_silently_restarted() -> None:
    cache.set_translation_job("terminal", "completed", user_id="owner")
    with pytest.raises(HTTPException):
        cache.set_translation_job("terminal", "in_progress", user_id="owner")
    job = cache.get_translation_job("terminal")
    assert job is not None and job["status"] == "completed"
