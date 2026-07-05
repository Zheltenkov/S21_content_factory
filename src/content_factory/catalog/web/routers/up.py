"""Curriculum-plan (УП) UI ported to native FastAPI (Phase 5.4).

Covers the plan index, plan detail, CSV export (409 when the DAG order is invalid),
row create/edit/delete, and the artifact-template proposal workflow
(generate / save / accept / reject). All data logic reuses the viewer functions.

Route order matters: ``/rows/new`` is declared before ``/rows/{row_id}`` so the
literal segment is not swallowed by the int converter.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from content_factory.catalog.pipeline import storage as intake_storage
from content_factory.catalog.viewer.app import (
    ARTIFACT_FAMILY_OPTIONS,
    ARTIFACT_SCOPE_TYPE_OPTIONS,
    build_curriculum_plan_for_brief,
    cleanup_empty_curriculum_plans,
    create_curriculum_plan_row,
    curriculum_plan_to_csv_bytes,
    delete_curriculum_plan,
    delete_curriculum_plan_row,
    ensure_intake_runtime_schema,
    get_curriculum_plan,
    get_curriculum_plan_row,
    list_curriculum_plans,
    parse_optional_float,
    parse_scope_names,
    update_curriculum_plan_row,
)
from content_factory.catalog.web.deps import catalog_db_path, get_conn
from content_factory.catalog.web.rendering import CATALOG_URL_PREFIX, render

router = APIRouter(prefix=CATALOG_URL_PREFIX, tags=["catalog-ui"])

logger = logging.getLogger("content_factory.catalog.web.routers.up")


def _sync_up_curriculum() -> None:
    """Mirror changed UP data into the shared Postgres catalog (best-effort).

    Replaces the side effect the legacy ``PrefixRewriteASGI`` mount ran after every
    successful ``/up`` POST. Kept best-effort (never blocks the UI) — the JSON-blob
    mirror itself is slated for replacement by real ``catalog.*`` reads in Phase 4c.
    """

    try:
        from content_factory.api.integrations.spravochnik_curriculum_sync import (
            sync_spravochnik_curriculum_plans,
        )

        sync_spravochnik_curriculum_plans()
    except Exception:
        logger.exception("Failed to mirror Spravochnik curriculum plans after UP mutation")


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(f"{CATALOG_URL_PREFIX}{path}", status_code=303)


def _redirect_synced(path: str) -> RedirectResponse:
    """Redirect after a UP mutation, mirroring the change into the shared catalog."""

    _sync_up_curriculum()
    return _redirect(path)


async def _form(request: Request) -> dict[str, str]:
    data = await request.form()
    return {key: str(value) for key, value in data.items()}


def _require_plan(conn: sqlite3.Connection, plan_id: int) -> dict[str, Any]:
    plan = get_curriculum_plan(conn, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Curriculum plan not found")
    return plan


# --------------------------------------------------------------------------- #
# index
# --------------------------------------------------------------------------- #
@router.get("/up", response_class=HTMLResponse)
def up_index(conn: sqlite3.Connection = Depends(get_conn)) -> HTMLResponse:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    html = render(
        "up_index.html",
        {"title": "Учебные планы", "plans": list_curriculum_plans(conn)},
        request_path="/up",
    )
    return HTMLResponse(html)


@router.post("/up/cleanup-empty")
def up_cleanup_empty(conn: sqlite3.Connection = Depends(get_conn)) -> RedirectResponse:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    cleanup_empty_curriculum_plans(conn)
    return _redirect_synced("/up")


# --------------------------------------------------------------------------- #
# plan-level actions
# --------------------------------------------------------------------------- #
@router.post("/up/plans/{plan_id}/delete")
def up_plan_delete(plan_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> RedirectResponse:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    delete_curriculum_plan(conn, plan_id)
    return _redirect_synced("/up")


@router.get("/up/plans/{plan_id}/csv")
def up_plan_csv(plan_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> Response:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    plan = _require_plan(conn, plan_id)
    if str(plan.get("status") or "").casefold() == "invalid":
        return Response(
            content="CSV export is blocked: curriculum plan has DAG order violations.",
            status_code=409,
            media_type="text/plain; charset=utf-8",
        )
    filename = f"curriculum_plan_{plan_id}.csv"
    return Response(
        content=curriculum_plan_to_csv_bytes(plan),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --------------------------------------------------------------------------- #
# artifact-template proposals
# --------------------------------------------------------------------------- #
@router.post("/up/plans/{plan_id}/template-proposals/generate")
def up_plan_proposals_generate(plan_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> RedirectResponse:
    from content_factory.catalog.pipeline import llm as intake_llm

    ensure_intake_runtime_schema(conn, catalog_db_path())
    plan = _require_plan(conn, plan_id)
    brief_id = int(plan.get("brief_id") or 0)
    if not brief_id:
        raise HTTPException(status_code=404, detail="Curriculum plan has no brief")
    try:
        intake_llm.set_usage_context(brief_id=brief_id, stage="up_template_consilium")
        intake_storage.generate_curriculum_artifact_template_proposals(conn, brief_id=brief_id, plan_id=plan_id)
    finally:
        intake_llm.clear_usage_context()
    return _redirect_synced(f"/up/plans/{plan_id}/template-proposals")


@router.get("/up/plans/{plan_id}/template-proposals", response_class=HTMLResponse)
def up_plan_proposals_get(plan_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> HTMLResponse:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    plan = _require_plan(conn, plan_id)
    proposals = intake_storage.load_curriculum_artifact_template_proposals(conn, int(plan.get("brief_id") or 0))
    html = render(
        "up_template_proposals.html",
        {
            "title": f"Предложения шаблонов УП #{plan_id}",
            "plan": plan,
            "proposals": proposals,
            "artifact_family_options": ARTIFACT_FAMILY_OPTIONS,
            "scope_type_options": ARTIFACT_SCOPE_TYPE_OPTIONS,
        },
        request_path="/up",
    )
    return HTMLResponse(html)


@router.post("/up/plans/{plan_id}/template-proposals/{proposal_id}")
async def up_plan_proposal_post(
    plan_id: int, proposal_id: int, request: Request, conn: sqlite3.Connection = Depends(get_conn)
) -> RedirectResponse:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    plan = _require_plan(conn, plan_id)
    form = await _form(request)
    action = form.get("action", "")
    redirect_plan_id = plan_id
    if action in {"save_proposal", "accept_proposal"}:
        scope_type = form.get("scope_type", "coverage_area").strip() or "coverage_area"
        intake_storage.update_curriculum_artifact_template_proposal(
            conn,
            proposal_id,
            title=form.get("title", "").strip(),
            artifact_family=form.get("artifact_family", "practice").strip() or "practice",
            scope_type=scope_type,
            scope_names=parse_scope_names(form.get("scope_names"), scope_type),
            artifact_description=form.get("artifact_description", "").strip(),
            project_name_pattern=form.get("project_name_pattern", "").strip(),
            materials_pattern=form.get("materials_pattern", "").strip(),
            storytelling_pattern=form.get("storytelling_pattern", "").strip(),
            validation_criteria=form.get("validation_criteria", "").strip(),
            rationale=form.get("rationale", "").strip(),
            confidence=parse_optional_float(form.get("confidence")),
        )
    if action == "accept_proposal":
        intake_storage.accept_curriculum_artifact_template_proposal(conn, proposal_id)
        brief_id = int(plan.get("brief_id") or 0)
        rebuilt = build_curriculum_plan_for_brief(conn, brief_id)
        redirect_plan_id = int(rebuilt.get("plan_id") or plan_id)
        conn.execute(
            """
            UPDATE curriculum_artifact_template_proposal
            SET plan_id = COALESCE(plan_id, ?)
            WHERE brief_id = ?
            """,
            (redirect_plan_id, brief_id),
        )
        conn.commit()
    elif action == "reject_proposal":
        intake_storage.reject_curriculum_artifact_template_proposal(conn, proposal_id)
    return _redirect_synced(f"/up/plans/{redirect_plan_id}/template-proposals")


# --------------------------------------------------------------------------- #
# rows
# --------------------------------------------------------------------------- #
@router.post("/up/plans/{plan_id}/rows/new")
def up_plan_row_new(plan_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> RedirectResponse:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    _require_plan(conn, plan_id)
    row_id = create_curriculum_plan_row(conn, plan_id)
    return _redirect_synced(f"/up/plans/{plan_id}/rows/{row_id}")


@router.get("/up/plans/{plan_id}/rows/{row_id}", response_class=HTMLResponse)
def up_plan_row_get(plan_id: int, row_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> HTMLResponse:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    plan = get_curriculum_plan(conn, plan_id)
    row = get_curriculum_plan_row(conn, plan_id, row_id)
    if not plan or not row:
        raise HTTPException(status_code=404, detail="Curriculum plan row not found")
    html = render(
        "up_row_edit.html",
        {"title": f"Редактирование строки УП #{row_id}", "plan": plan, "row": row},
        request_path="/up",
    )
    return HTMLResponse(html)


@router.post("/up/plans/{plan_id}/rows/{row_id}")
async def up_plan_row_post(
    plan_id: int, row_id: int, request: Request, conn: sqlite3.Connection = Depends(get_conn)
) -> Response:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    plan = get_curriculum_plan(conn, plan_id)
    row = get_curriculum_plan_row(conn, plan_id, row_id)
    if not plan or not row:
        raise HTTPException(status_code=404, detail="Curriculum plan row not found")
    form = await _form(request)
    try:
        update_curriculum_plan_row(conn, plan_id, row_id, form)
    except ValueError as exc:
        html = render(
            "up_row_edit.html",
            {
                "title": f"Редактирование строки УП #{row_id}",
                "plan": get_curriculum_plan(conn, plan_id),
                "row": {**row, **form},
                "form_error": str(exc),
            },
            request_path="/up",
        )
        return HTMLResponse(html, status_code=400)
    return _redirect_synced(f"/up/plans/{plan_id}")


@router.post("/up/plans/{plan_id}/rows/{row_id}/delete")
def up_plan_row_delete(plan_id: int, row_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> RedirectResponse:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    delete_curriculum_plan_row(conn, plan_id, row_id)
    return _redirect_synced(f"/up/plans/{plan_id}")


# --------------------------------------------------------------------------- #
# plan detail (declared last: only matches when no sub-path above did)
# --------------------------------------------------------------------------- #
@router.get("/up/plans/{plan_id}", response_class=HTMLResponse)
def up_plan_detail(plan_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> HTMLResponse:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    plan = _require_plan(conn, plan_id)
    html = render(
        "up_detail.html",
        {"title": f"УП #{plan_id}", "plan": plan},
        request_path=f"/up/plans/{plan_id}",
    )
    return HTMLResponse(html)
