"""Brief-scoped persistence: briefs, evidence, skill suggestions, prerequisite
edges/reviews, and curriculum-plan snapshots.

The write half of the intake pipeline (docstring: "пишет результаты"). Extracted
from ``catalog/pipeline/storage.py``; ``storage`` re-exports the public functions
so the ``storage.foo`` / ``intake_storage.foo`` call sites stay unchanged.
"""

from __future__ import annotations

import json
from typing import Any

from content_factory.catalog.db import CatalogConnection
from content_factory.catalog.pipeline._storage_common import (
    _as_dict,
    _review_queue_entity_type,
    _supports_superseded,
    _table_exists,
)
from content_factory.catalog.pipeline.skill_promotion import sync_curriculum_plan_skill_set

from .models import Evidence, SkillCandidate


def save_brief(con: CatalogConnection, raw: str, spec: dict) -> int:
    cur = con.execute(
        "INSERT INTO profile_brief(raw_text, role, seniority, domain) VALUES (?,?,?,?)",
        (raw, spec.get("role"), spec.get("seniority"), spec.get("domain")))
    con.commit()
    return int(cur.lastrowid or 0)


def save_evidence(con: CatalogConnection, brief_id: int, evidence: list[Evidence]) -> dict[str, int]:
    idmap: dict[str, int] = {}
    for e in evidence:
        cur = con.execute(
            "INSERT INTO evidence_source(brief_id, claim, source_type, url, snippet, retrieved_at) VALUES (?,?,?,?,?,?)",
            (brief_id, e.claim, e.source_type, e.url, e.snippet, e.retrieved_at))
        idmap[e.id] = int(cur.lastrowid or 0)
    con.commit()
    return idmap


def save_suggestions(con: CatalogConnection, brief_id: int, cands: list[SkillCandidate], ev_idmap: dict[str, int]) -> dict[str, int]:
    tmp_to_db: dict[str, int] = {}
    allow_superseded = _supports_superseded(con)
    ordered = sorted(cands, key=lambda candidate: 0 if candidate.parent_tmp_id is None else 1)
    for c in ordered:
        parent_db_id = tmp_to_db.get(c.parent_tmp_id) if c.parent_tmp_id else None
        stored_decision = c.decision
        if (
            stored_decision == "needs_review"
            and c.atomicity == "composite"
            and "composite_decomposed" in (c.reasons or [])
        ):
            stored_decision = "superseded"
        if stored_decision == "superseded" and not allow_superseded:
            stored_decision = "rejected"
        suggestion_cursor = con.execute(
            """INSERT INTO skill_suggestion(brief_id, suggested_name, source_name, group_name, coverage_area, bloom,
               indicators_json, tools, resolution, canonical_skill_id, match_score,
               nearest_skill_id, nearest_name, nearest_group, confidence, council_agreement,
               evidence_ids, decision, entity_type, atomicity, parent_suggestion_id, atomize_rationale)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                brief_id,
                c.name,
                c.source_name,
                c.group,
                c.coverage_area,
                max((i.bloom for i in c.indicators), default=None),
                json.dumps([indicator.model_dump(mode="json") for indicator in c.indicators], ensure_ascii=False),
                json.dumps(c.tools, ensure_ascii=False),
                c.resolution,
                c.canonical_skill_id,
                c.match_score,
                c.nearest_skill_id,
                c.nearest_name,
                c.nearest_group,
                c.confidence,
                c.council_agreement,
                json.dumps([ev_idmap.get(x) for x in c.evidence_ids]),
                stored_decision or "pending",
                c.entity_type,
                c.atomicity,
                parent_db_id,
                c.atomize_rationale,
            ),
        )
        tmp_to_db[c.tmp_id] = int(suggestion_cursor.lastrowid or 0)
        # спорное -> в существующую review_queue (переиспользуем механизм каталога)
        if stored_decision == "needs_review":
            rq_entity_type = _review_queue_entity_type(c)
            primary_reason = c.reasons[0] if c.reasons else "needs_review"
            severity = "warning" if primary_reason in {"novel_skill", "council_split", "fuzzy_match_ambiguous", "low_confidence"} else "info"
            reasons_text = ", ".join(c.reasons) if c.reasons else "manual_review"
            details = (
                f"Интейк по брифу #{brief_id}: {c.entity_type} «{c.name}». "
                f"Атомарность: {c.atomicity}. "
                f"Резолв против каталога: {c.resolution or 'unknown'}, "
                f"уверенность {c.confidence:.2f}. "
                f"Причины проверки: {reasons_text}."
            )
            con.execute(
                """INSERT INTO review_queue(entity_type, entity_id, source_ref, reason_code, severity, details, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'open')""",
                (rq_entity_type, tmp_to_db[c.tmp_id], f"brief:{brief_id}", primary_reason, severity, details),
            )
    con.commit()
    return tmp_to_db


def save_prerequisites(
    con: CatalogConnection,
    brief_id: int,
    DAG: Any,
    cands: list[SkillCandidate],
    tmp_to_db: dict[str, int] | None = None,
) -> int:
    by_tid = {c.tmp_id: c for c in cands}
    n = 0
    for u, v in DAG.edges():
        cu, cv = by_tid[u], by_tid[v]
        edge = DAG[u][v].get("edge")
        con.execute(
            """INSERT INTO skill_prerequisite(brief_id, src_skill_id, dst_skill_id, src_suggestion_id, dst_suggestion_id,
               src_name, dst_name, relation_type, confidence, source, review_state)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                brief_id,
                cu.canonical_skill_id,
                cv.canonical_skill_id,
                tmp_to_db.get(u) if tmp_to_db else None,
                tmp_to_db.get(v) if tmp_to_db else None,
                cu.name,
                cv.name,
                edge.relation_type if edge else "hard",
                DAG[u][v].get("conf"),
                edge.source if edge else "pipeline",
                "accepted" if (edge is None or edge.decision == "accept") else "needs_review",
            ),
        )
        n += 1
    con.commit()
    return n


def save_prerequisite_reviews(con: CatalogConnection, brief_id: int, edge_reviews: list[dict[str, Any]]) -> int:
    count = 0
    decided_edge_keys: set[str] = set()
    if _table_exists(con, "prerequisite_edge_decision"):
        decided_edge_keys = {
            str(row["edge_key"])
            for row in con.execute(
                "SELECT edge_key FROM prerequisite_edge_decision WHERE brief_id = ?",
                (brief_id,),
            )
        }
    for item in edge_reviews:
        edge_key = str(item.get("edge_key") or "")
        if edge_key and edge_key in decided_edge_keys:
            continue
        con.execute(
            """
            INSERT INTO review_queue(entity_type, entity_id, source_ref, reason_code, severity, details, status)
            VALUES ('prerequisite_edge', NULL, ?, ?, ?, ?, 'open')
            """,
            (
                f"brief:{brief_id}",
                str(item.get("reason_code", "needs_review")),
                str(item.get("severity", "info")),
                json.dumps(
                    {
                        "review_kind": "prerequisite_edge",
                        "edge_key": edge_key,
                        "src_id": item.get("src_id"),
                        "dst_id": item.get("dst_id"),
                        "edge_label": item.get("edge_label"),
                        "confidence": item.get("confidence"),
                        "source": item.get("source"),
                        "relation_type": item.get("relation_type"),
                        "reasons": item.get("reasons") or [],
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        count += 1
    con.commit()
    return count


def clear_curriculum_plan(con: CatalogConnection, brief_id: int, source_policy: str = "accepted_only") -> None:
    plan_rows = con.execute(
        "SELECT id FROM curriculum_plan WHERE brief_id = ? AND source_policy = ?",
        (brief_id, source_policy),
    ).fetchall()
    for row in plan_rows:
        if _table_exists(con, "skill_set") and _table_exists(con, "skill_set_item"):
            skill_set_rows = con.execute(
                "SELECT id FROM skill_set WHERE source_type = 'curriculum_plan' AND source_id = ?",
                (row["id"],),
            ).fetchall()
            for skill_set_row in skill_set_rows:
                con.execute("DELETE FROM skill_set_item WHERE skill_set_id = ?", (skill_set_row["id"],))
            con.execute("DELETE FROM skill_set WHERE source_type = 'curriculum_plan' AND source_id = ?", (row["id"],))
        con.execute("DELETE FROM curriculum_plan_row WHERE plan_id = ?", (row["id"],))
    con.execute(
        "DELETE FROM curriculum_plan WHERE brief_id = ? AND source_policy = ?",
        (brief_id, source_policy),
    )
    con.commit()


def save_curriculum_plan(
    con: CatalogConnection,
    brief_id: int,
    plan_payload: dict[str, Any],
    source_policy: str = "accepted_only",
) -> dict[str, int]:
    clear_curriculum_plan(con, brief_id, source_policy)
    summary = _as_dict(plan_payload.get("summary"))
    cur = con.execute(
        """
        INSERT INTO curriculum_plan(
            brief_id, source_policy, status, title, audience_level,
            total_blocks, total_projects, total_hours, total_days, total_xp, payload_json, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            brief_id,
            source_policy,
            str(plan_payload.get("status", "draft")),
            plan_payload.get("title"),
            plan_payload.get("audience_level"),
            int(summary.get("blocks", 0) or 0),
            int(summary.get("projects", 0) or 0),
            float(summary.get("total_hours", 0) or 0),
            float(summary.get("total_days", 0) or 0),
            int(summary.get("total_xp", 0) or 0),
            json.dumps(plan_payload, ensure_ascii=False),
        ),
    )
    plan_id = int(cur.lastrowid or 0)
    row_count = 0
    for row in plan_payload.get("rows", []):
        if not isinstance(row, dict):
            continue
        effort_days = None if row.get("effort_days") in (None, "") else float(row.get("effort_days", 0) or 0)
        cumulative_days = None if row.get("cumulative_days") in (None, "") else float(row.get("cumulative_days", 0) or 0)
        xp = None if row.get("xp") in (None, "") else int(row.get("xp", 0) or 0)
        con.execute(
            """
            INSERT INTO curriculum_plan_row(
                plan_id, block_index, row_number, project_index_in_block, block_title, block_goal,
                project_name, project_summary, outcomes_know, outcomes_can, outcomes_skills,
                learning_outcomes, skills_list, audience_level, required_tools, materials,
                validation_criteria, storytelling, delivery_format, group_size, effort_hours, effort_days,
                cumulative_days, xp, completion_percent, p2p_checks, weighted_skills,
                platform_project_name, artifact_links
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan_id,
                int(row.get("block_index", 0) or 0),
                int(row.get("row_number", 0) or 0),
                int(row.get("project_index_in_block", 0) or 0),
                row.get("block_title"),
                row.get("block_goal"),
                row.get("project_name"),
                row.get("project_summary"),
                row.get("outcomes_know"),
                row.get("outcomes_can"),
                row.get("outcomes_skills"),
                row.get("learning_outcomes"),
                row.get("skills_list"),
                row.get("audience_level"),
                row.get("required_tools"),
                row.get("materials"),
                row.get("validation_criteria"),
                row.get("storytelling"),
                row.get("delivery_format"),
                row.get("group_size"),
                float(row.get("effort_hours", 0) or 0),
                effort_days,
                cumulative_days,
                xp,
                None if row.get("completion_percent") in (None, "") else float(row.get("completion_percent", 0) or 0),
                None if row.get("p2p_checks") in (None, "") else int(row.get("p2p_checks", 0) or 0),
                row.get("weighted_skills"),
                row.get("platform_project_name"),
                row.get("artifact_links"),
            ),
        )
        row_count += 1
    skill_set = sync_curriculum_plan_skill_set(
        con,
        brief_id=brief_id,
        plan_id=plan_id,
        plan_payload=plan_payload,
    )
    con.commit()
    return {"plan_id": plan_id, "row_count": row_count, "skill_set_id": int(skill_set.get("skill_set_id") or 0)}
