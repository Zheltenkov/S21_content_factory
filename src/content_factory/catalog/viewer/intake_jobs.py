"""Persistence helpers for intake jobs.

This module owns intake job CRUD and status hydration. The pipeline orchestration
module should not also know how to serialize result payloads or decorate jobs for
the UI.
"""

from __future__ import annotations

import json
from typing import Any

from content_factory.catalog.db import CatalogConnection
from content_factory.catalog.viewer._common import fetch_all, utc_now_iso
from content_factory.catalog.viewer.labels import intake_job_status_label, intake_stage_label


def create_intake_job(
    conn: CatalogConnection,
    *,
    source_kind: str,
    source_name: str | None,
    file_path: str | None,
    brief_text: str,
    use_council: bool,
) -> int:
    current_time = utc_now_iso()
    cursor = conn.execute(
        """
        INSERT INTO intake_job(
            source_kind,
            source_name,
            file_path,
            brief_text,
            status,
            current_stage,
            progress_note,
            use_council,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, 'pending', 'queued', 'Задача поставлена в очередь на обработку.', ?, ?, ?)
        """,
        (source_kind, source_name, file_path, brief_text, 1 if use_council else 0, current_time, current_time),
    )
    conn.commit()
    return int(cursor.lastrowid or 0)


def update_intake_job(
    conn: CatalogConnection,
    job_id: int,
    *,
    status: str | None = None,
    current_stage: str | None = None,
    progress_note: str | None = None,
    error_text: str | None = None,
    result_payload: dict[str, Any] | None = None,
    mark_started: bool = False,
    mark_finished: bool = False,
) -> None:
    fields: list[str] = ["updated_at = ?"]
    params: list[object] = [utc_now_iso()]

    if status is not None:
        fields.append("status = ?")
        params.append(status)
    if current_stage is not None:
        fields.append("current_stage = ?")
        params.append(current_stage)
    if progress_note is not None:
        fields.append("progress_note = ?")
        params.append(progress_note)
    if error_text is not None:
        fields.append("error_text = ?")
        params.append(error_text)
    if result_payload is not None:
        fields.append("result_payload = ?")
        params.append(json.dumps(result_payload, ensure_ascii=False))
    if mark_started:
        fields.append("started_at = ?")
        params.append(utc_now_iso())
    if mark_finished:
        fields.append("finished_at = ?")
        params.append(utc_now_iso())

    params.append(job_id)
    conn.execute(f"UPDATE intake_job SET {', '.join(fields)} WHERE id = ?", tuple(params))
    conn.commit()


def get_intake_job(conn: CatalogConnection, job_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM intake_job WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return None
    job = dict(row)
    if job.get("result_payload"):
        try:
            job["result_payload"] = json.loads(job["result_payload"])
        except json.JSONDecodeError:
            job["result_payload"] = None
    _hydrate_job_status_labels(job)
    return job


def get_intake_job_brief_id(conn: CatalogConnection, job_id: int) -> tuple[dict[str, Any] | None, int | None]:
    job = get_intake_job(conn, job_id)
    payload = job.get("result_payload") if job else None
    brief_id = payload.get("brief_id") if isinstance(payload, dict) else None
    return job, brief_id if isinstance(brief_id, int) else None


def list_recent_intake_jobs(conn: CatalogConnection, limit: int = 8) -> list[dict[str, Any]]:
    items = fetch_all(
        conn,
        """
        SELECT
            id,
            source_kind,
            source_name,
            status,
            current_stage,
            use_council,
            created_at,
            finished_at
        FROM intake_job
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
    for item in items:
        _hydrate_job_status_labels(item)
    return items


def _hydrate_job_status_labels(job: dict[str, Any]) -> None:
    job["status_label"] = intake_job_status_label(str(job.get("status")))
    job["current_stage_label"] = intake_stage_label(str(job.get("current_stage")))
