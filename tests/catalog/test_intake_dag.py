from __future__ import annotations

import json
from typing import Any

from content_factory.catalog.viewer._common import utc_now_iso
from content_factory.catalog.viewer.intake_dag import (
    approve_brief_curriculum_design,
    build_deferred_dag_payload,
    clear_brief_dag_artifacts,
    get_brief_dag_state,
    load_latest_brief_dag_payload,
    update_jobs_dag_payload,
)
from content_factory.catalog.viewer.intake_jobs import create_intake_job, update_intake_job


def _create_brief(conn: Any) -> int:
    cursor = conn.execute(
        "INSERT INTO profile_brief(raw_text, role, seniority, domain) VALUES (?, ?, ?, ?)",
        ("brief text", "Data Engineer", "junior", "analytics"),
    )
    conn.commit()
    return int(cursor.lastrowid)


def test_update_jobs_dag_payload_updates_succeeded_job_result(catalog_conn: Any) -> None:
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
    dag_payload = build_deferred_dag_payload(
        {"accepted_atomic_count": 1, "pending_atomic_count": 0, "open_review_count": 0},
        status="stale",
        message="DAG stale",
    )

    update_jobs_dag_payload(catalog_conn, brief_id, dag_payload, {"skill_prerequisite": 2})

    row = catalog_conn.execute("SELECT result_payload FROM intake_job WHERE id = ?", (job_id,)).fetchone()
    payload = json.loads(row["result_payload"])
    assert payload["dag"] == dag_payload
    assert payload["persisted"]["review_open"] == 1
    assert payload["persisted"]["skill_prerequisite"] == 2


def test_load_latest_brief_dag_payload_reuses_built_order(catalog_conn: Any) -> None:
    brief_id = _create_brief(catalog_conn)
    job_id = create_intake_job(
        catalog_conn,
        source_kind="text",
        source_name=None,
        file_path=None,
        brief_text="brief",
        use_council=False,
    )
    dag = {"status": "built", "order": [{"id": "S1"}], "final_edges": []}
    update_intake_job(
        catalog_conn,
        job_id,
        status="succeeded",
        result_payload={"brief_id": brief_id, "dag": dag},
    )

    assert load_latest_brief_dag_payload(catalog_conn, brief_id) == dag


def test_clear_brief_dag_artifacts_removes_prerequisites_and_edge_reviews(catalog_conn: Any) -> None:
    brief_id = _create_brief(catalog_conn)
    catalog_conn.execute(
        """
        INSERT INTO skill_prerequisite(brief_id, src_name, dst_name, relation_type, review_state)
        VALUES (?, 'SQL', 'Indexes', 'soft', 'needs_review')
        """,
        (brief_id,),
    )
    catalog_conn.execute(
        """
        INSERT INTO review_queue(id, entity_type, source_ref, reason_code, severity, details, status, created_at)
        VALUES (?, 'prerequisite_edge', ?, 'ai_proposed', 'info', ?, 'open', ?)
        """,
        (
            1,
            f"brief:{brief_id}",
            json.dumps({"review_kind": "prerequisite_edge", "edge_key": "S1->S2"}, ensure_ascii=False),
            utc_now_iso(),
        ),
    )
    catalog_conn.execute(
        """
        INSERT INTO review_queue(id, entity_type, source_ref, reason_code, severity, details, status, created_at)
        VALUES (?, 'skill', ?, 'ambiguous_skill_name', 'warning', 'manual check', 'open', ?)
        """,
        (2, f"brief:{brief_id}", utc_now_iso()),
    )
    catalog_conn.commit()

    clear_brief_dag_artifacts(catalog_conn, brief_id)

    assert (
        catalog_conn.execute("SELECT COUNT(*) FROM skill_prerequisite WHERE brief_id = ?", (brief_id,)).fetchone()[0]
        == 0
    )
    assert catalog_conn.execute("SELECT COUNT(*) FROM review_queue WHERE id = 1").fetchone()[0] == 0
    assert catalog_conn.execute("SELECT COUNT(*) FROM review_queue WHERE id = 2").fetchone()[0] == 1


def test_get_brief_dag_state_counts_current_readiness(catalog_conn: Any) -> None:
    brief_id = _create_brief(catalog_conn)
    catalog_conn.execute(
        """
        INSERT INTO skill_suggestion(
            brief_id, suggested_name, bloom, decision, entity_type, atomicity
        )
        VALUES (?, 'SQL', 'apply', 'accepted', 'skill', 'atomic')
        """,
        (brief_id,),
    )
    catalog_conn.execute(
        """
        INSERT INTO skill_suggestion(
            brief_id, suggested_name, bloom, decision, entity_type, atomicity
        )
        VALUES (?, 'Indexes', 'apply', 'needs_review', 'skill', 'atomic')
        """,
        (brief_id,),
    )
    catalog_conn.execute(
        """
        INSERT INTO skill_prerequisite(brief_id, src_name, dst_name, relation_type, review_state)
        VALUES (?, 'SQL', 'Indexes', 'hard', 'accepted')
        """,
        (brief_id,),
    )
    catalog_conn.execute(
        """
        INSERT INTO review_queue(id, entity_type, source_ref, reason_code, severity, details, status, created_at)
        VALUES (?, 'skill', ?, 'ambiguous_skill_name', 'warning', 'manual check', 'open', ?)
        """,
        (1, f"brief:{brief_id}", utc_now_iso()),
    )
    catalog_conn.commit()

    state = get_brief_dag_state(catalog_conn, brief_id)

    assert state["accepted_atomic_count"] == 1
    assert state["pending_atomic_count"] == 1
    assert state["open_review_count"] == 1
    assert state["prerequisite_count"] == 1
    assert state["role"] == "Data Engineer"


def test_approve_brief_curriculum_design_persists_separate_metadata(catalog_conn: Any) -> None:
    brief_id = _create_brief(catalog_conn)
    suggestion_id = int(
        catalog_conn.execute(
            """
            INSERT INTO skill_suggestion(
                brief_id, suggested_name, group_name, coverage_area, bloom, indicators_json,
                tools, evidence_ids, resolution, confidence, decision, entity_type, atomicity
            )
            VALUES (?, 'Собрать данные', 'Аналитика', 'Сбор данных', 'apply', '[]', '[]', '[]',
                    'new', 0.9, 'accepted', 'skill', 'atomic')
            """,
            (brief_id,),
        ).lastrowid
        or 0
    )
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
        result_payload={
            "brief_id": brief_id,
            "spec": {
                "program_goal": "Освоить сбор данных",
                "must_include_areas": ["Сбор данных"],
            },
            "dag": {
                "order": [{"id": f"S{suggestion_id}"}],
                "final_edges": [],
            },
        },
    )

    design = approve_brief_curriculum_design(catalog_conn, brief_id)

    assert design["approved"] is True
    assert design["ready"] is True
    row = catalog_conn.execute("SELECT metadata_json FROM profile_brief WHERE id = ?", (brief_id,)).fetchone()
    metadata = json.loads(row["metadata_json"])
    assert metadata["curriculum_design_spec"]["stages"][0]["coverage_areas"] == ["Сбор данных"]
