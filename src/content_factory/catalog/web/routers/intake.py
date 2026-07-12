"""Intake pipeline UI served by the native FastAPI catalog router.

Covers the brief workspace (GET/POST), the async job view + JSON status polling
(same contract the inline ``intake.html`` JS expects), the workflow actions
(next-step / build-dag / apply-catalog / candidate-decision) and the curriculum
plan CSV export. Pipeline/data logic lives in ``catalog.viewer`` modules; this
file owns only Starlette/FastAPI transport concerns.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from content_factory.catalog.db import CatalogConnection
from content_factory.catalog.viewer.curriculum_ops import curriculum_plan_to_csv_bytes
from content_factory.catalog.viewer.intake_brief_io import load_brief_text
from content_factory.catalog.viewer.intake_catalog_apply import (
    apply_brief_catalog_decisions,
    apply_candidate_decision,
)
from content_factory.catalog.viewer.intake_cleanup import clear_intake_workspace
from content_factory.catalog.viewer.intake_dag import build_dag_for_brief, get_brief_dag_state
from content_factory.catalog.viewer.intake_jobs import (
    create_intake_job,
    get_intake_job,
    get_intake_job_brief_id,
    list_recent_intake_jobs,
)
from content_factory.catalog.viewer.intake_runtime import (
    ensure_intake_runtime_schema,
    queue_intake_job,
)
from content_factory.catalog.viewer.intake_workspace import (
    build_intake_workspace_state,
    hydrate_job_result_payload,
)
from content_factory.catalog.viewer.labels import intake_job_status_label, intake_stage_label
from content_factory.catalog.web.deps import catalog_db_path, get_conn
from content_factory.catalog.web.form_parsing import form_and_files, form_fields
from content_factory.catalog.web.rendering import CATALOG_URL_PREFIX, render

router = APIRouter(prefix=CATALOG_URL_PREFIX, tags=["catalog-ui"])


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(f"{CATALOG_URL_PREFIX}{path}", status_code=303)


def _render_intake(context: dict[str, object]) -> str:
    return render("intake.html", context, request_path="/intake")


# --------------------------------------------------------------------------- #
# brief workspace
# --------------------------------------------------------------------------- #
@router.get("/intake", response_class=HTMLResponse)
def intake_get() -> RedirectResponse:
    """Retire the duplicate brief workspace in favor of the UP constructor."""

    return RedirectResponse("/app/curriculum", status_code=303)


@router.post("/intake")
async def intake_post(request: Request, conn: CatalogConnection = Depends(get_conn)) -> Response:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    form_data, files = await form_and_files(request)
    try:
        brief_text, upload_name, source_kind, file_path = load_brief_text(form_data, files)
    except ValueError as exc:
        html = _render_intake(
            {
                "title": "Бриф",
                "brief": form_data.get("brief", ""),
                "job": None,
                "recent_jobs": list_recent_intake_jobs(conn),
                "result": None,
                "form_error": str(exc),
                "upload_name": None,
            }
        )
        return HTMLResponse(html, status_code=400)

    if not brief_text:
        html = _render_intake(
            {
                "title": "Бриф",
                "brief": "",
                "job": None,
                "recent_jobs": list_recent_intake_jobs(conn),
                "result": None,
                "form_error": "Нужно вставить текст брифа или загрузить файл.",
                "upload_name": upload_name,
            }
        )
        return HTMLResponse(html, status_code=400)

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
    return RedirectResponse(f"/app/curriculum?job_id={job_id}", status_code=303)


@router.post("/intake/jobs/clear")
def intake_jobs_clear(conn: CatalogConnection = Depends(get_conn)) -> RedirectResponse:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    clear_intake_workspace(conn)
    return RedirectResponse("/app/curriculum", status_code=303)


# --------------------------------------------------------------------------- #
# async job — JSON status polling
# --------------------------------------------------------------------------- #
@router.get("/intake/jobs/{job_id}/status")
def intake_job_status(job_id: int, conn: CatalogConnection = Depends(get_conn)) -> JSONResponse:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    job = get_intake_job(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Intake job not found")
    return JSONResponse(
        {
            "id": job["id"],
            "status": job["status"],
            "status_label": intake_job_status_label(str(job.get("status"))),
            "current_stage": job.get("current_stage"),
            "current_stage_label": intake_stage_label(str(job.get("current_stage"))),
            "progress_note": job.get("progress_note"),
            "error_text": job.get("error_text"),
            "finished_at": job.get("finished_at"),
        }
    )


# --------------------------------------------------------------------------- #
# workflow actions
# --------------------------------------------------------------------------- #
@router.post("/intake/jobs/{job_id}/next-step")
def intake_job_next_step(job_id: int, conn: CatalogConnection = Depends(get_conn)) -> RedirectResponse:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    job = get_intake_job(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Intake job not found")

    result = job.get("result_payload") if job.get("status") == "succeeded" else None
    result = hydrate_job_result_payload(conn, result)
    dag_build_state = None
    if isinstance(result, dict) and isinstance(result.get("brief_id"), int):
        dag_build_state = get_brief_dag_state(conn, int(result["brief_id"]))
    workspace_state = build_intake_workspace_state(conn, job, result, dag_build_state)
    next_step = workspace_state.get("next_step") if isinstance(workspace_state, dict) else None
    next_code = str(next_step.get("code") or "") if isinstance(next_step, dict) else ""
    brief_id = workspace_state.get("brief_id") if isinstance(workspace_state, dict) else None
    brief_id = brief_id if isinstance(brief_id, int) else None

    if next_code == "apply_catalog" and brief_id is not None:
        apply_brief_catalog_decisions(conn, brief_id)
        return _redirect(f"/intake/jobs/{job_id}")
    if next_code == "build_dag" and brief_id is not None:
        build_result = build_dag_for_brief(conn, brief_id)
        latest_job_id = build_result["state"].get("latest_job_id") or job_id
        return _redirect(f"/intake/jobs/{latest_job_id}")
    if isinstance(next_step, dict) and next_step.get("href"):
        return _redirect(str(next_step["href"]))
    return _redirect(f"/intake/jobs/{job_id}")


@router.post("/intake/jobs/{job_id}/build-dag")
def intake_job_build_dag(job_id: int, conn: CatalogConnection = Depends(get_conn)) -> RedirectResponse:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    job, brief_id = get_intake_job_brief_id(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Intake job not found")
    if brief_id is None:
        raise HTTPException(status_code=404, detail="Brief id not found")
    build_result = build_dag_for_brief(conn, brief_id)
    latest_job_id = build_result["state"].get("latest_job_id") or job_id
    return _redirect(f"/intake/jobs/{latest_job_id}")


@router.post("/intake/jobs/{job_id}/apply-catalog")
def intake_job_apply_catalog(job_id: int, conn: CatalogConnection = Depends(get_conn)) -> RedirectResponse:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    job, brief_id = get_intake_job_brief_id(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Intake job not found")
    if brief_id is None:
        raise HTTPException(status_code=404, detail="Brief id not found")
    apply_brief_catalog_decisions(conn, brief_id)
    return _redirect(f"/intake/jobs/{job_id}")


@router.post("/intake/jobs/{job_id}/candidate-decision")
async def intake_job_candidate_decision(
    job_id: int, request: Request, conn: CatalogConnection = Depends(get_conn)
) -> Response:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    form_data = await form_fields(request)
    try:
        suggestion_id = int(form_data.get("suggestion_id", "0"))
    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid suggestion id") from None
    action = form_data.get("candidate_action", "")
    if action not in {"accept", "link", "reject", "review"}:
        raise HTTPException(status_code=404, detail="Invalid candidate action")
    target_decision = "needs_review"
    resolution_note = "Возвращено на review из intake-таблицы."
    if action == "accept":
        target_decision = "accepted"
        resolution_note = "Подтверждено из intake-таблицы."
    elif action == "link":
        from content_factory.catalog.pipeline import storage as intake_storage

        link_result = intake_storage.link_suggestion_to_nearest(conn, suggestion_id)
        if link_result.get("status") != "linked":
            raise HTTPException(status_code=404, detail="Nearest catalog skill not found")
        target_decision = "accepted"
        resolution_note = f"Покрыто существующим skill: {link_result.get('canonical_name') or link_result.get('skill_id')}."
    elif action == "reject":
        target_decision = "rejected"
        resolution_note = "Отклонено из intake-таблицы."

    wants_json = "application/json" in request.headers.get("accept", "") or request.headers.get(
        "x-requested-with", ""
    ) == "fetch"
    apply_candidate_decision(conn, suggestion_id, target_decision, resolution_note)
    if wants_json:
        return JSONResponse(
            {
                "ok": True,
                "suggestion_id": suggestion_id,
                "decision": target_decision,
                "message": "Решение сохранено. После завершения проверки примените принятые навыки в справочник.",
            }
        )
    return _redirect(f"/intake/jobs/{job_id}")


# --------------------------------------------------------------------------- #
# curriculum plan CSV
# --------------------------------------------------------------------------- #
@router.get("/intake/jobs/{job_id}/plan.csv")
def intake_job_plan_csv(job_id: int, conn: CatalogConnection = Depends(get_conn)) -> Response:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    job = get_intake_job(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Intake job not found")
    result_payload = job.get("result_payload")
    if not isinstance(result_payload, dict):
        raise HTTPException(status_code=404, detail="Curriculum plan not found")
    plan_payload = result_payload.get("curriculum_plan")
    if not isinstance(plan_payload, dict) or not plan_payload.get("rows"):
        raise HTTPException(status_code=404, detail="Curriculum plan rows not found")
    filename = f"curriculum_plan_brief_{result_payload.get('brief_id', job_id)}.csv"
    return Response(
        content=curriculum_plan_to_csv_bytes(plan_payload),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --------------------------------------------------------------------------- #
# legacy full-view URL (declared last: matches only after the sub-paths above)
# --------------------------------------------------------------------------- #
@router.get("/intake/jobs/{job_id}", response_class=HTMLResponse)
def intake_job_detail(job_id: int, conn: CatalogConnection = Depends(get_conn)) -> RedirectResponse:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    job = get_intake_job(conn, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Intake job not found")
    return RedirectResponse(f"/app/curriculum?job_id={job_id}", status_code=303)
