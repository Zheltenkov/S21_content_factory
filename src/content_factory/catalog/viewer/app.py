from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from content_factory.catalog.db import CatalogConnection

if TYPE_CHECKING:
    pass

from content_factory.catalog.viewer._common import (
    UploadedFile,  # noqa: F401  re-exported for catalog/web routers
    _read_request_body,  # noqa: F401  re-exported for catalog/web routers
    clean_profile_name,
    clean_profile_slug,
    format_local_datetime,  # noqa: F401  re-exported for catalog/web routers
    load_summary,  # noqa: F401  re-exported for catalog/web routers
    parse_multipart_form_data,  # noqa: F401  re-exported for catalog/web routers
    parse_optional_float,  # noqa: F401  re-exported for catalog/web routers
    parse_optional_int,  # noqa: F401  re-exported for catalog/web routers
    parse_path_int,  # noqa: F401  re-exported for catalog/web routers
    parse_post_data,  # noqa: F401  re-exported for catalog/web routers
    parse_post_form_and_files,  # noqa: F401  re-exported for catalog/web routers
    refresh_summary_counts,  # noqa: F401  re-exported for catalog/web routers
    table_exists,
    utc_now_iso,
)
from content_factory.catalog.viewer.candidate_competency_ops import (
    _competency_similarity_label,  # noqa: F401
    close_candidate_competency_if_empty,  # noqa: F401
    competency_token_set,  # noqa: F401
    ensure_service_profile_competency,  # noqa: F401
    list_active_competency_options,  # noqa: F401
    list_candidate_competencies,  # noqa: F401
    list_candidate_competency_skills,  # noqa: F401
    list_competency_similarity_candidates,  # noqa: F401
    merge_candidate_competency,  # noqa: F401
    move_candidate_competency_skill,  # noqa: F401
    prune_empty_profile_competencies,  # noqa: F401
    rename_candidate_competency,  # noqa: F401
    resolve_candidate_competency,  # noqa: F401
)
from content_factory.catalog.viewer.catalog_admin_ops import (
    COMPLEXITY_OPTIONS,  # noqa: F401
    add_skill_alias,  # noqa: F401
    build_complexity_summary,  # noqa: F401
    complexity_label_for_band,  # noqa: F401
    create_catalog_group,  # noqa: F401
    create_catalog_indicator,  # noqa: F401
    create_catalog_skill,  # noqa: F401
    find_alias_owner,  # noqa: F401
    get_catalog_group,  # noqa: F401
    get_catalog_indicator,  # noqa: F401
    get_catalog_skill,  # noqa: F401
    get_skill_set,  # noqa: F401
    list_archived_groups,  # noqa: F401
    list_archived_indicators,  # noqa: F401
    list_archived_skills,  # noqa: F401
    list_catalog_group_skills,  # noqa: F401
    list_catalog_groups,  # noqa: F401
    list_catalog_indicators,  # noqa: F401
    list_skill_aliases,  # noqa: F401
    list_skill_set_items,  # noqa: F401
    list_skill_sets,  # noqa: F401
    merge_catalog_skills,  # noqa: F401
    parse_artifact_template_scopes,  # noqa: F401
    refresh_catalog_skill_complexity,  # noqa: F401
    remove_catalog_group,  # noqa: F401
    remove_catalog_indicator,  # noqa: F401
    remove_catalog_skill,  # noqa: F401
    remove_skill_alias,  # noqa: F401
    restore_catalog_group,  # noqa: F401
    restore_catalog_indicator,  # noqa: F401
    restore_catalog_skill,  # noqa: F401
    search_catalog_skills,  # noqa: F401
    update_catalog_group,  # noqa: F401
    update_catalog_indicator,  # noqa: F401
    update_catalog_skill,  # noqa: F401
)
from content_factory.catalog.viewer.curriculum_ops import (
    _count_up_outcomes,  # noqa: F401
    _count_up_skills,  # noqa: F401
    build_curriculum_plan_payload_from_rows,  # noqa: F401
    build_curriculum_quality_metrics_for_ui,  # noqa: F401
    cleanup_empty_curriculum_plans,  # noqa: F401
    create_curriculum_plan_row,  # noqa: F401
    curriculum_plan_status_label,  # noqa: F401
    curriculum_plan_to_csv_bytes,  # noqa: F401
    delete_curriculum_plan,  # noqa: F401
    delete_curriculum_plan_row,  # noqa: F401
    get_curriculum_plan,  # noqa: F401
    get_curriculum_plan_row,  # noqa: F401
    list_curriculum_plans,  # noqa: F401
    load_curriculum_plan_rows,  # noqa: F401
    parse_scope_names,  # noqa: F401
    reset_curriculum_plan_payload_in_jobs,  # noqa: F401
    sync_curriculum_plan_payload,  # noqa: F401
    update_curriculum_plan_row,  # noqa: F401
    weighted_skills_from_row,  # noqa: F401
)
from content_factory.catalog.viewer.intake_brief_io import (
    decode_uploaded_text,  # noqa: F401
    extract_brief_text_from_bytes,  # noqa: F401
    extract_csv_text,  # noqa: F401
    extract_docx_text,  # noqa: F401
    load_brief_text,  # noqa: F401
)
from content_factory.catalog.viewer.intake_catalog_apply import (
    apply_brief_catalog_decisions,  # noqa: F401
    apply_candidate_decision,  # noqa: F401
    load_brief_catalog_promotion_summary,  # noqa: F401
    update_jobs_catalog_payload,  # noqa: F401
)
from content_factory.catalog.viewer.intake_cleanup import (
    clear_intake_workspace,  # noqa: F401
    prune_empty_generated_catalog_nodes,  # noqa: F401
)
from content_factory.catalog.viewer.intake_dag import (
    build_curriculum_plan_for_brief,  # noqa: F401
    build_dag_for_brief,  # noqa: F401
    build_deferred_dag_payload,  # noqa: F401
    clear_brief_curriculum_plan_artifacts,  # noqa: F401
    clear_brief_dag_artifacts,  # noqa: F401
    count_brief_template_proposals,  # noqa: F401
    get_brief_catalog_apply_state,  # noqa: F401
    get_brief_dag_state,  # noqa: F401
    get_latest_job_id_for_brief,  # noqa: F401
    list_dag_build_options,  # noqa: F401
    load_accepted_skill_candidates,  # noqa: F401
    load_brief_spec_for_plan,  # noqa: F401
    load_prerequisite_edge_decisions,  # noqa: F401
    refresh_brief_dag_state,  # noqa: F401
    update_jobs_dag_payload,  # noqa: F401
)
from content_factory.catalog.viewer.intake_jobs import (
    create_intake_job,  # noqa: F401
    get_intake_job,  # noqa: F401
    get_intake_job_brief_id,  # noqa: F401
    list_recent_intake_jobs,  # noqa: F401
    update_intake_job,  # noqa: F401
)
from content_factory.catalog.viewer.intake_ops import (
    run_intake_pipeline,  # noqa: F401
)
from content_factory.catalog.viewer.intake_reviews import (
    count_open_candidate_competencies,  # noqa: F401
    count_open_prerequisite_edge_reviews_for_brief,  # noqa: F401
    count_open_skill_reviews_for_brief,  # noqa: F401
    format_prerequisite_edge_review,  # noqa: F401
    list_reviews,  # noqa: F401
    parse_review_details_json,  # noqa: F401
    repair_intake_review_links,  # noqa: F401
    save_prerequisite_edge_decision,  # noqa: F401
    split_edge_label,  # noqa: F401
    split_review_reason_codes,  # noqa: F401
    update_review_status,  # noqa: F401
)
from content_factory.catalog.viewer.intake_runtime import (
    ensure_intake_runtime_schema,  # noqa: F401
    execute_intake_job,  # noqa: F401
    queue_intake_job,  # noqa: F401
    repair_stale_intake_jobs,  # noqa: F401
)
from content_factory.catalog.viewer.intake_workspace import (
    _reason_set,  # noqa: F401
    build_candidate_recommended_action,  # noqa: F401
    build_intake_workflow_steps,  # noqa: F401
    build_intake_workspace_state,  # noqa: F401
    build_similarity_hint,  # noqa: F401
    hydrate_job_result_payload,  # noqa: F401
    load_nearest_skill_preview,  # noqa: F401
)
from content_factory.catalog.viewer.labels import (
    edge_reason_label,  # noqa: F401  re-exported for catalog/web routers
    intake_job_status_label,  # noqa: F401  re-exported for catalog/web routers
    intake_stage_label,  # noqa: F401  re-exported for catalog/web routers
    review_entity_label,  # noqa: F401  re-exported for catalog/web routers
    review_severity_label,  # noqa: F401  re-exported for catalog/web routers
    review_source_label,  # noqa: F401  re-exported for catalog/web routers
    review_status_label,  # noqa: F401  re-exported for catalog/web routers
    review_text_label,  # noqa: F401  re-exported for catalog/web routers
)
from content_factory.catalog.viewer.observability import (
    build_intake_quality_metrics,  # noqa: F401  re-exported for catalog.web.routers.intake
    build_job_observability,  # noqa: F401  re-exported for catalog.web.routers.intake
    load_llm_usage_summary,  # noqa: F401  re-exported for catalog.web.routers.intake
)
from content_factory.catalog.viewer.read_queries import (
    get_competency,  # noqa: F401  re-exported for catalog/web routers
    get_competency_skills,  # noqa: F401  re-exported for catalog/web routers
    get_profile,  # noqa: F401  re-exported for catalog/web routers
    get_profile_tree,  # noqa: F401  re-exported for catalog/web routers
    has_directory_hierarchy,  # noqa: F401  re-exported for catalog/web routers
    list_canonical_directory_additions,  # noqa: F401  re-exported for catalog/web routers
    list_competencies,  # noqa: F401  re-exported for catalog/web routers
    list_directory_hierarchy,  # noqa: F401  re-exported for catalog/web routers
    list_profiles,  # noqa: F401  re-exported for catalog/web routers
    resolve_directory_profile,  # noqa: F401  re-exported for catalog/web routers
)
from content_factory.catalog.viewer.ui_constants import (
    ARTIFACT_FAMILY_OPTIONS,  # noqa: F401
    ARTIFACT_SCOPE_TYPE_OPTIONS,  # noqa: F401
    DEFAULT_SUMMARY,  # noqa: F401
    INTAKE_PROGRESS_STEPS,  # noqa: F401
    STATIC_DIR,  # noqa: F401
    TEMPLATES_DIR,  # noqa: F401
)


def repair_dirty_profile_names(conn: CatalogConnection) -> int:
    if not table_exists(conn, "profile"):
        return 0
    updated = 0
    for row in conn.execute("SELECT id, name, slug FROM profile ORDER BY id").fetchall():
        current_name = str(row["name"] or "")
        cleaned_name = clean_profile_name(current_name)
        current_slug = str(row["slug"] or "")
        cleaned_slug = clean_profile_slug(current_slug)
        if cleaned_name and cleaned_name != current_name:
            conn.execute("UPDATE profile SET name = ? WHERE id = ?", (cleaned_name, row["id"]))
            updated += 1
        if cleaned_slug and cleaned_slug != current_slug:
            exists = conn.execute(
                "SELECT 1 FROM profile WHERE slug = ? AND id != ?",
                (cleaned_slug, row["id"]),
            ).fetchone()
            if not exists:
                conn.execute("UPDATE profile SET slug = ? WHERE id = ?", (cleaned_slug, row["id"]))
                updated += 1
    if updated:
        conn.commit()
    return updated


def ensure_catalog_group(
    conn: CatalogConnection,
    code: str,
    name: str,
    sort_order: int,
    status: str = "active",
    source: str = "derived",
) -> int:
    row = conn.execute(
        "SELECT id FROM skill_group WHERE code = ? OR name = ? ORDER BY id LIMIT 1",
        (code, name),
    ).fetchone()
    if row:
        conn.execute(
            """
            UPDATE skill_group
            SET code = ?,
                name = ?,
                sort_order = ?,
                status = ?,
                source = COALESCE(NULLIF(source, ''), ?),
                updated_at = ?
            WHERE id = ?
            """,
            (code, name, sort_order, status, source, utc_now_iso(), int(row["id"])),
        )
        return int(row["id"])
    cursor = conn.execute(
        """
        INSERT INTO skill_group(code, name, sort_order, status, source, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (code, name, sort_order, status, source, utc_now_iso()),
    )
    return int(cursor.lastrowid or 0)


def response(start_response: Callable[..., object], body: bytes, status: str = "200 OK", content_type: str = "text/html; charset=utf-8", headers: list[tuple[str, str]] | None = None) -> list[bytes]:
    final_headers = [("Content-Type", content_type), ("Content-Length", str(len(body)))]
    if headers:
        final_headers.extend(headers)
    start_response(status, final_headers)
    return [body]


def html_response(start_response: Callable[..., object], html: str, status: str = "200 OK") -> list[bytes]:
    return response(start_response, html.encode("utf-8"), status=status)


def json_response(start_response: Callable[..., object], payload: dict[str, Any], status: str = "200 OK") -> list[bytes]:
    return response(
        start_response,
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        status=status,
        content_type="application/json; charset=utf-8",
    )


def redirect_response(start_response: Callable[..., object], location: str) -> list[bytes]:
    return response(start_response, b"", status="302 Found", headers=[("Location", location)])


def not_found(start_response: Callable[..., object], text: str = "Not found") -> list[bytes]:
    return response(start_response, text.encode("utf-8"), status="404 Not Found", content_type="text/plain; charset=utf-8")
