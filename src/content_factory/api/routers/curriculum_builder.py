"""Server-rendered cockpit for the curriculum-plan constructor."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from content_factory.catalog.db import CatalogConnection
from content_factory.catalog.pipeline import storage as intake_storage
from content_factory.catalog.viewer._common import parse_optional_float
from content_factory.catalog.viewer.curriculum_ops import get_curriculum_plan, parse_scope_names
from content_factory.catalog.viewer.intake_brief_io import load_brief_text
from content_factory.catalog.viewer.intake_catalog_apply import (
    apply_brief_catalog_decisions,
    apply_candidate_decision,
)
from content_factory.catalog.viewer.intake_dag import (
    approve_brief_curriculum_design,
    build_curriculum_plan_for_brief,
    build_dag_for_brief,
)
from content_factory.catalog.viewer.intake_jobs import create_intake_job, get_intake_job_brief_id
from content_factory.catalog.viewer.intake_runtime import ensure_intake_runtime_schema, queue_intake_job
from content_factory.catalog.viewer.ui_constants import ARTIFACT_FAMILY_OPTIONS, ARTIFACT_SCOPE_TYPE_OPTIONS
from content_factory.catalog.viewer.up_builder_state import load_curriculum_builder_state
from content_factory.catalog.web.deps import catalog_db_path, get_conn
from content_factory.catalog.web.form_parsing import form_and_files, form_fields
from content_factory.catalog.web.rendering import render

router = APIRouter(include_in_schema=False)


def _redirect_curriculum(
    *, brief_id: int | None = None, job_id: int | None = None, anchor: str = ""
) -> RedirectResponse:
    if brief_id is not None:
        return RedirectResponse(f"/app/curriculum?brief_id={brief_id}{anchor}", status_code=303)
    if job_id is not None:
        return RedirectResponse(f"/app/curriculum?job_id={job_id}", status_code=303)
    return RedirectResponse("/app/curriculum", status_code=303)


def _candidate_action_target(
    conn: CatalogConnection,
    suggestion_id: int,
    action: str,
) -> tuple[str, str]:
    """Resolve one UI action to the persisted candidate decision."""

    if action == "accept":
        return "accepted", "Подтверждено в конструкторе УП."
    if action == "reject":
        return "rejected", "Отклонено в конструкторе УП."
    if action == "review":
        return "needs_review", "Возвращено на проверку в конструкторе УП."
    if action == "link":
        from content_factory.catalog.pipeline import storage as intake_storage

        link_result = intake_storage.link_suggestion_to_nearest(conn, suggestion_id)
        if link_result.get("status") != "linked":
            raise HTTPException(status_code=404, detail="Nearest catalog skill not found")
        canonical_name = link_result.get("canonical_name") or link_result.get("skill_id")
        return "accepted", f"Покрыто существующим skill: {canonical_name}."
    raise HTTPException(status_code=404, detail="Invalid candidate action")


def _require_builder_plan(conn: CatalogConnection, plan_id: int) -> dict[str, Any]:
    plan = get_curriculum_plan(conn, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Curriculum plan not found")
    if not int(plan.get("brief_id") or 0):
        raise HTTPException(status_code=404, detail="Curriculum plan has no brief")
    return plan


def _require_builder_proposal(conn: CatalogConnection, proposal_id: int, brief_id: int) -> None:
    proposal = conn.execute(
        "SELECT brief_id FROM curriculum_artifact_template_proposal WHERE id = ?",
        (proposal_id,),
    ).fetchone()
    if not proposal or int(proposal["brief_id"]) != brief_id:
        raise HTTPException(status_code=404, detail="Template proposal does not belong to this brief")


def _require_template_readiness(conn: CatalogConnection, brief_id: int) -> None:
    state = load_curriculum_builder_state(conn, brief_id=brief_id)
    if not state.snapshot.dag_valid:
        raise HTTPException(status_code=409, detail="Template review requires a valid DAG")
    if not state.snapshot.design_spec or not state.snapshot.design_spec.ready:
        raise HTTPException(status_code=409, detail="Accept the curriculum design before reviewing templates")


def _require_design_readiness(conn: CatalogConnection, brief_id: int) -> None:
    state = load_curriculum_builder_state(conn, brief_id=brief_id)
    if state.snapshot.open_edge_reviews > 0:
        raise HTTPException(status_code=409, detail="Resolve all prerequisite edge reviews before building the plan")
    if not state.snapshot.design_spec or not state.snapshot.design_spec.ready:
        raise HTTPException(status_code=409, detail="Accept the curriculum design before building the plan")


def _render_builder(
    conn: CatalogConnection,
    *,
    brief_id: int | None = None,
    job_id: int | None = None,
    brief_text: str = "",
    form_error: str | None = None,
    upload_name: str | None = None,
) -> HTMLResponse:
    builder_state = load_curriculum_builder_state(conn, brief_id=brief_id, job_id=job_id)
    html = render(
        "up_builder.html",
        {
            "title": "Конструктор УП",
            "builder": builder_state,
            "wordmark_href": "/app/curriculum",
            "show_main_nav": False,
            "brief_form": {
                "brief": brief_text,
                "form_error": form_error,
                "upload_name": upload_name,
            },
            "artifact_family_options": ARTIFACT_FAMILY_OPTIONS,
            "scope_type_options": ARTIFACT_SCOPE_TYPE_OPTIONS,
        },
        request_path="/up",
        ecosystem_active_code="curriculum",
    )
    return HTMLResponse(html, status_code=400 if form_error else 200)


@router.get("/app/curriculum", response_class=HTMLResponse)
def curriculum_builder_page(
    brief_id: int | None = Query(default=None),
    job_id: int | None = Query(default=None),
    conn: CatalogConnection = Depends(get_conn),
) -> HTMLResponse:
    """Render the compact UP constructor entrypoint."""

    ensure_intake_runtime_schema(conn, catalog_db_path())
    return _render_builder(conn, brief_id=brief_id, job_id=job_id)


@router.post("/app/curriculum/briefs")
async def curriculum_builder_brief_post(request: Request, conn: CatalogConnection = Depends(get_conn)) -> Response:
    """Create an intake job from the constructor without leaving the cockpit."""

    ensure_intake_runtime_schema(conn, catalog_db_path())
    form_data, files = await form_and_files(request)
    try:
        brief_text, upload_name, source_kind, file_path = load_brief_text(form_data, files)
    except ValueError as exc:
        return _render_builder(
            conn,
            brief_text=form_data.get("brief", ""),
            form_error=str(exc),
            upload_name=None,
        )

    if not brief_text:
        return _render_builder(
            conn,
            brief_text=form_data.get("brief", ""),
            form_error="Нужно вставить текст брифа или выбрать файл.",
            upload_name=upload_name,
        )

    from content_factory.catalog.pipeline import config as intake_config

    job_id = create_intake_job(
        conn,
        source_kind=source_kind,
        source_name=upload_name,
        file_path=file_path,
        brief_text=brief_text,
        use_council=intake_config.USE_COUNCIL,
    )
    queue_intake_job(catalog_db_path(), job_id)
    return _redirect_curriculum(job_id=job_id)


@router.post("/app/curriculum/jobs/{job_id}/apply-catalog")
def curriculum_builder_apply_catalog(job_id: int, conn: CatalogConnection = Depends(get_conn)) -> RedirectResponse:
    """Apply accepted skills and return to the constructor state for the brief."""

    ensure_intake_runtime_schema(conn, catalog_db_path())
    job, brief_id = get_intake_job_brief_id(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Intake job not found")
    if brief_id is None:
        raise HTTPException(status_code=404, detail="Brief id not found")
    apply_brief_catalog_decisions(conn, brief_id)
    return _redirect_curriculum(brief_id=brief_id, anchor="#structure-transition")


@router.post("/app/curriculum/jobs/{job_id}/candidate-decision")
async def curriculum_builder_candidate_decision(
    job_id: int,
    request: Request,
    conn: CatalogConnection = Depends(get_conn),
) -> RedirectResponse:
    """Persist one human skill decision and stay inside the constructor."""

    ensure_intake_runtime_schema(conn, catalog_db_path())
    job, brief_id = get_intake_job_brief_id(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Intake job not found")
    if brief_id is None:
        raise HTTPException(status_code=404, detail="Brief id not found")

    form_data = await form_fields(request)
    try:
        suggestion_id = int(form_data.get("suggestion_id", "0"))
    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid suggestion id") from None

    suggestion = conn.execute(
        "SELECT brief_id FROM skill_suggestion WHERE id = ?",
        (suggestion_id,),
    ).fetchone()
    if not suggestion or int(suggestion["brief_id"]) != brief_id:
        raise HTTPException(status_code=404, detail="Suggestion does not belong to this brief")

    target_decision, resolution_note = _candidate_action_target(
        conn,
        suggestion_id,
        form_data.get("candidate_action", ""),
    )
    apply_candidate_decision(conn, suggestion_id, target_decision, resolution_note)
    return _redirect_curriculum(brief_id=brief_id, anchor="#skills-review")


@router.post("/app/curriculum/jobs/{job_id}/build-dag")
def curriculum_builder_build_dag(job_id: int, conn: CatalogConnection = Depends(get_conn)) -> RedirectResponse:
    """Build DAG and return to the constructor state for the brief."""

    ensure_intake_runtime_schema(conn, catalog_db_path())
    job, brief_id = get_intake_job_brief_id(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Intake job not found")
    if brief_id is None:
        raise HTTPException(status_code=404, detail="Brief id not found")
    build_dag_for_brief(conn, brief_id)
    return _redirect_curriculum(brief_id=brief_id, anchor="#structure-transition")


@router.post("/app/curriculum/briefs/{brief_id}/design/approve")
def curriculum_builder_approve_design(
    brief_id: int,
    conn: CatalogConnection = Depends(get_conn),
) -> RedirectResponse:
    """Accept the journey contract before template review and final UP assembly."""

    ensure_intake_runtime_schema(conn, catalog_db_path())
    try:
        approve_brief_curriculum_design(conn, brief_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _redirect_curriculum(brief_id=brief_id, anchor="#program-design")


@router.post("/app/curriculum/plans/{plan_id}/template-proposals/generate")
def curriculum_builder_generate_templates(
    plan_id: int,
    conn: CatalogConnection = Depends(get_conn),
) -> RedirectResponse:
    """Generate template proposals after DAG readiness and stay in the constructor."""

    ensure_intake_runtime_schema(conn, catalog_db_path())
    plan = _require_builder_plan(conn, plan_id)
    brief_id = int(plan["brief_id"])
    _require_template_readiness(conn, brief_id)

    from content_factory.catalog.pipeline import llm as intake_llm

    try:
        intake_llm.set_usage_context(brief_id=brief_id, stage="up_template_consilium")
        intake_storage.generate_curriculum_artifact_template_proposals(conn, brief_id=brief_id, plan_id=plan_id)
    finally:
        intake_llm.clear_usage_context()
    return _redirect_curriculum(brief_id=brief_id, anchor="#template-review")


@router.post("/app/curriculum/plans/{plan_id}/template-proposals/{proposal_id}")
async def curriculum_builder_template_proposal(
    plan_id: int,
    proposal_id: int,
    request: Request,
    conn: CatalogConnection = Depends(get_conn),
) -> RedirectResponse:
    """Save one human template decision without rebuilding the plan prematurely."""

    ensure_intake_runtime_schema(conn, catalog_db_path())
    plan = _require_builder_plan(conn, plan_id)
    brief_id = int(plan["brief_id"])
    _require_builder_proposal(conn, proposal_id, brief_id)
    _require_template_readiness(conn, brief_id)

    form = await form_fields(request)
    action = form.get("action", "")
    if action not in {"save_proposal", "accept_proposal", "reject_proposal", "publish_proposal"}:
        raise HTTPException(status_code=404, detail="Invalid template proposal action")

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
            repeatable=form.get("repeatable", "").strip().lower() in {"1", "true", "on", "yes"},
        )
    if action == "accept_proposal":
        intake_storage.accept_curriculum_artifact_template_proposal(conn, proposal_id)
    elif action == "reject_proposal":
        intake_storage.reject_curriculum_artifact_template_proposal(conn, proposal_id)
    elif action == "publish_proposal":
        result = intake_storage.publish_curriculum_artifact_template_proposal(conn, proposal_id)
        if result.get("status") != "published":
            raise HTTPException(status_code=409, detail="Only an accepted brief template can be published")
    return _redirect_curriculum(brief_id=brief_id, anchor="#template-review")


@router.post("/app/curriculum/briefs/{brief_id}/build-plan")
def curriculum_builder_build_plan(
    brief_id: int,
    conn: CatalogConnection = Depends(get_conn),
) -> RedirectResponse:
    """Build the UP once all template decisions are closed."""

    ensure_intake_runtime_schema(conn, catalog_db_path())
    state = load_curriculum_builder_state(conn, brief_id=brief_id)
    if not state.snapshot.dag_valid:
        raise HTTPException(status_code=409, detail="Curriculum plan requires a valid DAG")
    if state.snapshot.template_open > 0:
        raise HTTPException(status_code=409, detail="Resolve all template proposals before building the plan")
    if state.snapshot.template_accepted <= 0:
        raise HTTPException(status_code=409, detail="Accept at least one template before building the plan")
    _require_design_readiness(conn, brief_id)
    build_curriculum_plan_for_brief(conn, brief_id)
    return _redirect_curriculum(brief_id=brief_id, anchor="#plan-ready")
