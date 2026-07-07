"""Review queue UI served by the native FastAPI catalog router.

GET renders the same ``reviews.html`` as the legacy viewer; POST updates a review
status (PRG, preserving the active filters in the redirect) and the two brief-level
actions (build-dag / apply-catalog) that jump to the resulting intake job.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from content_factory.catalog.db import CatalogConnection
from content_factory.catalog.viewer.intake_ops import (
    apply_brief_catalog_decisions,
    build_dag_for_brief,
    ensure_intake_runtime_schema,
    list_dag_build_options,
    list_reviews,
    update_review_status,
)
from content_factory.catalog.web.deps import catalog_db_path, get_conn
from content_factory.catalog.web.rendering import CATALOG_URL_PREFIX, render

router = APIRouter(prefix=CATALOG_URL_PREFIX, tags=["catalog-ui"])


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(f"{CATALOG_URL_PREFIX}{path}", status_code=303)


async def _form(request: Request) -> dict[str, str]:
    data = await request.form()
    return {key: str(value) for key, value in data.items()}


@router.get("/reviews", response_class=HTMLResponse)
def reviews_get(
    status: str = Query(default="open"),
    severity: str = Query(default="all"),
    reason: str = Query(default="all"),
    entity_type: str = Query(default="all"),
    conn: CatalogConnection = Depends(get_conn),
) -> HTMLResponse:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    status_totals, breakdown, items, reason_codes, entity_type_codes = list_reviews(
        conn,
        status_filter=status,
        severity_filter=severity,
        reason_filter=reason,
        entity_type_filter=entity_type,
    )
    html = render(
        "reviews.html",
        {
            "title": "Проверка импорта",
            "status_totals": status_totals,
            "breakdown": breakdown,
            "items": items,
            "status_filter": status,
            "severity_filter": severity,
            "reason_filter": reason,
            "entity_type_filter": entity_type,
            "reason_codes": reason_codes,
            "entity_type_codes": entity_type_codes,
            "dag_build_options": list_dag_build_options(conn),
        },
        request_path="/reviews",
    )
    return HTMLResponse(html)


@router.post("/reviews")
async def reviews_post(request: Request, conn: CatalogConnection = Depends(get_conn)) -> RedirectResponse:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    form = await _form(request)
    try:
        review_id = int(form.get("review_id", "0"))
    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid review id") from None
    new_status = form.get("new_status", "open")
    if new_status not in {"open", "resolved", "ignored"}:
        raise HTTPException(status_code=404, detail="Invalid review status")

    update_review_status(conn, review_id, new_status, form.get("resolution_note", ""))
    redirect_parts: list[str] = []
    redirect_status = "open" if new_status in {"resolved", "ignored"} else form.get("status", "open")
    if redirect_status:
        redirect_parts.append(f"status={redirect_status}")
    for key in ("severity", "reason", "entity_type"):
        value = form.get(key, "")
        if value:
            redirect_parts.append(f"{key}={value}")
    location = "/reviews"
    if redirect_parts:
        location += "?" + "&".join(redirect_parts)
    return _redirect(location)


@router.post("/reviews/build-dag")
async def reviews_build_dag(request: Request, conn: CatalogConnection = Depends(get_conn)) -> RedirectResponse:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    form = await _form(request)
    try:
        brief_id = int(form.get("brief_id", "0"))
    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid brief id") from None
    build_result = build_dag_for_brief(conn, brief_id)
    latest_job_id = build_result["state"].get("latest_job_id")
    if latest_job_id:
        return _redirect(f"/intake/jobs/{latest_job_id}")
    return _redirect("/reviews")


@router.post("/reviews/apply-catalog")
async def reviews_apply_catalog(request: Request, conn: CatalogConnection = Depends(get_conn)) -> RedirectResponse:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    form = await _form(request)
    try:
        brief_id = int(form.get("brief_id", "0"))
    except ValueError:
        raise HTTPException(status_code=404, detail="Invalid brief id") from None
    apply_result = apply_brief_catalog_decisions(conn, brief_id)
    dag_state = apply_result.get("dag_state") if isinstance(apply_result, dict) else None
    latest_job_id = dag_state.get("latest_job_id") if isinstance(dag_state, dict) else None
    if latest_job_id:
        return _redirect(f"/intake/jobs/{latest_job_id}")
    return _redirect("/reviews")
