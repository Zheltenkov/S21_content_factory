"""Curriculum-constructor state model for the compact UP cockpit.

The constructor UI should not infer workflow state from scattered template
conditions. This module builds one view model from catalog facts: intake job,
reviews, catalog apply state, DAG payload, template proposals, and plan rows.
"""

from __future__ import annotations

from typing import Any, cast

from content_factory.catalog.db import CatalogConnection
from content_factory.catalog.pipeline.artifact_templates import load_curriculum_artifact_template_proposals
from content_factory.catalog.pipeline.curriculum import CurriculumDesignSpec
from content_factory.catalog.viewer._common import fetch_all, fetch_one, table_exists
from content_factory.catalog.viewer.curriculum_ops import get_curriculum_plan
from content_factory.catalog.viewer.intake_dag import count_brief_template_proposals, get_brief_catalog_apply_state
from content_factory.catalog.viewer.intake_reviews import count_open_prerequisite_edge_reviews_for_brief
from content_factory.catalog.viewer.intake_workspace import hydrate_job_result_payload
from content_factory.catalog.viewer.up_builder_derivation import derive_curriculum_builder_state
from content_factory.catalog.viewer.up_builder_models import (
    BriefOption,
    BuilderSnapshot,
    BuilderStage,  # noqa: F401 — реэкспорт модели для тестов/потребителей up_builder_state
    CurriculumBuilderState,
)
from content_factory.catalog.viewer.up_builder_parsing import (
    _as_dict,
    _build_brief_analysis,
    _build_skill_candidates,
    _build_template_proposals,
    _loads_json,
    _to_int,
    _to_optional_int,
)


def load_curriculum_builder_state(
    conn: CatalogConnection,
    *,
    brief_id: int | None = None,
    job_id: int | None = None,
    recent_limit: int = 8,
    select_latest: bool = False,
) -> CurriculumBuilderState:
    """Load constructor state for a selected or latest brief."""

    recent_briefs = _load_recent_briefs(conn, recent_limit)
    if job_id is not None:
        snapshot = _load_snapshot_by_job(conn, job_id)
    else:
        selected_id = brief_id if brief_id is not None else (recent_briefs[0].brief_id if select_latest and recent_briefs else None)
        snapshot = _load_snapshot(conn, selected_id) if selected_id is not None else BuilderSnapshot()
    return derive_curriculum_builder_state(snapshot, recent_briefs)


def _load_recent_briefs(conn: CatalogConnection, limit: int) -> list[BriefOption]:
    if not table_exists(conn, "profile_brief"):
        return []
    rows = fetch_all(
        conn,
        """
        SELECT id, role, domain, created_at
        FROM profile_brief
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [
        BriefOption(
            brief_id=int(row["id"]),
            role=str(row.get("role") or "Без роли"),
            domain=str(row.get("domain") or "Без домена"),
            created_at=str(row.get("created_at")) if row.get("created_at") else None,
        )
        for row in rows
    ]


def _load_snapshot(conn: CatalogConnection, brief_id: int) -> BuilderSnapshot:
    brief = _load_brief(conn, brief_id)
    latest_job = _load_latest_job(conn, brief_id)
    latest_job_payload = _as_dict(latest_job.get("result_payload") if latest_job else None)
    latest_job_payload = hydrate_job_result_payload(conn, latest_job_payload) or latest_job_payload
    plan = _load_latest_plan(conn, brief_id)
    suggestions = _count_skill_suggestions(conn, brief_id)
    review_counts = _count_skill_reviews(conn, brief_id)
    open_edge_reviews = count_open_prerequisite_edge_reviews_for_brief(conn, brief_id)
    catalog_state = get_brief_catalog_apply_state(conn, brief_id)
    proposal_counts = count_brief_template_proposals(conn, brief_id)
    template_proposals = _build_template_proposals(load_curriculum_artifact_template_proposals(conn, brief_id))
    dag_payload = _as_dict(latest_job_payload.get("dag"))
    persisted = _as_dict(latest_job_payload.get("persisted"))
    brief_analysis = _build_brief_analysis(latest_job_payload)
    skill_candidates = _build_skill_candidates(latest_job_payload.get("candidates"))
    candidate_review_count = sum(1 for candidate in skill_candidates if candidate.is_open)

    active_promotions = _to_int(catalog_state.get("active_promotions"))
    skill_set_items = _to_int(catalog_state.get("skill_set_items"))
    catalog_applied = (
        active_promotions > 0
        or skill_set_items > 0
        or _to_int(persisted.get("catalog_promoted")) > 0
        or proposal_counts["total"] > 0
        or (plan is not None and int(plan.get("id") or 0) > 0)
        or str(dag_payload.get("status") or "") == "catalog_applied"
    )
    order = dag_payload.get("order")
    order_count = len(order) if isinstance(order, list) else 0
    design_spec = _load_design_spec(conn, brief_id, dag_payload) if order_count else None
    plan_design = _as_dict(plan.get("design_spec") if plan else None)
    plan_design_current = bool(
        design_spec
        and design_spec.ready
        and plan_design.get("approved") is True
        and str(plan_design.get("design_hash") or "") == design_spec.design_hash
    )

    return BuilderSnapshot(
        brief_id=brief_id,
        role=str(brief.get("role") or ""),
        domain=str(brief.get("domain") or ""),
        seniority=str(brief.get("seniority") or ""),
        created_at=str(brief.get("created_at")) if brief.get("created_at") else None,
        latest_job_id=_to_optional_int(latest_job.get("id") if latest_job else None),
        latest_job_status=str(latest_job.get("status") or "") if latest_job else "",
        latest_job_stage=str(latest_job.get("current_stage") or "") if latest_job else "",
        latest_job_source=str(latest_job.get("source_name") or "") if latest_job else "",
        plan_id=_to_optional_int(plan.get("id") if plan else None),
        plan_status=str(plan.get("status") or "") if plan else "",
        plan_row_count=_to_int(plan.get("row_count") if plan else 0),
        plan_design_current=plan_design_current,
        total_suggestions=suggestions["total"],
        accepted_atomic_count=suggestions["accepted_atomic"],
        pending_atomic_count=suggestions["pending_atomic"],
        open_skill_reviews=max(review_counts["open"], candidate_review_count),
        open_edge_reviews=open_edge_reviews,
        active_promotions=active_promotions,
        skill_set_items=skill_set_items,
        catalog_applied=catalog_applied,
        dag_status=str(dag_payload.get("status") or ""),
        dag_message=str(dag_payload.get("message") or ""),
        dag_nodes=_to_int(dag_payload.get("nodes")),
        dag_order_count=order_count,
        template_total=proposal_counts["total"],
        template_open=proposal_counts["open"],
        template_accepted=proposal_counts["accepted"],
        template_rejected=proposal_counts["rejected"],
        brief_analysis=brief_analysis,
        skill_candidates=tuple(skill_candidates),
        template_proposals=tuple(template_proposals),
        design_spec=design_spec,
    )


def _load_design_spec(
    conn: CatalogConnection,
    brief_id: int,
    dag_payload: dict[str, Any],
) -> CurriculumDesignSpec | None:
    from content_factory.catalog.pipeline import stage_dag_to_up
    from content_factory.catalog.pipeline.curriculum import build_curriculum_design_spec
    from content_factory.catalog.viewer.intake_dag import load_accepted_skill_candidates, load_brief_spec_for_plan

    candidates, _ = load_accepted_skill_candidates(conn, brief_id)
    if not candidates:
        return None
    nodes = stage_dag_to_up.build_plan_nodes(candidates)
    return build_curriculum_design_spec(load_brief_spec_for_plan(conn, brief_id), nodes, dag_payload)


def _load_snapshot_by_job(conn: CatalogConnection, job_id: int) -> BuilderSnapshot:
    if not table_exists(conn, "intake_job"):
        return BuilderSnapshot()
    job = fetch_one(
        conn,
        """
        SELECT id, status, current_stage, source_name, result_payload, created_at
        FROM intake_job
        WHERE id = ?
        """,
        (job_id,),
    )
    if not job:
        return BuilderSnapshot()

    payload = _loads_json(job.get("result_payload"))
    brief_id = _to_optional_int(payload.get("brief_id"))
    if brief_id is not None:
        return _load_snapshot(conn, brief_id)

    return BuilderSnapshot(
        latest_job_id=_to_optional_int(job.get("id")),
        latest_job_status=str(job.get("status") or ""),
        latest_job_stage=str(job.get("current_stage") or ""),
        latest_job_source=str(job.get("source_name") or ""),
        created_at=str(job.get("created_at")) if job.get("created_at") else None,
    )


def _load_brief(conn: CatalogConnection, brief_id: int) -> dict[str, Any]:
    if not table_exists(conn, "profile_brief"):
        return {}
    return fetch_one(
        conn,
        """
        SELECT id, role, seniority, domain, created_at
        FROM profile_brief
        WHERE id = ?
        """,
        (brief_id,),
    ) or {}


def _load_latest_job(conn: CatalogConnection, brief_id: int) -> dict[str, Any] | None:
    if not table_exists(conn, "intake_job"):
        return None
    row = fetch_one(
        conn,
        """
        SELECT id, status, current_stage, source_name, result_payload, created_at, updated_at
        FROM intake_job
        WHERE json_valid(result_payload)
          AND CAST(json_extract(result_payload, '$.brief_id') AS INTEGER) = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (brief_id,),
    )
    if not row:
        return None
    row["result_payload"] = _loads_json(row.get("result_payload"))
    return row


def _load_latest_plan(conn: CatalogConnection, brief_id: int) -> dict[str, Any] | None:
    if not table_exists(conn, "curriculum_plan"):
        return None
    row = fetch_one(
        conn,
        """
        SELECT id
        FROM curriculum_plan
        WHERE brief_id = ?
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (brief_id,),
    )
    if not row:
        return None
    return cast(dict[str, Any], get_curriculum_plan(conn, int(row["id"])))


def _count_skill_suggestions(conn: CatalogConnection, brief_id: int) -> dict[str, int]:
    if not table_exists(conn, "skill_suggestion"):
        return {"total": 0, "accepted_atomic": 0, "pending_atomic": 0}
    row = fetch_one(
        conn,
        """
        SELECT
            COUNT(*) AS total_count,
            SUM(CASE WHEN entity_type = 'skill' AND atomicity = 'atomic' AND decision = 'accepted' THEN 1 ELSE 0 END)
                AS accepted_atomic_count,
            SUM(CASE WHEN entity_type = 'skill' AND atomicity = 'atomic' AND decision = 'needs_review' THEN 1 ELSE 0 END)
                AS pending_atomic_count
        FROM skill_suggestion
        WHERE brief_id = ?
        """,
        (brief_id,),
    )
    if not row:
        return {"total": 0, "accepted_atomic": 0, "pending_atomic": 0}
    return {
        "total": _to_int(row.get("total_count")),
        "accepted_atomic": _to_int(row.get("accepted_atomic_count")),
        "pending_atomic": _to_int(row.get("pending_atomic_count")),
    }


def _count_skill_reviews(conn: CatalogConnection, brief_id: int) -> dict[str, int]:
    if not table_exists(conn, "review_queue"):
        return {"open": 0}
    row = fetch_one(
        conn,
        """
        SELECT SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_count
        FROM review_queue
        WHERE source_ref = ?
          AND entity_type = 'skill'
          AND NOT (
              json_valid(details)
              AND json_extract(details, '$.review_kind') = 'prerequisite_edge'
          )
        """,
        (f"brief:{brief_id}",),
    )
    return {"open": _to_int(row.get("open_count") if row else 0)}
