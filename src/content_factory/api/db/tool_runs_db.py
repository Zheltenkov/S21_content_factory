"""Persistence helpers for unified tool runs and catalog migration."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from content_factory.api.db.models import SpravochnikCatalogEntity, ToolRun, utc_now_naive
from content_factory.api.db.session import SessionLocal


def create_tool_run(
    *,
    run_id: str,
    tool_name: str,
    user_id: str | None,
    input_ref: str | None = None,
    output_ref: str | None = None,
) -> None:
    """Insert a pending tool run."""

    with SessionLocal() as db:
        db.add(
            ToolRun(
                run_id=run_id,
                tool_name=tool_name,
                user_id=user_id,
                status="pending",
                input_ref=input_ref,
                output_ref=output_ref,
            )
        )
        db.commit()


def update_tool_run(
    run_id: str,
    *,
    status: str,
    summary: dict[str, Any] | None = None,
    output_ref: str | None = None,
    error: str | None = None,
) -> None:
    """Update a tool run status from a worker thread."""

    with SessionLocal() as db:
        run = db.query(ToolRun).filter(ToolRun.run_id == run_id).first()
        if run is None:
            return
        run_obj = cast(Any, run)
        run_obj.status = status
        run_obj.summary = summary if summary is not None else run_obj.summary
        run_obj.output_ref = output_ref if output_ref is not None else run_obj.output_ref
        run_obj.error = error
        run_obj.updated_at = utc_now_naive()
        db.commit()


def get_tool_run(db: Session, run_id: str, user_id: str | None = None) -> ToolRun | None:
    """Load one run with optional user scoping."""

    query = db.query(ToolRun).filter(ToolRun.run_id == run_id)
    if user_id:
        query = query.filter(ToolRun.user_id == user_id)
    return query.first()


def _sqlite_rows(db_path: Path, table_name: str) -> Iterable[sqlite3.Row]:
    """Yield all rows from a SQLite table when it exists."""

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        if not exists:
            return []
        return list(conn.execute(f"SELECT * FROM {table_name}"))


def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
    """Convert SQLite row values into JSON-safe payload."""

    payload: dict[str, Any] = {}
    for key in row.keys():
        value = row[key]
        if isinstance(value, bytes):
            payload[key] = value.decode("utf-8", errors="replace")
        else:
            payload[key] = value
    return payload


def import_spravochnik_catalog(db: Session, sqlite_path: Path) -> dict[str, int]:
    """Mirror key Spravochnik catalog tables into PostgreSQL JSON entities."""

    entity_specs = {
        "skill": "name",
        "competency": "title",
        "profile": "title",
        "indicator_row": "base_text",
        "review_queue": "reason_code",
        "curriculum_plan": "title",
        "curriculum_artifact_template": "name",
    }
    counts: dict[str, int] = {}
    now = datetime.utcnow()
    for table_name, title_column in entity_specs.items():
        rows = list(_sqlite_rows(sqlite_path, table_name))
        counts[table_name] = len(rows)
        for row in rows:
            payload = _row_payload(row)
            source_id = str(payload.get("id") or payload.get("row_id") or "")
            if not source_id:
                continue
            stmt = insert(SpravochnikCatalogEntity).values(
                entity_type=table_name,
                source_id=source_id,
                title=str(payload.get(title_column) or "") or None,
                status=str(payload.get("status") or "active"),
                payload=payload,
                source_updated_at=None,
                updated_at=now,
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_spravochnik_entity_source",
                set_={
                    "title": stmt.excluded.title,
                    "status": stmt.excluded.status,
                    "payload": stmt.excluded.payload,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
            db.execute(stmt)
    db.commit()
    return counts
