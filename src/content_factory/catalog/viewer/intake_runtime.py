"""Runtime orchestration for intake jobs.

This module owns background execution, worker lease/heartbeat handling, stale
job recovery and per-request schema preflight. The deterministic pipeline body
lives in ``intake_ops`` and is imported only inside the worker entrypoint.
"""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from content_factory.catalog.db import (
    CatalogConnection,
    catalog_database_url,
    open_catalog_connection,
    resolve_backend,
)
from content_factory.catalog.viewer._common import table_exists
from content_factory.catalog.viewer.intake_jobs import get_intake_job, update_intake_job
from content_factory.catalog.viewer.intake_reviews import repair_intake_review_links
from content_factory.catalog.viewer.intake_worker import (
    HEARTBEAT_INTERVAL_SECONDS,
    claim_intake_job,
    heartbeat_intake_job,
    reclaim_expired_intake_jobs,
    release_intake_job,
    worker_identity,
)

INTAKE_SCHEMA_READY: set[str] = set()
INTAKE_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="intake")
INTAKE_STALE_TIMEOUT_SECONDS = 180


def _intake_schema_ready_key(db_path: Path) -> str:
    """Return the backend-specific identity used for one-time intake repairs."""

    backend = resolve_backend()
    if backend == "postgres":
        url = catalog_database_url() or ""
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest() if url else "missing-url"
        return f"postgres:{digest}"
    return f"{backend}:{db_path.resolve()}"


def _ensure_intake_review_schema(conn: CatalogConnection, db_path: Path) -> None:
    """Run one-time-per-database review-link repair."""

    ready_key = _intake_schema_ready_key(db_path)
    if ready_key not in INTAKE_SCHEMA_READY:
        repair_intake_review_links(conn)
        INTAKE_SCHEMA_READY.add(ready_key)


def _dispatch_pending_intake_jobs(conn: CatalogConnection, db_path: Path) -> None:
    """Submit pending jobs to the executor; claim step gates double-runs."""

    if not table_exists(conn, "intake_job"):
        return
    rows = conn.execute(
        "SELECT id FROM intake_job WHERE status = 'pending' ORDER BY created_at LIMIT 20"
    ).fetchall()
    for row in rows:
        INTAKE_EXECUTOR.submit(execute_intake_job, db_path, int(row["id"]))


def ensure_intake_runtime_schema(conn: CatalogConnection, db_path: Path) -> None:
    """Per-request intake preflight: schema readiness + crashed-job recovery."""

    _ensure_intake_review_schema(conn, db_path)
    repair_stale_intake_jobs(conn)


def repair_stale_intake_jobs(conn: CatalogConnection, stale_after_seconds: int = INTAKE_STALE_TIMEOUT_SECONDS) -> int:
    """Recover intake jobs whose worker lease expired."""

    if not table_exists(conn, "intake_job"):
        return 0
    result = reclaim_expired_intake_jobs(conn)
    return result["requeued"] + result["failed"]


def execute_intake_job(db_path: Path, job_id: int) -> None:
    """Claim, run and release one intake job with a DB-backed heartbeat."""

    from content_factory.catalog.viewer.intake_ops import run_intake_pipeline

    owner = worker_identity()
    conn = open_catalog_connection(db_path)
    heartbeat_stop = threading.Event()
    heartbeat_thread: threading.Thread | None = None
    try:
        _ensure_intake_review_schema(conn, db_path)
        if not claim_intake_job(conn, job_id, owner):
            return
        job = get_intake_job(conn, job_id)
        if not job:
            release_intake_job(conn, job_id, owner)
            return
        update_intake_job(conn, job_id, progress_note="Запуск intake-пайплайна.")

        def _run_heartbeat() -> None:
            heartbeat_conn = open_catalog_connection(db_path)
            try:
                while not heartbeat_stop.wait(HEARTBEAT_INTERVAL_SECONDS):
                    heartbeat_intake_job(heartbeat_conn, job_id, owner)
            finally:
                heartbeat_conn.close()

        heartbeat_thread = threading.Thread(
            target=_run_heartbeat, name=f"intake-hb-{job_id}", daemon=True
        )
        heartbeat_thread.start()

        def progress(stage: str, note: str) -> None:
            worker_conn = open_catalog_connection(db_path)
            try:
                _ensure_intake_review_schema(worker_conn, db_path)
                update_intake_job(worker_conn, job_id, current_stage=stage, progress_note=note)
            finally:
                worker_conn.close()

        result = run_intake_pipeline(
            conn,
            db_path,
            str(job["brief_text"]),
            intake_job_id=job_id,
            progress_callback=progress,
        )
        update_intake_job(
            conn,
            job_id,
            status="succeeded",
            current_stage="completed",
            progress_note="Обработка завершена.",
            result_payload=result,
            mark_finished=True,
        )
        release_intake_job(conn, job_id, owner)
    except Exception as exc:
        update_intake_job(
            conn,
            job_id,
            status="failed",
            current_stage="failed",
            progress_note="Пайплайн завершился с ошибкой.",
            error_text=str(exc),
            mark_finished=True,
        )
        try:
            release_intake_job(conn, job_id, owner)
        except Exception:
            pass
    finally:
        heartbeat_stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=5)
        conn.close()


def queue_intake_job(db_path: Path, job_id: int) -> None:
    """Submit an intake job to the process-local executor."""

    INTAKE_EXECUTOR.submit(execute_intake_job, db_path, job_id)
