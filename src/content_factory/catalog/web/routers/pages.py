"""Read-only catalog pages ported to native FastAPI (Phase 5.1).

These paths have no POST sibling, so they can be served natively while the rest of
the viewer stays WSGI-mounted. The router is registered before the WSGI mount, so
these exact paths are handled here and everything else falls through to the mount.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from content_factory.catalog.db import CatalogConnection
from content_factory.catalog.viewer.app import (
    get_competency,
    get_competency_skills,
    get_profile,
    get_profile_tree,
    has_directory_hierarchy,
    list_competencies,
    list_directory_hierarchy,
    list_profiles,
)
from content_factory.catalog.web.deps import get_conn
from content_factory.catalog.web.rendering import CATALOG_URL_PREFIX, render

router = APIRouter(prefix="/app/spravochnik", tags=["catalog-ui"])


@router.get("")
@router.get("/")
def catalog_root() -> RedirectResponse:
    """Catalog entry point → the intake workspace (mirrors the WSGI ``/`` redirect)."""

    return RedirectResponse(f"{CATALOG_URL_PREFIX}/intake", status_code=303)


@router.get("/favicon.ico")
def catalog_favicon() -> Response:
    return Response(content=b"", status_code=204, media_type="image/x-icon")


@router.get("/competencies", response_class=HTMLResponse)
def competencies_page(
    q: str = Query(default=""),
    scope: str = Query(default="all"),
    conn: CatalogConnection = Depends(get_conn),
) -> HTMLResponse:
    query = q.strip()
    hierarchy_enabled = has_directory_hierarchy(conn)
    if hierarchy_enabled:
        competencies, directory_profile = list_directory_hierarchy(conn, query, scope)
    else:
        competencies = list_competencies(conn, query, scope)
        directory_profile = None
    html = render(
        "competencies.html",
        {
            "title": "Справочник",
            "query": query,
            "scope": scope,
            "competencies": competencies,
            "directory_profile": directory_profile,
            "hierarchy_mode": "typed" if hierarchy_enabled else "raw",
        },
        request_path="/competencies",
    )
    return HTMLResponse(html)


@router.get("/competencies/{competency_id}", response_class=HTMLResponse)
def competency_detail_page(
    competency_id: int,
    conn: CatalogConnection = Depends(get_conn),
) -> HTMLResponse:
    competency = get_competency(conn, competency_id)
    if not competency:
        raise HTTPException(status_code=404, detail="Competency not found")
    skills = get_competency_skills(conn, competency_id)
    html = render(
        "competency_detail.html",
        {"title": competency["title"], "competency": competency, "skills": skills},
        request_path=f"/competencies/{competency_id}",
    )
    return HTMLResponse(html)


@router.get("/profiles", response_class=HTMLResponse)
def profiles_page(
    service: str = Query(default="0"),
    conn: CatalogConnection = Depends(get_conn),
) -> HTMLResponse:
    include_service_profiles = service == "1"
    profiles = list_profiles(conn, include_service=include_service_profiles)
    html = render(
        "profiles.html",
        {
            "title": "Профили",
            "profiles": profiles,
            "include_service_profiles": include_service_profiles,
        },
        request_path="/profiles",
    )
    return HTMLResponse(html)


@router.get("/profiles/{profile_id}", response_class=HTMLResponse)
def profile_detail_page(
    profile_id: int,
    conn: CatalogConnection = Depends(get_conn),
) -> HTMLResponse:
    profile = get_profile(conn, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    competencies = get_profile_tree(conn, profile_id)
    html = render(
        "profile_detail.html",
        {"title": profile["name"], "profile": profile, "competencies": competencies},
        request_path=f"/profiles/{profile_id}",
    )
    return HTMLResponse(html)
