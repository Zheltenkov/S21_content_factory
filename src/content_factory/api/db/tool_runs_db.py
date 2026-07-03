"""Persistence helpers for unified tool runs."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy.orm import Session

from content_factory.api.db.models import ToolRun, utc_now_naive
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
