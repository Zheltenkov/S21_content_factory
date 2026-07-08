"""Cleanup helpers for transient intake workspace artifacts."""

from __future__ import annotations

from datetime import UTC, datetime

from content_factory.catalog.db import CatalogConnection
from content_factory.catalog.viewer._common import column_exists, table_columns, table_exists


def prune_empty_generated_catalog_nodes(conn: CatalogConnection) -> dict[str, int]:
    """Remove empty generated taxonomy nodes; keep manual empty nodes editable."""
    stats: dict[str, int] = {}
    if table_exists(conn, "skill_set") and table_exists(conn, "skill_set_item"):
        stats["skill_set_orphan_items"] = conn.execute(
            """
            DELETE FROM skill_set_item
            WHERE skill_set_id NOT IN (SELECT id FROM skill_set)
               OR skill_id NOT IN (SELECT id FROM skill)
            """
        ).rowcount
        stats["skill_set_empty_archived"] = conn.execute(
            """
            UPDATE skill_set
            SET status = 'archived',
                updated_at = ?
            WHERE status != 'archived'
              AND NOT EXISTS (
                  SELECT 1
                  FROM skill_set_item ssi
                  WHERE ssi.skill_set_id = skill_set.id
              )
            """,
            (datetime.now(UTC).isoformat(),),
        ).rowcount

    if table_exists(conn, "skill_group") and table_exists(conn, "skill"):
        stats["skill_group_empty_generated_archived"] = conn.execute(
            """
            UPDATE skill_group
            SET status = 'deprecated',
                updated_at = ?
            WHERE COALESCE(source, '') IN ('derived', 'live_snapshot', 'intake_accept')
              AND status != 'deprecated'
              AND NOT EXISTS (
                  SELECT 1
                  FROM skill s_active
                  WHERE s_active.group_id = skill_group.id
                    AND COALESCE(s_active.is_active, 1) = 1
              )
              AND EXISTS (
                  SELECT 1
                  FROM skill s_any
                  WHERE s_any.group_id = skill_group.id
              )
            """,
            (datetime.now(UTC).isoformat(),),
        ).rowcount
        stats["skill_group_empty_generated_deleted"] = conn.execute(
            """
            DELETE FROM skill_group
            WHERE COALESCE(source, '') IN ('derived', 'live_snapshot', 'intake_accept')
              AND NOT EXISTS (
                  SELECT 1
                  FROM skill s
                  WHERE s.group_id = skill_group.id
              )
            """
        ).rowcount

    if table_exists(conn, "competency") and table_exists(conn, "profile_competency") and table_exists(conn, "competency_skill"):
        stats["profile_competency_empty_deleted"] = conn.execute(
            """
            DELETE FROM profile_competency
            WHERE NOT EXISTS (
                SELECT 1
                FROM competency_skill cs
                WHERE cs.profile_competency_id = profile_competency.id
            )
            AND (
                title_in_source IS NULL
                OR title_in_source = ''
                OR title_in_source = (
                    SELECT title FROM competency c WHERE c.id = profile_competency.competency_id
                )
            )
            """
        ).rowcount
    conn.commit()
    return stats


def clear_intake_workspace(conn: CatalogConnection) -> dict[str, int]:
    """Clear transient intake artifacts while keeping canonical catalog tables intact."""
    from content_factory.catalog.pipeline import competency_catalog
    from content_factory.catalog.pipeline import storage as intake_storage

    stats: dict[str, int] = {}
    intake_competency_ids: set[int] = set()

    if table_exists(conn, "review_queue"):
        intake_competency_ids.update(
            int(row["entity_id"])
            for row in conn.execute(
                """
                SELECT entity_id
                FROM review_queue
                WHERE entity_type = 'competency'
                  AND entity_id IS NOT NULL
                  AND source_ref LIKE 'intake_accept:%'
                """
            ).fetchall()
        )

    if table_exists(conn, "profile_competency") and table_exists(conn, "profile"):
        intake_competency_ids.update(
            int(row["competency_id"])
            for row in conn.execute(
                """
                SELECT DISTINCT pc.competency_id
                FROM profile_competency pc
                JOIN profile p ON p.id = pc.profile_id
                WHERE p.slug = ?
                """,
                (competency_catalog.SERVICE_PROFILE_SLUG,),
            ).fetchall()
            if row["competency_id"] is not None
        )

    if table_exists(conn, "skill_promotion_log"):
        active_promotions = conn.execute(
            """
            SELECT suggestion_id
            FROM skill_promotion_log
            WHERE status = 'active'
            ORDER BY id
            """
        ).fetchall()
        reverted = 0
        for row in active_promotions:
            result = intake_storage.revert_suggestion_promotion(conn, int(row["suggestion_id"]))
            if result.get("status") == "reverted":
                reverted += 1
        stats["skill_promotions_reverted"] = reverted

    if table_exists(conn, "indicator"):
        indicator_cols = table_columns(conn, "indicator")
        clauses = []
        params: list[object] = []
        if "source_scale_title" in indicator_cols:
            clauses.append("source_scale_title = 'intake-live'")
        if "source_profile_name" in indicator_cols:
            clauses.append("source_profile_name = ?")
            params.append(competency_catalog.SERVICE_PROFILE_NAME)
        if clauses:
            stats["indicator_intake"] = conn.execute(
                f"DELETE FROM indicator WHERE {' OR '.join(clauses)}",
                tuple(params),
            ).rowcount

    if table_exists(conn, "indicator_level_cell") and table_exists(conn, "indicator_row"):
        stats["indicator_level_cell_intake"] = conn.execute(
            """
            DELETE FROM indicator_level_cell
            WHERE indicator_row_id IN (
                SELECT id FROM indicator_row WHERE COALESCE(notes, '') LIKE 'intake_accept:%'
            )
            """
        ).rowcount

    if table_exists(conn, "indicator_row"):
        stats["indicator_row_intake"] = conn.execute(
            "DELETE FROM indicator_row WHERE COALESCE(notes, '') LIKE 'intake_accept:%'"
        ).rowcount

    if table_exists(conn, "competency_skill") and table_exists(conn, "profile_competency") and table_exists(conn, "profile"):
        stats["competency_skill_intake"] = conn.execute(
            """
            DELETE FROM competency_skill
            WHERE id IN (
                SELECT cs.id
                FROM competency_skill cs
                JOIN profile_competency pc ON pc.id = cs.profile_competency_id
                JOIN profile p ON p.id = pc.profile_id
                WHERE p.slug = ?
            )
            """,
            (competency_catalog.SERVICE_PROFILE_SLUG,),
        ).rowcount

    if table_exists(conn, "profile_competency") and table_exists(conn, "profile"):
        stats["profile_competency_intake_orphan"] = conn.execute(
            """
            DELETE FROM profile_competency
            WHERE id IN (
                SELECT pc.id
                FROM profile_competency pc
                JOIN profile p ON p.id = pc.profile_id
                WHERE p.slug = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM competency_skill cs WHERE cs.profile_competency_id = pc.id
                  )
            )
            """,
            (competency_catalog.SERVICE_PROFILE_SLUG,),
        ).rowcount

    if table_exists(conn, "profile"):
        stats["profile_intake_empty"] = conn.execute(
            """
            DELETE FROM profile
            WHERE slug = ?
              AND NOT EXISTS (
                  SELECT 1 FROM profile_competency pc WHERE pc.profile_id = profile.id
              )
            """,
            (competency_catalog.SERVICE_PROFILE_SLUG,),
        ).rowcount

    if intake_competency_ids and table_exists(conn, "competency") and table_exists(conn, "profile_competency"):
        ordered_intake_competency_ids = sorted(intake_competency_ids)
        placeholders = ", ".join("?" for _ in ordered_intake_competency_ids)
        stats["competency_intake_candidate_orphan"] = conn.execute(
            f"""
            DELETE FROM competency
            WHERE id IN ({placeholders})
              AND NOT EXISTS (
                  SELECT 1 FROM profile_competency pc WHERE pc.competency_id = competency.id
              )
            """,
            tuple(ordered_intake_competency_ids),
        ).rowcount

    if table_exists(conn, "review_queue"):
        stats["review_queue"] = conn.execute(
            """
            DELETE FROM review_queue
            WHERE source_ref LIKE 'brief:%'
               OR source_ref LIKE 'intake_accept:%'
            """
        ).rowcount

    if table_exists(conn, "skill_prerequisite") and column_exists(conn, "skill_prerequisite", "brief_id"):
        stats["skill_prerequisite"] = conn.execute("DELETE FROM skill_prerequisite WHERE brief_id IS NOT NULL").rowcount

    if table_exists(conn, "prerequisite_edge_decision") and column_exists(conn, "prerequisite_edge_decision", "brief_id"):
        stats["prerequisite_edge_decision"] = conn.execute(
            "DELETE FROM prerequisite_edge_decision WHERE brief_id IS NOT NULL"
        ).rowcount

    if table_exists(conn, "skill_set") and table_exists(conn, "skill_set_item"):
        runtime_skill_sets = conn.execute(
            """
            SELECT id
            FROM skill_set
            WHERE source_type IN ('brief', 'curriculum_plan')
               OR source_ref LIKE 'brief:%'
               OR source_ref LIKE '%;brief:%'
            """
        ).fetchall()
        runtime_skill_set_ids = [int(row["id"]) for row in runtime_skill_sets]
        if runtime_skill_set_ids:
            placeholders = ", ".join("?" for _ in runtime_skill_set_ids)
            stats["skill_set_item_runtime"] = conn.execute(
                f"DELETE FROM skill_set_item WHERE skill_set_id IN ({placeholders})",
                tuple(runtime_skill_set_ids),
            ).rowcount
            stats["skill_set_runtime"] = conn.execute(
                f"DELETE FROM skill_set WHERE id IN ({placeholders})",
                tuple(runtime_skill_set_ids),
            ).rowcount

    if table_exists(conn, "curriculum_plan_row") and table_exists(conn, "curriculum_plan"):
        stats["curriculum_plan_row"] = conn.execute(
            """
            DELETE FROM curriculum_plan_row
            WHERE plan_id IN (SELECT id FROM curriculum_plan WHERE brief_id IS NOT NULL)
            """
        ).rowcount

    if table_exists(conn, "curriculum_plan"):
        stats["curriculum_plan"] = conn.execute("DELETE FROM curriculum_plan WHERE brief_id IS NOT NULL").rowcount

    if table_exists(conn, "curriculum_artifact_template_proposal"):
        stats["curriculum_artifact_template_proposal"] = conn.execute(
            "DELETE FROM curriculum_artifact_template_proposal WHERE brief_id IS NOT NULL"
        ).rowcount

    if table_exists(conn, "skill_suggestion"):
        stats["skill_suggestion"] = conn.execute("DELETE FROM skill_suggestion WHERE brief_id IS NOT NULL").rowcount

    if table_exists(conn, "skill_promotion_log"):
        stats["skill_promotion_log"] = conn.execute("DELETE FROM skill_promotion_log").rowcount

    if table_exists(conn, "evidence_source"):
        stats["evidence_source"] = conn.execute("DELETE FROM evidence_source WHERE brief_id IS NOT NULL").rowcount

    if table_exists(conn, "evidence_query_cache"):
        stats["evidence_query_cache"] = conn.execute("DELETE FROM evidence_query_cache").rowcount

    if table_exists(conn, "intake_job"):
        stats["intake_job"] = conn.execute("DELETE FROM intake_job").rowcount

    if table_exists(conn, "profile_brief"):
        stats["profile_brief"] = conn.execute("DELETE FROM profile_brief").rowcount

    prune_stats = prune_empty_generated_catalog_nodes(conn)
    stats.update({f"prune_{key}": value for key, value in prune_stats.items()})
    conn.commit()
    return {key: int(value or 0) for key, value in stats.items()}
