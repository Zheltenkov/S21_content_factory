"""DAG and curriculum-plan operations for intake briefs.

This module owns the brief-level DAG state, DAG payload persistence, DAG artifact
cleanup, accepted-skill loading, and the build path that produces both a DAG and
the derived curriculum plan. Keeping these operations here prevents review/status
code from importing the wider intake orchestration hub.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from content_factory.catalog.db import CatalogConnection, CatalogRow
from content_factory.catalog.viewer._common import column_exists, table_exists, utc_now_iso
from content_factory.catalog.viewer.curriculum_ops import (
    build_deferred_curriculum_plan_payload,
    update_jobs_curriculum_plan_payload,
)

if TYPE_CHECKING:
    from content_factory.catalog.pipeline.models import SkillCandidate


def get_latest_job_id_for_brief(conn: CatalogConnection, brief_id: int) -> int | None:
    row = conn.execute(
        """
        SELECT id
        FROM intake_job
        WHERE json_valid(result_payload)
          AND CAST(json_extract(result_payload, '$.brief_id') AS INTEGER) = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (brief_id,),
    ).fetchone()
    return int(row["id"]) if row else None


def get_brief_dag_state(conn: CatalogConnection, brief_id: int) -> dict[str, Any]:
    accepted_atomic = conn.execute(
        """
        SELECT COUNT(*)
        FROM skill_suggestion
        WHERE brief_id = ?
          AND entity_type = 'skill'
          AND atomicity = 'atomic'
          AND decision = 'accepted'
        """,
        (brief_id,),
    ).fetchone()[0]
    pending_atomic = conn.execute(
        """
        SELECT COUNT(*)
        FROM skill_suggestion
        WHERE brief_id = ?
          AND entity_type = 'skill'
          AND atomicity = 'atomic'
          AND decision = 'needs_review'
        """,
        (brief_id,),
    ).fetchone()[0]
    open_reviews = conn.execute(
        """
        SELECT COUNT(*)
        FROM review_queue
        WHERE source_ref = ?
          AND entity_type = 'skill'
          AND status = 'open'
          AND NOT (
              json_valid(details)
              AND json_extract(details, '$.review_kind') = 'prerequisite_edge'
          )
        """,
        (f"brief:{brief_id}",),
    ).fetchone()[0]
    prerequisite_rows = (
        conn.execute(
            "SELECT COUNT(*) FROM skill_prerequisite WHERE brief_id = ?",
            (brief_id,),
        ).fetchone()[0]
        if table_exists(conn, "skill_prerequisite") and column_exists(conn, "skill_prerequisite", "brief_id")
        else 0
    )
    brief_row = conn.execute(
        "SELECT role, domain FROM profile_brief WHERE id = ?",
        (brief_id,),
    ).fetchone()
    return {
        "brief_id": brief_id,
        "role": brief_row["role"] if brief_row else None,
        "domain": brief_row["domain"] if brief_row else None,
        "latest_job_id": get_latest_job_id_for_brief(conn, brief_id),
        "accepted_atomic_count": int(accepted_atomic),
        "pending_atomic_count": int(pending_atomic),
        "open_review_count": int(open_reviews),
        "prerequisite_count": int(prerequisite_rows),
    }


def load_prerequisite_edge_decisions(conn: CatalogConnection, brief_id: int) -> dict[str, str]:
    if not table_exists(conn, "prerequisite_edge_decision"):
        return {}
    return {
        str(row["edge_key"]): str(row["decision"])
        for row in conn.execute(
            """
            SELECT edge_key, decision
            FROM prerequisite_edge_decision
            WHERE brief_id = ?
            """,
            (brief_id,),
        )
    }


def build_deferred_dag_payload(state: dict[str, Any], *, status: str, message: str) -> dict[str, Any]:
    return {
        "status": status,
        "message": message,
        "accepted_atomic_candidates": int(state["accepted_atomic_count"]),
        "pending_atomic_candidates": int(state["pending_atomic_count"]),
        "open_review_count": int(state["open_review_count"]),
        "nodes": 0,
        "edges": 0,
        "removed_cycle": 0,
        "removed_transitive": 0,
        "acyclic": True,
        "waves": [],
        "order": [],
        "final_edges": [],
        "edge_review_queue": [],
        "used_candidate_ids": [],
    }


def update_jobs_dag_payload(
    conn: CatalogConnection,
    brief_id: int,
    dag_payload: dict[str, Any],
    persisted_update: dict[str, Any] | None = None,
) -> None:
    rows = conn.execute(
        """
        SELECT id, result_payload
        FROM intake_job
        WHERE status = 'succeeded'
          AND json_valid(result_payload)
          AND CAST(json_extract(result_payload, '$.brief_id') AS INTEGER) = ?
        """,
        (brief_id,),
    ).fetchall()
    for row in rows:
        payload = json.loads(row["result_payload"])
        payload["dag"] = dag_payload
        if persisted_update and isinstance(payload.get("persisted"), dict):
            payload["persisted"].update(persisted_update)
        conn.execute(
            "UPDATE intake_job SET result_payload = ?, updated_at = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), utc_now_iso(), row["id"]),
        )
    conn.commit()


def clear_brief_dag_artifacts(
    conn: CatalogConnection,
    brief_id: int,
    *,
    preserve_edge_reviews: bool = False,
    clear_edge_decisions: bool = False,
) -> None:
    if table_exists(conn, "skill_prerequisite") and column_exists(conn, "skill_prerequisite", "brief_id"):
        conn.execute("DELETE FROM skill_prerequisite WHERE brief_id = ?", (brief_id,))
    if table_exists(conn, "review_queue") and not preserve_edge_reviews:
        conn.execute(
            """
            DELETE FROM review_queue
            WHERE source_ref = ?
              AND json_valid(details)
              AND json_extract(details, '$.review_kind') = 'prerequisite_edge'
            """,
            (f"brief:{brief_id}",),
        )
    if clear_edge_decisions and table_exists(conn, "prerequisite_edge_decision"):
        conn.execute("DELETE FROM prerequisite_edge_decision WHERE brief_id = ?", (brief_id,))
    conn.commit()


def clear_brief_curriculum_plan_artifacts(conn: CatalogConnection, brief_id: int) -> None:
    if table_exists(conn, "curriculum_plan_row"):
        conn.execute(
            """
            DELETE FROM curriculum_plan_row
            WHERE plan_id IN (SELECT id FROM curriculum_plan WHERE brief_id = ?)
            """,
            (brief_id,),
        )
    if table_exists(conn, "curriculum_plan"):
        conn.execute("DELETE FROM curriculum_plan WHERE brief_id = ?", (brief_id,))
    conn.commit()


def refresh_brief_dag_state(
    conn: CatalogConnection,
    brief_id: int,
    *,
    status: str = "deferred",
    message: str | None = None,
) -> dict[str, Any]:
    state = get_brief_dag_state(conn, brief_id)
    if message is None:
        if state["accepted_atomic_count"]:
            message = "Граф будет пересчитан по текущему набору принятых атомарных навыков."
            status = "stale" if state["prerequisite_count"] else status
        else:
            message = "Граф пока пуст: нет принятых атомарных навыков."
    dag_payload = build_deferred_dag_payload(state, status=status, message=message)
    update_jobs_dag_payload(
        conn,
        brief_id,
        dag_payload,
        persisted_update={
            "skill_prerequisite": 0,
            "prerequisite_reviews": 0,
            "review_open": int(state["open_review_count"]),
        },
    )
    return state


def count_brief_template_proposals(conn: CatalogConnection, brief_id: int) -> dict[str, int]:
    if not table_exists(conn, "curriculum_artifact_template_proposal"):
        return {"total": 0, "open": 0, "accepted": 0, "rejected": 0}
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total_count,
            SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_count,
            SUM(CASE WHEN status = 'accepted' THEN 1 ELSE 0 END) AS accepted_count,
            SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected_count
        FROM curriculum_artifact_template_proposal
        WHERE brief_id = ?
        """,
        (brief_id,),
    ).fetchone()
    if not row:
        return {"total": 0, "open": 0, "accepted": 0, "rejected": 0}
    return {
        "total": int(row["total_count"] or 0),
        "open": int(row["open_count"] or 0),
        "accepted": int(row["accepted_count"] or 0),
        "rejected": int(row["rejected_count"] or 0),
    }


def get_brief_catalog_apply_state(conn: CatalogConnection, brief_id: int) -> dict[str, Any]:
    accepted_atomic = 0
    active_promotions = 0
    active_promoted_skills = 0
    if table_exists(conn, "skill_suggestion"):
        accepted_atomic = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM skill_suggestion
                WHERE brief_id = ?
                  AND entity_type = 'skill'
                  AND atomicity = 'atomic'
                  AND decision = 'accepted'
                """,
                (brief_id,),
            ).fetchone()[0]
        )
    if table_exists(conn, "skill_promotion_log") and table_exists(conn, "skill_suggestion"):
        promotion_row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_promotions,
                COUNT(DISTINCT spl.skill_id) AS distinct_skills
            FROM skill_promotion_log spl
            JOIN skill_suggestion ss ON ss.id = spl.suggestion_id
            WHERE ss.brief_id = ?
              AND spl.status = 'active'
            """,
            (brief_id,),
        ).fetchone()
        if isinstance(promotion_row, CatalogRow):
            total_promotions = promotion_row["total_promotions"]
            distinct_skills = promotion_row["distinct_skills"]
        else:
            total_promotions = promotion_row[0]
            distinct_skills = promotion_row[1]
        active_promotions = int(total_promotions or 0)
        active_promoted_skills = int(distinct_skills or 0)
    skill_set_items = 0
    if table_exists(conn, "skill_set") and table_exists(conn, "skill_set_item"):
        skill_set_items = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM skill_set_item ssi
                JOIN skill_set ss ON ss.id = ssi.skill_set_id
                WHERE ss.source_type = 'brief'
                  AND ss.source_id = ?
                  AND ss.status = 'active'
                """,
                (brief_id,),
            ).fetchone()[0]
        )
    templates = count_brief_template_proposals(conn, brief_id)
    # Several accepted candidates can legitimately resolve into one canonical skill.
    # DAG/UP readiness must compare skillset rows with unique promoted skills, not
    # with the raw candidate count, otherwise deduplication blocks the workflow.
    catalog_applied = bool(
        accepted_atomic and active_promotions >= accepted_atomic and skill_set_items >= active_promoted_skills
    )
    return {
        "accepted_atomic": accepted_atomic,
        "active_promotions": active_promotions,
        "active_promoted_skills": active_promoted_skills,
        "skill_set_items": skill_set_items,
        "template_proposals": templates["total"],
        "open_template_proposals": templates["open"],
        "accepted_template_proposals": templates["accepted"],
        "catalog_applied": catalog_applied,
    }


def load_accepted_skill_candidates(
    conn: CatalogConnection, brief_id: int
) -> tuple[list[SkillCandidate], dict[str, int]]:
    from content_factory.catalog.pipeline.models import IndicatorSpec, SkillCandidate

    rows = conn.execute(
        """
        SELECT
            ss.id,
            ss.suggested_name,
            ss.group_name,
            ss.coverage_area,
            ss.bloom,
            ss.indicators_json,
            ss.tools,
            ss.evidence_ids,
            ss.resolution,
            ss.canonical_skill_id,
            s.canonical_name,
            ss.confidence,
            ss.council_agreement
        FROM skill_suggestion ss
        LEFT JOIN skill s ON s.id = ss.canonical_skill_id
        WHERE ss.brief_id = ?
          AND ss.entity_type = 'skill'
          AND ss.atomicity = 'atomic'
          AND ss.decision = 'accepted'
        ORDER BY ss.id
        """,
        (brief_id,),
    ).fetchall()

    bloom_fallback = {"remember", "understand", "apply", "analyze", "evaluate", "create"}
    cands: list[SkillCandidate] = []
    tmp_to_db: dict[str, int] = {}
    for row in rows:
        bloom_label = str(row["bloom"] or "remember").strip().casefold()
        if bloom_label not in bloom_fallback:
            bloom_label = "remember"
        raw_indicators = json.loads(row["indicators_json"] or "[]")
        indicators: list[IndicatorSpec] = []
        for item in raw_indicators:
            if not isinstance(item, dict):
                continue
            indicator_bloom = str(item.get("bloom") or bloom_label).strip().casefold()
            if indicator_bloom not in bloom_fallback:
                indicator_bloom = bloom_label
            indicators.append(
                IndicatorSpec(
                    text=str(item.get("text") or row["suggested_name"]),
                    bloom=indicator_bloom,
                )
            )
        if not indicators:
            indicators = [IndicatorSpec(text=row["suggested_name"], bloom=bloom_label)]
        tmp_id = f"S{row['id']}"
        candidate = SkillCandidate(
            tmp_id=tmp_id,
            name=row["suggested_name"],
            group=row["group_name"] or "Без группы",
            coverage_area=row["coverage_area"],
            indicators=indicators,
            tools=json.loads(row["tools"] or "[]"),
            evidence_ids=[str(item) for item in json.loads(row["evidence_ids"] or "[]") if item is not None],
            confidence=float(row["confidence"] or 0.0),
            council_agreement=float(row["council_agreement"]) if row["council_agreement"] is not None else None,
            entity_type="skill",
            atomicity="atomic",
            resolution=row["resolution"],
            canonical_skill_id=row["canonical_skill_id"],
            canonical_name=row["canonical_name"],
            canonical_group=None,
            decision="accepted",
        )
        cands.append(candidate)
        tmp_to_db[tmp_id] = int(row["id"])
    return cands, tmp_to_db


def load_brief_spec_for_plan(conn: CatalogConnection, brief_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT raw_text, role, seniority, domain, metadata_json FROM profile_brief WHERE id = ?",
        (brief_id,),
    ).fetchone()
    if not row:
        return {}
    from content_factory.catalog.pipeline import stage_brief_to_catalog
    from content_factory.catalog.pipeline import storage as intake_storage

    metadata = _load_json_object(row["metadata_json"])
    latest_job = conn.execute(
        """
        SELECT result_payload
        FROM intake_job
        WHERE status = 'succeeded'
          AND json_valid(result_payload)
          AND CAST(json_extract(result_payload, '$.brief_id') AS INTEGER) = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (brief_id,),
    ).fetchone()
    latest_payload = _load_json_object(latest_job["result_payload"]) if latest_job else {}
    raw_latest_spec = latest_payload.get("spec")
    latest_spec: dict[str, Any] = raw_latest_spec if isinstance(raw_latest_spec, dict) else {}
    spec = {
        **latest_spec,
        "role": row["role"],
        "seniority": row["seniority"],
        "domain": row["domain"],
        "raw_text": row["raw_text"],
    }
    accepted_design = metadata.get("curriculum_design_spec") if isinstance(metadata, dict) else None
    if isinstance(accepted_design, dict):
        spec["curriculum_design_spec"] = accepted_design
        spec["curriculum_design_approved"] = bool(accepted_design.get("approved"))
    spec.update(
        {
            key: value
            for key, value in stage_brief_to_catalog.extract_workload_from_text(str(row["raw_text"] or "")).items()
            if value is not None
        }
    )
    spec["artifact_templates"] = intake_storage.load_curriculum_artifact_templates(conn)
    return spec


def approve_brief_curriculum_design(conn: CatalogConnection, brief_id: int) -> dict[str, Any]:
    """Persist an immutable journey snapshot outside the standard UP document."""

    from content_factory.catalog.viewer.intake_reviews import count_open_prerequisite_edge_reviews_for_brief

    open_edges = count_open_prerequisite_edge_reviews_for_brief(conn, brief_id)
    if open_edges:
        raise ValueError(f"Resolve prerequisite edge reviews before design approval: {open_edges} open")

    from content_factory.catalog.pipeline import stage_dag_to_up
    from content_factory.catalog.pipeline.curriculum import (
        approve_curriculum_design_spec,
        build_curriculum_design_spec,
    )

    brief = conn.execute("SELECT metadata_json FROM profile_brief WHERE id = ?", (brief_id,)).fetchone()
    if not brief:
        raise ValueError("Brief not found")
    latest_job = conn.execute(
        """
        SELECT result_payload
        FROM intake_job
        WHERE status = 'succeeded'
          AND json_valid(result_payload)
          AND CAST(json_extract(result_payload, '$.brief_id') AS INTEGER) = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (brief_id,),
    ).fetchone()
    latest_payload = _load_json_object(latest_job["result_payload"]) if latest_job else {}
    raw_dag_payload = latest_payload.get("dag")
    dag_payload: dict[str, Any] = raw_dag_payload if isinstance(raw_dag_payload, dict) else {}
    if not dag_payload.get("order"):
        raise ValueError("A valid DAG is required before design approval")

    candidates, _ = load_accepted_skill_candidates(conn, brief_id)
    nodes = stage_dag_to_up.build_plan_nodes(candidates)
    design = build_curriculum_design_spec(load_brief_spec_for_plan(conn, brief_id), nodes, dag_payload)
    if design.uncovered_required_areas:
        missing = ", ".join(design.uncovered_required_areas)
        raise ValueError(f"Required coverage areas are not assigned to accepted skills: {missing}")
    accepted = approve_curriculum_design_spec(design).as_dict()
    metadata = _load_json_object(brief["metadata_json"])
    metadata["curriculum_design_spec"] = accepted
    conn.execute(
        "UPDATE profile_brief SET metadata_json = ? WHERE id = ?",
        (json.dumps(metadata, ensure_ascii=False), brief_id),
    )
    conn.commit()
    return accepted


def load_latest_brief_dag_payload(conn: CatalogConnection, brief_id: int) -> dict[str, Any]:
    """Load the durable DAG snapshot used for an explicit plan rebuild."""

    row = conn.execute(
        """
        SELECT result_payload
        FROM intake_job
        WHERE status = 'succeeded'
          AND json_valid(result_payload)
          AND CAST(json_extract(result_payload, '$.brief_id') AS INTEGER) = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (brief_id,),
    ).fetchone()
    payload = _load_json_object(row["result_payload"]) if row else {}
    raw_dag = payload.get("dag")
    dag = raw_dag if isinstance(raw_dag, dict) else {}
    order = dag.get("order")
    return dag if isinstance(order, list) and order else {}


def _load_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def build_curriculum_plan_for_brief(
    conn: CatalogConnection,
    brief_id: int,
    candidates: list[SkillCandidate] | None = None,
    dag_payload: dict[str, Any] | None = None,
    *,
    allow_open_edge_reviews: bool = False,
) -> dict[str, Any]:
    from content_factory.catalog.pipeline import stage_dag_to_up, storage
    from content_factory.catalog.viewer.intake_reviews import count_open_prerequisite_edge_reviews_for_brief

    open_edges = count_open_prerequisite_edge_reviews_for_brief(conn, brief_id)
    if open_edges and not allow_open_edge_reviews:
        raise ValueError(f"Resolve prerequisite edge reviews before building the plan: {open_edges} open")

    accepted_candidates, _tmp_to_db = load_accepted_skill_candidates(conn, brief_id)
    cands = accepted_candidates if candidates is None else candidates
    persisted_dag_payload = load_latest_brief_dag_payload(conn, brief_id)
    effective_dag_payload = dag_payload or persisted_dag_payload
    if not effective_dag_payload:
        effective_dag_payload = build_deferred_dag_payload(
            get_brief_dag_state(conn, brief_id),
            status="deferred",
            message="DAG не построен",
        )
    spec = load_brief_spec_for_plan(conn, brief_id)
    # Compute the complete replacement before storage mutates the current plan.
    plan_payload = stage_dag_to_up.run(spec, cands, effective_dag_payload)
    save_meta = storage.save_curriculum_plan(conn, brief_id, plan_payload)
    plan_payload["plan_id"] = save_meta["plan_id"]
    plan_payload["row_count"] = save_meta["row_count"]
    template_stats = count_brief_template_proposals(conn, brief_id)
    plan_payload["template_proposal_count"] = template_stats["total"]
    plan_payload["template_proposal_status"] = (
        "open" if template_stats["open"] else ("done" if template_stats["total"] else "none")
    )
    conn.execute(
        "UPDATE curriculum_plan SET payload_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (json.dumps(plan_payload, ensure_ascii=False), int(save_meta["plan_id"])),
    )
    conn.commit()
    update_jobs_curriculum_plan_payload(
        conn,
        brief_id,
        plan_payload,
        persisted_update={
            "curriculum_plan_rows": save_meta["row_count"],
            "template_proposals": template_stats["total"],
        },
    )
    return plan_payload


def build_dag_for_brief(conn: CatalogConnection, brief_id: int) -> dict[str, Any]:
    from content_factory.catalog.pipeline import llm as intake_llm
    from content_factory.catalog.pipeline import stage_catalog_to_dag, storage

    catalog_state = get_brief_catalog_apply_state(conn, brief_id)
    if not bool(catalog_state.get("catalog_applied")):
        clear_brief_dag_artifacts(conn, brief_id, clear_edge_decisions=True)
        clear_brief_curriculum_plan_artifacts(conn, brief_id)
        state = refresh_brief_dag_state(
            conn,
            brief_id,
            status="waiting_catalog",
            message="DAG не построен: сначала примените принятые навыки в справочник и набор навыков.",
        )
        plan_payload = build_deferred_curriculum_plan_payload(
            "УП не построен: сначала примените принятые skills в справочник, затем примите шаблоны и запустите DAG."
        )
        update_jobs_curriculum_plan_payload(
            conn,
            brief_id,
            plan_payload,
            persisted_update={"curriculum_plan_rows": 0},
        )
        return {
            "brief_id": brief_id,
            "state": state,
            "catalog_state": catalog_state,
            "dag": build_deferred_dag_payload(
                state,
                status="waiting_catalog",
                message="DAG не построен: сначала примените принятые навыки в справочник и набор навыков.",
            ),
            "curriculum_plan": plan_payload,
        }

    edge_decisions = load_prerequisite_edge_decisions(conn, brief_id)
    if edge_decisions:
        from content_factory.catalog.viewer.intake_reviews import count_open_prerequisite_edge_reviews_for_brief

        open_edge_reviews = count_open_prerequisite_edge_reviews_for_brief(conn, brief_id)
        if open_edge_reviews:
            raise ValueError(f"Resolve prerequisite edge reviews before rebuilding the DAG: {open_edge_reviews} open")

    clear_brief_dag_artifacts(conn, brief_id)
    cands, tmp_to_db = load_accepted_skill_candidates(conn, brief_id)
    if not cands:
        clear_brief_curriculum_plan_artifacts(conn, brief_id)
        plan_payload = build_deferred_curriculum_plan_payload(
            "Черновик УП пока не строится: ещё нет принятых навыков с валидным DAG."
        )
        save_meta = storage.save_curriculum_plan(conn, brief_id, plan_payload)
        plan_payload["plan_id"] = save_meta["plan_id"]
        plan_payload["row_count"] = save_meta["row_count"]
        state = refresh_brief_dag_state(
            conn,
            brief_id,
            status="deferred",
            message="Граф пока пуст: ещё нет принятых атомарных навыков. Он построится автоматически после первого принятия.",
        )
        update_jobs_curriculum_plan_payload(
            conn,
            brief_id,
            plan_payload,
            persisted_update={"curriculum_plan_rows": 0},
        )
        return {
            "brief_id": brief_id,
            "state": state,
            "dag": build_deferred_dag_payload(
                state,
                status="deferred",
                message="Граф пока пуст: ещё нет принятых атомарных навыков. Он построится автоматически после первого принятия.",
            ),
            "curriculum_plan": plan_payload,
        }

    intake_llm.set_usage_context(stage="dag", brief_id=brief_id)
    try:
        edges, dag, removed_cycle, removed_transitive, dag_payload = stage_catalog_to_dag.run(
            cands,
            edge_decisions=edge_decisions,
        )
    finally:
        intake_llm.set_usage_context(stage=None)
    prereq_count = storage.save_prerequisites(conn, brief_id, dag, cands, tmp_to_db)
    prereq_review_count = storage.save_prerequisite_reviews(conn, brief_id, dag_payload["edge_review_queue"])
    dag_payload["status"] = "built"
    dag_payload["message"] = (
        "Граф построен по текущему набору принятых атомарных навыков и пересчитывается автоматически."
    )
    dag_payload["accepted_atomic_candidates"] = len(cands)
    dag_payload["prerequisite_rows"] = prereq_count
    dag_payload["prerequisite_review_rows"] = prereq_review_count
    plan_payload = build_curriculum_plan_for_brief(
        conn,
        brief_id,
        cands,
        dag_payload,
        allow_open_edge_reviews=True,
    )
    update_jobs_dag_payload(
        conn,
        brief_id,
        dag_payload,
        persisted_update={
            "skill_prerequisite": prereq_count,
            "prerequisite_reviews": prereq_review_count,
            "review_open": int(get_brief_dag_state(conn, brief_id)["open_review_count"]),
        },
    )
    return {
        "brief_id": brief_id,
        "state": get_brief_dag_state(conn, brief_id),
        "dag": dag_payload,
        "curriculum_plan": plan_payload,
        "edges": len(edges),
        "removed_cycle": len(removed_cycle),
        "removed_transitive": len(removed_transitive),
    }


def list_dag_build_options(conn: CatalogConnection) -> list[dict[str, Any]]:
    if not table_exists(conn, "profile_brief") or not table_exists(conn, "skill_suggestion"):
        return []
    rows = conn.execute(
        """
        SELECT pb.id, pb.role, pb.domain
        FROM profile_brief pb
        WHERE EXISTS (SELECT 1 FROM skill_suggestion ss WHERE ss.brief_id = pb.id)
        ORDER BY pb.id DESC
        """
    ).fetchall()
    options = []
    for row in rows:
        state = get_brief_dag_state(conn, int(row["id"]))
        options.append(state)
    return options
