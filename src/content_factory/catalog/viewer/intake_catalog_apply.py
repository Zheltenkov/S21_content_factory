"""Catalog-apply operations for accepted intake candidates.

This module owns the mutation path from reviewed skill candidates into the
canonical catalog/skill set, plus the catalog-state payload written back to
intake jobs. It deliberately depends on the DAG module only for invalidation and
readiness state.
"""

from __future__ import annotations

import json
from typing import Any

from content_factory.catalog.db import CatalogConnection
from content_factory.catalog.viewer._common import fetch_all, table_exists, utc_now_iso
from content_factory.catalog.viewer.curriculum_ops import (
    build_deferred_curriculum_plan_payload,
    update_jobs_curriculum_plan_payload,
)
from content_factory.catalog.viewer.intake_dag import (
    clear_brief_curriculum_plan_artifacts,
    clear_brief_dag_artifacts,
    get_brief_catalog_apply_state,
    refresh_brief_dag_state,
)


def load_brief_catalog_promotion_summary(conn: CatalogConnection, brief_id: int, limit: int = 10) -> dict[str, Any]:
    if not all(table_exists(conn, name) for name in ("skill_promotion_log", "skill_suggestion", "skill")):
        return {"total": 0, "items": []}
    rows = fetch_all(
        conn,
        """
        SELECT
            spl.skill_id,
            spl.suggestion_id,
            spl.status,
            ss.suggested_name,
            ss.resolution,
            s.canonical_name,
            COALESCE(sg.name, ss.group_name, '') AS group_name
        FROM skill_promotion_log spl
        JOIN skill_suggestion ss ON ss.id = spl.suggestion_id
        JOIN skill s ON s.id = spl.skill_id
        LEFT JOIN skill_group sg ON sg.id = s.group_id
        WHERE ss.brief_id = ?
          AND spl.status = 'active'
        ORDER BY spl.id DESC
        LIMIT ?
        """,
        (brief_id, limit),
    )
    total = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM skill_promotion_log spl
            JOIN skill_suggestion ss ON ss.id = spl.suggestion_id
            WHERE ss.brief_id = ?
              AND spl.status = 'active'
            """,
            (brief_id,),
        ).fetchone()[0]
    )
    return {"total": total, "items": rows}


def update_jobs_catalog_payload(
    conn: CatalogConnection,
    brief_id: int,
    *,
    catalog_state: dict[str, Any],
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
        payload["catalog_state"] = catalog_state
        if persisted_update and isinstance(payload.get("persisted"), dict):
            payload["persisted"].update(persisted_update)
        conn.execute(
            "UPDATE intake_job SET result_payload = ?, updated_at = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), utc_now_iso(), row["id"]),
        )
    conn.commit()


def apply_brief_catalog_decisions(conn: CatalogConnection, brief_id: int) -> dict[str, Any]:
    """Apply accepted skill decisions to the canonical catalog as a batch step."""
    from content_factory.catalog.pipeline import llm as intake_llm
    from content_factory.catalog.pipeline import storage as intake_storage

    clear_brief_dag_artifacts(conn, brief_id, clear_edge_decisions=True)
    clear_brief_curriculum_plan_artifacts(conn, brief_id)
    promotion_stats = intake_storage.sync_promotions_for_brief(conn, brief_id)
    skill_set = intake_storage.sync_brief_skill_set(conn, brief_id)
    plan_payload = build_deferred_curriculum_plan_payload(
        "УП ещё не строился: примите нужные шаблоны УП и запустите построение DAG/УП."
    )
    save_meta = intake_storage.save_curriculum_plan(conn, brief_id, plan_payload)
    plan_payload["plan_id"] = save_meta["plan_id"]
    plan_payload["row_count"] = save_meta["row_count"]
    try:
        intake_llm.set_usage_context(brief_id=brief_id, stage="up_template_consilium")
        template_proposals = intake_storage.generate_curriculum_artifact_template_proposals(
            conn,
            brief_id=brief_id,
            plan_id=int(save_meta["plan_id"]),
        )
    finally:
        intake_llm.clear_usage_context()

    catalog_state = get_brief_catalog_apply_state(conn, brief_id)
    catalog_state.update(
        {
            "last_apply_promoted": int(promotion_stats.get("promoted", 0) or 0),
            "last_apply_reverted": int(promotion_stats.get("reverted", 0) or 0),
            "skill_set_status": skill_set.get("status"),
            "skill_set_id": skill_set.get("skill_set_id"),
        }
    )
    update_jobs_catalog_payload(
        conn,
        brief_id,
        catalog_state=catalog_state,
        persisted_update={
            "catalog_promoted": int(catalog_state.get("active_promotions") or 0),
            "catalog_reverted": int(promotion_stats.get("reverted", 0) or 0),
            "template_proposals": len(template_proposals),
            "skill_set_items": int(catalog_state.get("skill_set_items") or 0),
        },
    )
    state = refresh_brief_dag_state(
        conn,
        brief_id,
        status="catalog_applied",
        message="Справочник и набор навыков обновлены. Теперь можно принять шаблоны УП и построить DAG/УП.",
    )
    plan_payload["template_proposal_count"] = len(template_proposals)
    plan_payload["template_proposal_status"] = "open" if template_proposals else "none"
    conn.execute(
        "UPDATE curriculum_plan SET payload_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (json.dumps(plan_payload, ensure_ascii=False), int(save_meta["plan_id"])),
    )
    conn.commit()
    update_jobs_curriculum_plan_payload(
        conn,
        brief_id,
        plan_payload,
        persisted_update={"curriculum_plan_rows": 0},
    )
    return {
        "brief_id": brief_id,
        "catalog_state": catalog_state,
        "dag_state": state,
        "template_proposals": len(template_proposals),
        "promotion_stats": promotion_stats,
        "skill_set": skill_set,
    }


def apply_candidate_decision(
    conn: CatalogConnection,
    suggestion_id: int,
    target_decision: str,
    resolution_note: str | None = None,
) -> int | None:
    from content_factory.catalog.pipeline import storage

    row = conn.execute(
        """
        SELECT id, brief_id
        FROM skill_suggestion
        WHERE id = ?
        """,
        (suggestion_id,),
    ).fetchone()
    if not row:
        return None

    brief_id = int(row["brief_id"])
    review_status_map = {
        "accepted": "resolved",
        "needs_review": "open",
        "rejected": "ignored",
    }
    review_status = review_status_map.get(target_decision, "open")
    now = utc_now_iso()
    reviewed_at = None if review_status == "open" else now
    conn.execute(
        "UPDATE skill_suggestion SET decision = ? WHERE id = ?",
        (target_decision, suggestion_id),
    )
    conn.execute(
        """
        UPDATE review_queue
        SET status = ?,
            resolution_note = COALESCE(?, resolution_note),
            reviewed_at = ?,
            updated_at = ?
        WHERE source_ref = ?
          AND entity_id = ?
        """,
        (review_status, resolution_note, reviewed_at, now, f"brief:{brief_id}", suggestion_id),
    )
    if target_decision != "accepted":
        storage.revert_suggestion_promotion(conn, suggestion_id)
    clear_brief_dag_artifacts(conn, brief_id, clear_edge_decisions=True)
    clear_brief_curriculum_plan_artifacts(conn, brief_id)
    catalog_state = get_brief_catalog_apply_state(conn, brief_id)
    update_jobs_catalog_payload(
        conn,
        brief_id,
        catalog_state=catalog_state,
        persisted_update={
            "catalog_promoted": int(catalog_state.get("active_promotions") or 0),
            "skill_set_items": int(catalog_state.get("skill_set_items") or 0),
            "curriculum_plan_rows": 0,
        },
    )
    update_jobs_curriculum_plan_payload(
        conn,
        brief_id,
        build_deferred_curriculum_plan_payload(
            "УП инвалидирован изменением решения по skill. Примените решения в справочник и заново постройте DAG/УП."
        ),
        persisted_update={"curriculum_plan_rows": 0},
    )
    conn.commit()
    return brief_id
