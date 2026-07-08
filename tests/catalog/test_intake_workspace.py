from __future__ import annotations

from typing import Any

from content_factory.catalog.viewer.catalog_admin_ops import (
    create_catalog_group,
    create_catalog_indicator,
    create_catalog_skill,
)
from content_factory.catalog.viewer.intake_workspace import (
    build_candidate_recommended_action,
    build_intake_workflow_steps,
    hydrate_job_result_payload,
)


def test_candidate_recommended_action_prioritizes_explicit_reviews() -> None:
    assert (
        build_candidate_recommended_action(
            100.0,
            "matched",
            True,
            "Ценностное предложение",
            "Подозрительный match с каталогом: нужно проверить смысл и группу canonical skill",
        )["code"]
        == "check"
    )
    assert build_candidate_recommended_action(82.0, "new", True, "Проведение интервью")["code"] == "link"
    assert build_candidate_recommended_action(12.0, "new", False, None)["code"] == "create"
    assert build_candidate_recommended_action(91.0, "fuzzy", True, "Проведение интервью")["code"] == "link"
    assert build_candidate_recommended_action(99.0, "new", True, "Accepted", decision="accepted")["code"] == "done"
    assert build_candidate_recommended_action(99.0, "new", True, "Rejected", decision="rejected")["code"] == "rejected"


def test_workflow_steps_use_persisted_review_count() -> None:
    job = {"id": 7, "status": "succeeded"}
    result = {
        "candidates": [{"decision": "needs_review"}],
        "persisted": {"review_open": "0", "catalog_promoted": 1, "template_proposals": 1},
        "catalog_state": {"catalog_applied": True},
        "curriculum_plan": {"plan_id": 42, "row_count": 2},
    }

    steps = build_intake_workflow_steps(job, result, dag_build_state={"open_review_count": 0})

    by_key = {step["key"]: step for step in steps}
    assert by_key["review"]["status"] == "done"
    assert by_key["review"]["href"] == "/intake/jobs/7"
    assert by_key["templates"]["status"] == "done"
    assert by_key["up"]["status"] == "done"
    assert by_key["up"]["href"] == "/up/plans/42"


def test_hydrate_job_result_payload_attaches_catalog_preview(catalog_conn: Any) -> None:
    group_id = create_catalog_group(catalog_conn, "Research", 1, "active")
    skill_id = create_catalog_skill(
        catalog_conn,
        group_id,
        "Проведение интервью",
        1,
        "Интервью с пользователями",
        "",
        "matched",
        "",
        1,
    )
    create_catalog_indicator(
        catalog_conn,
        skill_id,
        "can",
        "Проводит пользовательское интервью по сценарию",
        1,
        "junior",
        1,
    )
    brief_id = int(
        catalog_conn.execute(
            "INSERT INTO profile_brief(raw_text, role, seniority, domain) VALUES (?, ?, ?, ?)",
            ("brief", "Product manager", "junior", "product"),
        ).lastrowid
        or 0
    )
    suggestion_id = int(
        catalog_conn.execute(
            """
            INSERT INTO skill_suggestion(
                brief_id, suggested_name, source_name, group_name, bloom, resolution,
                nearest_skill_id, nearest_name, nearest_group, match_score, decision,
                entity_type, atomicity
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                brief_id,
                "Проведение интервью",
                "Провести интервью",
                "Research",
                "apply",
                "matched",
                skill_id,
                "Проведение интервью",
                "Research",
                99.0,
                "needs_review",
                "skill",
                "atomic",
            ),
        ).lastrowid
        or 0
    )
    catalog_conn.commit()

    result = {
        "brief_id": brief_id,
        "candidates": [
            {
                "name": "Проведение интервью",
                "group": "Research",
                "entity_type": "skill",
                "atomicity": "atomic",
                "reasons": ["catalog_match_suspicious"],
            }
        ],
        "persisted": {},
    }

    hydrated = hydrate_job_result_payload(catalog_conn, result)

    assert hydrated is result
    candidate = hydrated["candidates"][0]
    assert candidate["suggestion_id"] == suggestion_id
    assert candidate["similarity_hint"]["label"] == "Подозрительный матч"
    assert candidate["recommended_action"]["code"] == "check"
    assert candidate["nearest_preview"]["name"] == "Проведение интервью"
    assert candidate["nearest_preview"]["indicators"] == [
        {
            "text": "Проводит пользовательское интервью по сценарию",
            "type": "can",
            "complexity": "Начальный (junior)",
        }
    ]
    assert hydrated["persisted"]["review_open"] == 0
    assert hydrated["dag"]["status"] == "waiting_catalog"
    assert hydrated["catalog_state"]["catalog_applied"] is False
