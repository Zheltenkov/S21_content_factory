"""catalog-admin pages ported to native FastAPI (Phase 5.2).

GET renders the same templates as the legacy viewer; POST reads the form via
``request.form()`` (like the old ``parse_post_data``), dispatches on ``action`` and
redirects (PRG, 303). All mutation/query logic reuses the viewer's data functions.
"""

from __future__ import annotations

from content_factory.catalog.db import CatalogConnection
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from content_factory.catalog.pipeline import competency_catalog
from content_factory.catalog.pipeline import storage as intake_storage
from content_factory.catalog.viewer.app import (
    ARTIFACT_FAMILY_OPTIONS,
    ARTIFACT_SCOPE_TYPE_OPTIONS,
    add_skill_alias,
    create_catalog_group,
    create_catalog_indicator,
    create_catalog_skill,
    ensure_intake_runtime_schema,
    get_catalog_group,
    get_catalog_skill,
    get_skill_set,
    list_active_competency_options,
    list_archived_groups,
    list_archived_indicators,
    list_archived_skills,
    list_candidate_competencies,
    list_catalog_group_skills,
    list_catalog_groups,
    list_catalog_indicators,
    list_skill_aliases,
    list_skill_set_items,
    list_skill_sets,
    merge_candidate_competency,
    merge_catalog_skills,
    move_candidate_competency_skill,
    parse_artifact_template_scopes,
    parse_optional_int,
    remove_catalog_group,
    remove_catalog_indicator,
    remove_catalog_skill,
    remove_skill_alias,
    rename_candidate_competency,
    resolve_candidate_competency,
    restore_catalog_group,
    restore_catalog_indicator,
    restore_catalog_skill,
    search_catalog_skills,
    update_catalog_group,
    update_catalog_indicator,
    update_catalog_skill,
    utc_now_iso,
)
from content_factory.catalog.web.deps import catalog_db_path, get_conn
from content_factory.catalog.web.rendering import CATALOG_URL_PREFIX, render

router = APIRouter(prefix="/app/spravochnik", tags=["catalog-ui"])


async def _form(request: Request) -> dict[str, str]:
    """Read the POST body as a plain {field: value} dict (urlencoded forms)."""

    data = await request.form()
    return {key: str(value) for key, value in data.items()}


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(f"{CATALOG_URL_PREFIX}{path}", status_code=303)


@router.get("/catalog-admin", response_class=HTMLResponse)
def catalog_admin_root() -> RedirectResponse:
    return _redirect("/catalog-admin/groups")


# --------------------------------------------------------------------------- #
# candidate competencies
# --------------------------------------------------------------------------- #
@router.get("/catalog-admin/candidate-competencies", response_class=HTMLResponse)
def candidate_competencies_get(conn: CatalogConnection = Depends(get_conn)) -> HTMLResponse:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    candidates = list_candidate_competencies(conn)
    html = render(
        "catalog_admin_candidate_competencies.html",
        {
            "title": "Кандидатные компетенции",
            "candidates": candidates,
            "competency_options": list_active_competency_options(conn),
            "open_count": len([i for i in candidates if str(i.get("review_state") or "") == "needs_review"]),
        },
        request_path="/catalog-admin/candidate-competencies",
    )
    return HTMLResponse(html)


@router.post("/catalog-admin/candidate-competencies")
async def candidate_competencies_post(request: Request, conn: CatalogConnection = Depends(get_conn)) -> RedirectResponse:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    form = await _form(request)
    competency_id = int(form.get("competency_id", "0") or 0)
    action = form.get("action", "")
    if not competency_id or action not in {"accept", "reject", "review", "rename", "merge", "move_skill"}:
        raise HTTPException(status_code=404, detail="Invalid candidate competency action")
    if action == "rename":
        rename_candidate_competency(conn, competency_id, form.get("new_title", ""))
    elif action == "merge":
        target = int(form.get("target_competency_id", "0") or 0)
        if not target:
            raise HTTPException(status_code=404, detail="Target competency is required")
        merge_candidate_competency(conn, competency_id, target)
    elif action == "move_skill":
        target = int(form.get("target_competency_id", "0") or 0)
        competency_skill_id = int(form.get("competency_skill_id", "0") or 0)
        if not target or not competency_skill_id:
            raise HTTPException(status_code=404, detail="Target competency and skill link are required")
        move_candidate_competency_skill(conn, competency_skill_id, target)
    else:
        resolve_candidate_competency(conn, competency_id=competency_id, action=action, resolution_note=form.get("resolution_note", ""))
    return _redirect("/catalog-admin/candidate-competencies")


# --------------------------------------------------------------------------- #
# archive
# --------------------------------------------------------------------------- #
@router.get("/catalog-admin/archive", response_class=HTMLResponse)
def archive_get(
    q: str = Query(default=""),
    scope: str = Query(default="all"),
    conn: CatalogConnection = Depends(get_conn),
) -> HTMLResponse:
    archive_query = q.strip()
    archive_scope = scope.strip() or "all"
    if archive_scope not in {"all", "groups", "skills", "indicators"}:
        archive_scope = "all"
    groups = list_archived_groups(conn, archive_query) if archive_scope in {"all", "groups"} else []
    skills = list_archived_skills(conn, archive_query) if archive_scope in {"all", "skills"} else []
    indicators = list_archived_indicators(conn, archive_query) if archive_scope in {"all", "indicators"} else []
    html = render(
        "catalog_admin_archive.html",
        {
            "title": "Архив каталога",
            "archived_groups": groups,
            "archived_skills": skills,
            "archived_indicators": indicators,
            "archive_query": archive_query,
            "archive_scope": archive_scope,
        },
        request_path="/catalog-admin/archive",
    )
    return HTMLResponse(html)


@router.post("/catalog-admin/archive")
async def archive_post(request: Request, conn: CatalogConnection = Depends(get_conn)) -> RedirectResponse:
    form = await _form(request)
    action = form.get("action", "")
    if action == "restore_group":
        restore_catalog_group(conn, int(form["group_id"]))
    elif action == "restore_skill":
        restore_catalog_skill(conn, int(form["skill_id"]))
    elif action == "restore_indicator":
        restore_catalog_indicator(conn, int(form["indicator_id"]))
    redirect_params: dict[str, str] = {}
    if form.get("q", "").strip():
        redirect_params["q"] = form["q"].strip()
    if form.get("scope", "").strip() and form["scope"].strip() != "all":
        redirect_params["scope"] = form["scope"].strip()
    location = "/catalog-admin/archive"
    if redirect_params:
        location += "?" + urlencode(redirect_params)
    return _redirect(location)


# --------------------------------------------------------------------------- #
# artifact templates
# --------------------------------------------------------------------------- #
@router.get("/catalog-admin/artifact-templates", response_class=HTMLResponse)
def artifact_templates_get(
    edit: str = Query(default=""),
    conn: CatalogConnection = Depends(get_conn),
) -> HTMLResponse:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    templates = intake_storage.load_curriculum_artifact_templates(conn, active_only=False)
    edit_id = parse_optional_int(edit)
    edit_template = next((dict(item) for item in templates if int(item.get("id") or 0) == edit_id), None)
    if edit_template:
        scopes = edit_template.get("scopes") or []
        first_scope = scopes[0] if scopes else {}
        edit_template["scope_type"] = str(first_scope.get("scope_type") or "coverage_area") if isinstance(first_scope, dict) else "coverage_area"
        edit_template["scope_weight"] = str(first_scope.get("weight") or "1.0") if isinstance(first_scope, dict) else "1.0"
        edit_template["scope_names_text"] = "\n".join(
            str(scope.get("scope_name") or "")
            for scope in scopes
            if isinstance(scope, dict) and str(scope.get("scope_type") or "") != "any"
        )
    html = render(
        "catalog_admin_artifact_templates.html",
        {
            "title": "Шаблоны УП",
            "templates": templates,
            "edit_template": edit_template,
            "artifact_family_options": ARTIFACT_FAMILY_OPTIONS,
            "scope_type_options": ARTIFACT_SCOPE_TYPE_OPTIONS,
        },
        request_path="/catalog-admin/artifact-templates",
    )
    return HTMLResponse(html)


@router.post("/catalog-admin/artifact-templates")
async def artifact_templates_post(request: Request, conn: CatalogConnection = Depends(get_conn)) -> RedirectResponse:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    form = await _form(request)
    action = form.get("action", "")
    template_id = parse_optional_int(form.get("template_id"))
    if action == "save_template":
        intake_storage.upsert_curriculum_artifact_template(
            conn,
            code=form.get("code", "").strip() or form.get("title", "").strip(),
            title=form.get("title", "").strip() or "Шаблон артефакта",
            artifact_family=form.get("artifact_family", "practice").strip() or "practice",
            artifact_description=form.get("artifact_description", "").strip(),
            project_name_pattern=form.get("project_name_pattern", "").strip(),
            materials_pattern=form.get("materials_pattern", "").strip(),
            storytelling_pattern=form.get("storytelling_pattern", "").strip(),
            validation_criteria=form.get("validation_criteria", "").strip(),
            priority=parse_optional_int(form.get("priority")) or 100,
            status=form.get("status", "active").strip() or "active",
            source="methodologist",
            scopes=parse_artifact_template_scopes(form),
        )
    elif action in {"activate_template", "deprecate_template"} and template_id:
        status = "active" if action == "activate_template" else "deprecated"
        conn.execute(
            "UPDATE curriculum_artifact_template SET status = ?, updated_at = ? WHERE id = ?",
            (status, utc_now_iso(), template_id),
        )
        conn.commit()
    return _redirect("/catalog-admin/artifact-templates")


# --------------------------------------------------------------------------- #
# skill sets
# --------------------------------------------------------------------------- #
@router.get("/catalog-admin/skillsets", response_class=HTMLResponse)
def skillsets_get(conn: CatalogConnection = Depends(get_conn)) -> HTMLResponse:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    html = render(
        "catalog_admin_skillsets.html",
        {"title": "Наборы skills", "skill_sets": list_skill_sets(conn)},
        request_path="/catalog-admin/skillsets",
    )
    return HTMLResponse(html)


@router.get("/catalog-admin/skillsets/{skill_set_id}", response_class=HTMLResponse)
def skillset_detail_get(skill_set_id: int, conn: CatalogConnection = Depends(get_conn)) -> HTMLResponse:
    ensure_intake_runtime_schema(conn, catalog_db_path())
    skill_set = get_skill_set(conn, skill_set_id)
    if not skill_set:
        raise HTTPException(status_code=404, detail="Skill set not found")
    html = render(
        "catalog_admin_skillset_detail.html",
        {"title": str(skill_set["title"]), "skill_set": skill_set, "items": list_skill_set_items(conn, skill_set_id)},
        request_path=f"/catalog-admin/skillsets/{skill_set_id}",
    )
    return HTMLResponse(html)


# --------------------------------------------------------------------------- #
# groups
# --------------------------------------------------------------------------- #
@router.get("/catalog-admin/groups", response_class=HTMLResponse)
def groups_get(conn: CatalogConnection = Depends(get_conn)) -> HTMLResponse:
    html = render(
        "catalog_admin_groups.html",
        {"title": "Каталог DB", "groups": list_catalog_groups(conn)},
        request_path="/catalog-admin/groups",
    )
    return HTMLResponse(html)


@router.post("/catalog-admin/groups")
async def groups_post(request: Request, conn: CatalogConnection = Depends(get_conn)) -> RedirectResponse:
    form = await _form(request)
    action = form.get("action", "")
    if action == "create_group":
        create_catalog_group(conn, name=form.get("name", "").strip() or "Новая группа", sort_order=int(form.get("sort_order", "999") or 999), status=form.get("status", "active"))
    elif action == "update_group":
        update_catalog_group(conn, group_id=int(form["group_id"]), name=form.get("name", "").strip() or "Группа", sort_order=int(form.get("sort_order", "999") or 999), status=form.get("status", "active"))
    elif action == "remove_group":
        remove_catalog_group(conn, int(form["group_id"]))
    return _redirect("/catalog-admin/groups")


@router.get("/catalog-admin/groups/{group_id}", response_class=HTMLResponse)
def group_detail_get(group_id: int, conn: CatalogConnection = Depends(get_conn)) -> HTMLResponse:
    group = get_catalog_group(conn, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    html = render(
        "catalog_admin_group_detail.html",
        {"title": group["name"], "group": group, "skills": list_catalog_group_skills(conn, group_id)},
        request_path=f"/catalog-admin/groups/{group_id}",
    )
    return HTMLResponse(html)


@router.post("/catalog-admin/groups/{group_id}")
async def group_detail_post(group_id: int, request: Request, conn: CatalogConnection = Depends(get_conn)) -> RedirectResponse:
    form = await _form(request)
    action = form.get("action", "")
    if action == "update_group":
        update_catalog_group(conn, group_id=group_id, name=form.get("name", "").strip() or "Группа", sort_order=int(form.get("sort_order", "999") or 999), status=form.get("status", "active"))
    elif action == "create_skill":
        create_catalog_skill(
            conn,
            group_id=group_id,
            name=form.get("name", "").strip() or "Новый skill",
            sort_order=int(form.get("sort_order", "999") or 999),
            description=form.get("description", ""),
            source_skill_name=form.get("source_skill_name", ""),
            resolution_status=form.get("resolution_status", "manual"),
            match_note=form.get("match_note", ""),
            is_active=1 if form.get("is_active", "1") == "1" else 0,
        )
    elif action == "remove_skill":
        skill_id = int(form.get("skill_id", "0"))
        if skill_id:
            remove_catalog_skill(conn, skill_id)
    elif action == "remove_group":
        remove_catalog_group(conn, group_id)
        return _redirect("/catalog-admin/groups")
    return _redirect(f"/catalog-admin/groups/{group_id}")


# --------------------------------------------------------------------------- #
# skills
# --------------------------------------------------------------------------- #
@router.get("/catalog-admin/skills/{skill_id}", response_class=HTMLResponse)
def skill_detail_get(
    skill_id: int,
    merge_query: str = Query(default=""),
    competency_query: str = Query(default=""),
    conn: CatalogConnection = Depends(get_conn),
) -> HTMLResponse:
    skill = get_catalog_skill(conn, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    mq = merge_query.strip()
    cq = competency_query.strip()
    html = render(
        "catalog_admin_skill_detail.html",
        {
            "title": skill["name"],
            "skill": skill,
            "indicators": list_catalog_indicators(conn, skill_id),
            "aliases": list_skill_aliases(conn, skill_id),
            "merge_query": mq,
            "merge_candidates": search_catalog_skills(conn, mq, exclude_skill_id=skill_id) if mq else [],
            "competency_query": cq,
            "competency_links": competency_catalog.list_skill_competency_links(conn, skill_id),
            "competency_options": competency_catalog.list_competency_options(conn, cq),
        },
        request_path=f"/catalog-admin/skills/{skill_id}",
    )
    return HTMLResponse(html)


@router.post("/catalog-admin/skills/{skill_id}")
async def skill_detail_post(skill_id: int, request: Request, conn: CatalogConnection = Depends(get_conn)) -> RedirectResponse:
    form = await _form(request)
    action = form.get("action", "")
    if action == "update_skill":
        update_catalog_skill(
            conn,
            skill_id=skill_id,
            name=form.get("name", "").strip() or "Skill",
            sort_order=int(form.get("sort_order", "999") or 999),
            description=form.get("description", ""),
            source_skill_name=form.get("source_skill_name", ""),
            resolution_status=form.get("resolution_status", "manual"),
            match_note=form.get("match_note", ""),
            is_active=1 if form.get("is_active", "1") == "1" else 0,
        )
    elif action == "remove_skill":
        skill = get_catalog_skill(conn, skill_id)
        group_id = skill["group_id"] if skill else None
        remove_catalog_skill(conn, skill_id)
        return _redirect(f"/catalog-admin/groups/{group_id}") if group_id is not None else _redirect("/catalog-admin/groups")
    elif action == "add_alias":
        add_skill_alias(conn, skill_id=skill_id, alias=form.get("alias", ""), source="manual")
    elif action == "remove_alias":
        alias_id = int(form.get("alias_id", "0") or 0)
        if alias_id:
            remove_skill_alias(conn, skill_id, alias_id)
    elif action == "merge_skill":
        target_skill_id = int(form.get("target_skill_id", "0") or 0)
        if target_skill_id:
            merge_catalog_skills(conn, skill_id, target_skill_id)
            return _redirect(f"/catalog-admin/skills/{target_skill_id}")
    elif action == "link_competency":
        skill = get_catalog_skill(conn, skill_id)
        if skill:
            competency_catalog.ensure_skill_competency_link(
                conn,
                skill_id=skill_id,
                skill_name=str(skill.get("name") or "Skill"),
                competency_title=form.get("competency_title", ""),
                indicators=None,
                source_note="manual_catalog_admin",
            )
            conn.commit()
    elif action == "unlink_competency":
        competency_skill_id = int(form.get("competency_skill_id", "0") or 0)
        if competency_skill_id:
            competency_catalog.remove_competency_skill_link(conn, competency_skill_id)
    elif action == "create_indicator":
        create_catalog_indicator(
            conn,
            skill_id=skill_id,
            indicator_type=form.get("indicator_type", "Не указано"),
            text=form.get("text", "").strip() or "Новый индикатор",
            sort_order=int(form.get("sort_order", "999") or 999),
            complexity_band=form.get("complexity_band", ""),
            is_active=1 if form.get("is_active", "1") == "1" else 0,
        )
    elif action == "update_indicator":
        update_catalog_indicator(
            conn,
            indicator_id=int(form["indicator_id"]),
            indicator_type=form.get("indicator_type", "Не указано"),
            text=form.get("text", "").strip() or "Индикатор",
            sort_order=int(form.get("sort_order", "999") or 999),
            complexity_band=form.get("complexity_band", ""),
            is_active=1 if form.get("is_active", "1") == "1" else 0,
        )
    elif action == "remove_indicator":
        indicator_id = int(form.get("indicator_id", "0"))
        if indicator_id:
            remove_catalog_indicator(conn, indicator_id)
    return _redirect(f"/catalog-admin/skills/{skill_id}")
