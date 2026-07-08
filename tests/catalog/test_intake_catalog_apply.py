from __future__ import annotations

import json
from typing import Any

from content_factory.catalog.viewer._common import utc_now_iso
from content_factory.catalog.viewer.intake_catalog_apply import apply_candidate_decision, update_jobs_catalog_payload
from content_factory.catalog.viewer.intake_jobs import create_intake_job, update_intake_job


def _create_brief(conn: Any) -> int:
    cursor = conn.execute(
        "INSERT INTO profile_brief(raw_text, role, seniority, domain) VALUES (?, ?, ?, ?)",
        ("brief text", "Backend Developer", "junior", "web"),
    )
    conn.commit()
    return int(cursor.lastrowid)


def _create_suggestion(conn: Any, brief_id: int, *, decision: str = "needs_review") -> int:
    cursor = conn.execute(
        """
        INSERT INTO skill_suggestion(
            brief_id, suggested_name, bloom, decision, entity_type, atomicity
        )
        VALUES (?, 'FastAPI', 'apply', ?, 'skill', 'atomic')
        """,
        (brief_id, decision),
    )
    conn.commit()
    return int(cursor.lastrowid)


def test_update_jobs_catalog_payload_merges_persisted_state(catalog_conn: Any) -> None:
    brief_id = _create_brief(catalog_conn)
    job_id = create_intake_job(
        catalog_conn,
        source_kind="text",
        source_name=None,
        file_path=None,
        brief_text="brief",
        use_council=False,
    )
    update_intake_job(
        catalog_conn,
        job_id,
        status="succeeded",
        result_payload={"brief_id": brief_id, "persisted": {"review_open": 1}},
    )

    update_jobs_catalog_payload(
        catalog_conn,
        brief_id,
        catalog_state={"catalog_applied": True, "active_promotions": 1},
        persisted_update={"catalog_promoted": 1},
    )

    row = catalog_conn.execute("SELECT result_payload FROM intake_job WHERE id = ?", (job_id,)).fetchone()
    payload = json.loads(row["result_payload"])
    assert payload["catalog_state"] == {"catalog_applied": True, "active_promotions": 1}
    assert payload["persisted"]["review_open"] == 1
    assert payload["persisted"]["catalog_promoted"] == 1


def test_apply_candidate_decision_updates_review_and_invalidates_downstream(catalog_conn: Any) -> None:
    brief_id = _create_brief(catalog_conn)
    suggestion_id = _create_suggestion(catalog_conn, brief_id)
    job_id = create_intake_job(
        catalog_conn,
        source_kind="text",
        source_name=None,
        file_path=None,
        brief_text="brief",
        use_council=False,
    )
    update_intake_job(
        catalog_conn,
        job_id,
        status="succeeded",
        result_payload={"brief_id": brief_id, "persisted": {"curriculum_plan_rows": 3}},
    )
    catalog_conn.execute(
        """
        INSERT INTO review_queue(id, entity_type, entity_id, source_ref, reason_code, severity, details, status, created_at)
        VALUES (?, 'skill', ?, ?, 'ambiguous_skill_name', 'warning', 'manual check', 'open', ?)
        """,
        (1, suggestion_id, f"brief:{brief_id}", utc_now_iso()),
    )
    catalog_conn.execute(
        """
        INSERT INTO skill_prerequisite(brief_id, src_name, dst_name, relation_type, review_state)
        VALUES (?, 'FastAPI', 'Testing', 'hard', 'accepted')
        """,
        (brief_id,),
    )
    catalog_conn.commit()

    changed_brief_id = apply_candidate_decision(catalog_conn, suggestion_id, "accepted", "accepted in test")

    assert changed_brief_id == brief_id
    suggestion = catalog_conn.execute("SELECT decision FROM skill_suggestion WHERE id = ?", (suggestion_id,)).fetchone()
    review = catalog_conn.execute("SELECT status, resolution_note FROM review_queue WHERE id = 1").fetchone()
    assert suggestion["decision"] == "accepted"
    assert review["status"] == "resolved"
    assert review["resolution_note"] == "accepted in test"
    assert catalog_conn.execute("SELECT COUNT(*) FROM skill_prerequisite WHERE brief_id = ?", (brief_id,)).fetchone()[0] == 0
    row = catalog_conn.execute("SELECT result_payload FROM intake_job WHERE id = ?", (job_id,)).fetchone()
    payload = json.loads(row["result_payload"])
    assert payload["persisted"]["curriculum_plan_rows"] == 0
