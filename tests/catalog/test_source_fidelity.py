from __future__ import annotations

import csv
import io
import json
from copy import deepcopy
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from content_factory.catalog.pipeline.brief_persistence import save_curriculum_plan
from content_factory.catalog.pipeline.curriculum.source_fidelity import (
    apply_source_fidelity_gate,
    source_fidelity_errors,
)
from content_factory.catalog.viewer.curriculum_ops import build_curriculum_plan_payload_from_rows, get_curriculum_plan
from content_factory.catalog.web.routers import intake, up


def _plan() -> dict[str, Any]:
    source = {"source_id": "seed-18", "title": "Исследование", "source_ref": "План!B18", "team_mode": "team"}
    row = {
        "id": 75, "project_name": "Карта наблюдений", "row_number": 1, "block_index": 1,
        "revision_project_id": "project-8", "source_unit_ids": ["seed-18"], "delivery_format": "групповой",
        "participant_action": "Сопоставить выборки", "artifact": "Карта значений",
        "validation_criteria": "Расчёт → совпадает с эталоном", "source_refs": ["План!B18"], "requirement_refs": ["L17"],
    }
    decision = {
        "source_id": "seed-18", "source_ref": "План!B18", "rationale": "Добавить проверку выборки",
        "disposition": "revise", "target_project_ids": ["project-8"], "lost_outcomes": [],
    }
    return {
        "rows": [row], "source_curriculum": [source], "status": "built",
        "revision_generation": {"state": "complete", "requirements": [{"ref": "L17", "text": "Проверить выборку"}]},
        "report": {
            "publication_gate": {"passed": True, "failures": []},
            "planner_meta": {"revision": {"dispositions": [decision], "source_curriculum": [dict(source)]}},
        },
    }


def test_current_revision_requires_a_targeted_decision_and_exact_saved_quote() -> None:
    plan = _plan()
    plan["revision_generation"]["version"] = "curriculum-revision/2"
    decision = plan["report"]["planner_meta"]["revision"]["dispositions"][0]
    decision.update(decision_ref="Строка брифа L17", requirement_refs=["L17"],
                    evidence=[{"ref": "L17", "quote": "Проверить выборку"}])
    assert not source_fidelity_errors(plan)

    # A real reference attached to an unrelated quote cannot substantiate coverage.
    decision["evidence"][0]["quote"] = "Проверить прибор"
    failures = source_fidelity_errors(plan)
    assert any("точная цитата" in msg for msg in failures)
    assert any("L17: требование не назначено" in msg for msg in failures)
    decision["evidence"][0]["quote"] = "Проверить выборку"
    decision["target_project_ids"] = ["другой-проект"]
    assert any("расходятся с решением" in msg for msg in source_fidelity_errors(plan))


def test_identical_practice_in_distinct_projects_is_an_advisory_signal() -> None:
    plan = _plan()
    plan["revision_generation"]["version"] = "curriculum-revision/2"
    row = plan["rows"][0]
    row["decision_ref"] = "L17"
    other = {**row, "project_name": "Вторая работа", "revision_project_id": "project-9"}
    plan["rows"].append(other)
    apply_source_fidelity_gate(plan)
    [group] = plan["report"]["repeated_revision_practice"]
    assert [project["id"] for project in group] == ["project-8", "project-9"]
    assert not any("Совпадают" in f["message"] for f in plan["report"]["publication_gate"]["failures"])
    other["validation_criteria"] = "Повторить наблюдение на другом приборе"
    apply_source_fidelity_gate(plan)
    assert not plan["report"]["repeated_revision_practice"]


def test_completed_batch_cannot_release_a_historical_fallback_as_reviewed() -> None:
    plan = _plan()
    plan["revision_generation"].update(version="curriculum-revision/2", requirements=[])
    plan["rows"][0]["requirement_refs"] = []
    decision = plan["report"]["planner_meta"]["revision"]["dispositions"][0]
    decision.update(disposition="retain", decision_ref="", decision_origin="historical_copy")
    assert any("отсутствует решение с основанием" in msg for msg in source_fidelity_errors(plan))


def test_expected_inventory_is_independent_and_decisions_do_not_count_as_output() -> None:
    plan = _plan()
    assert not source_fidelity_errors(plan)
    revision = plan["report"]["planner_meta"]["revision"]
    revision["source_curriculum"] = []
    plan["rows"] = []
    errors = source_fidelity_errors(plan)
    assert any("seed-18" in message and "исчезла" in message for message in errors)
    assert any("seed-18" in message and "покрыта" in message for message in errors)
    assert any("L17" in message and "не назначено" in message for message in errors)


def test_duplicate_ids_and_forged_replacement_are_reported() -> None:
    plan = _plan()
    revision = plan["report"]["planner_meta"]["revision"]
    revision["source_curriculum"][0]["team_mode"] = "individual"
    revision["dispositions"] *= 2
    plan["rows"][0]["source_unit_ids"] = ["invented"]
    errors = source_fidelity_errors(plan)
    assert any("реестр редакции расходится" in message for message in errors)
    assert any("seed-18 повторяется" in message for message in errors)
    assert any("invented" in message for message in errors)


def test_prerequisite_projects_are_rechecked_after_manual_reordering() -> None:
    plan = _plan()
    row = plan["rows"][0]
    row["prerequisite_project_ids"] = ["lab"]
    lab = {**row, "revision_project_id": "lab", "source_unit_ids": [], "prerequisite_project_ids": [], "row_number": 2}
    plan["rows"].append(lab)
    assert any("предшествующий проект lab" in msg for msg in source_fidelity_errors(plan))
    row["row_number"] = 3
    assert not source_fidelity_errors(plan)


def test_omit_requires_an_explicit_decision_and_does_not_lose_other_failures() -> None:
    plan = _plan()
    omission = {"source_id": "old", "source_ref": "А6"}
    plan["source_curriculum"].append(omission)
    revision = plan["report"]["planner_meta"]["revision"]
    revision["source_curriculum"].append(dict(omission))
    decision = {**omission, "disposition": "omit", "rationale": "Согласованный перенос", "target_project_ids": []}
    revision["dispositions"].append(decision)
    plan["report"]["publication_gate"]["failures"].append({"code": "blocking_questions", "message": "Есть вопрос"})
    apply_source_fidelity_gate(plan)
    assert any("подтверждения" in failure["message"] for failure in plan["report"]["publication_gate"]["failures"])
    decision.update(decision_ref="Протокол 19", decision_approved=True)
    apply_source_fidelity_gate(plan)
    assert plan["report"]["publication_gate"] == {
        "passed": False, "failures": [{"code": "blocking_questions", "message": "Есть вопрос"}],
    }


def test_form_change_and_absent_research_need_a_reviewed_decision() -> None:
    plan = _plan()
    plan["revision_generation"]["state"] = "unavailable"
    plan["rows"][0]["delivery_format"] = "индивидуальный"
    decision = plan["report"]["planner_meta"]["revision"]["dispositions"][0]
    decision["target_team_mode"] = "individual"
    assert len(source_fidelity_errors(plan)) == 2
    decision.update(decision_ref="Протокол 19", decision_approved=True)
    assert not source_fidelity_errors(plan)
    plan["revision_generation"]["unassigned_requirements"] = ["L42"]
    assert any("L42" in message for message in source_fidelity_errors(plan))


def test_legacy_payload_is_unchanged_and_contains_no_new_gate() -> None:
    for plan in ({"rows": []}, {"source_curriculum": [], "rows": [{"project_name": "Обычный проект"}]}):
        original = deepcopy(plan)
        apply_source_fidelity_gate(plan)
        assert plan == original


def test_sql_row_identity_survives_renumbering_and_is_not_inherited_by_replacement() -> None:
    plan = _plan()
    first_row = plan["rows"][0]
    second_row = {**first_row, "id": 81, "row_number": 2, "revision_project_id": "new", "source_unit_ids": []}
    plan["rows"].append(second_row)
    meta = {"id": 9, "payload_json": json.dumps(plan), "status": "built"}
    sql_rows = [
        {"id": 81, "row_number": 1, "project_name": "Вторая", "delivery_format": "индивидуальный"},
        {"id": 75, "row_number": 2, "project_name": "Первая", "delivery_format": "групповой"},
    ]
    restored = build_curriculum_plan_payload_from_rows(meta, sql_rows)
    assert [row["source_unit_ids"] for row in restored["rows"]] == [[], ["seed-18"]]
    assert not source_fidelity_errors({**restored, "rows": [
        {**row, "validation_criteria": "Совпадают → да"} for row in restored["rows"]
    ]})
    sql_rows[1]["id"] = 999
    restored = build_curriculum_plan_payload_from_rows(meta, sql_rows)
    assert "source_unit_ids" not in restored["rows"][1]
    assert any("не покрыта" in msg for msg in source_fidelity_errors(restored))


@pytest.mark.parametrize("path", ["/up/plans/9/csv", "/intake/jobs/9/plan.csv"])
def test_http_export_marks_unresolved_source_as_draft_and_keeps_reasons(path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan()
    plan["rows"][0]["delivery_format"] = "индивидуальный"
    app = FastAPI()
    app.include_router(up.router)
    app.include_router(intake.router)
    app.dependency_overrides[up.get_conn] = lambda: None
    monkeypatch.setattr(up, "ensure_intake_runtime_schema", lambda *_: None)
    monkeypatch.setattr(intake, "ensure_intake_runtime_schema", lambda *_: None)
    monkeypatch.setattr(up, "_require_plan", lambda *_: plan)
    monkeypatch.setattr(intake, "get_intake_job", lambda *_: {"result_payload": {"curriculum_plan": plan}})
    response = TestClient(app).get(f"/app/spravochnik{path}")
    assert response.status_code == 200
    assert "_draft.csv" in response.headers["content-disposition"]
    [exported] = csv.reader(io.StringIO(response.content.decode("utf-8-sig")))
    assert len(exported) == 22 and "Черновик" in exported[9] and "seed-18" in exported[9]
    assert exported[3] == plan["rows"][0]["project_name"]


def test_saved_revision_binds_db_ids_and_refreshes_calendar(catalog_conn: Any) -> None:
    plan = _plan()
    plan["revision_calendar"] = {"target_total_hours": 30, "hours_per_week": 10}
    plan["rows"][0].update(effort_hours=12, route="core")
    plan["rows"].append({
        **plan["rows"][0], "id": 987, "row_number": 2, "project_name": "Выборочный аудит",
        "route": "specialization", "effort_hours": 5, "revision_project_id": "supplement", "source_unit_ids": [],
    })
    brief = catalog_conn.execute("INSERT INTO profile_brief(raw_text) VALUES (?)", ("Тестовое обновление",))
    saved = save_curriculum_plan(catalog_conn, int(brief.lastrowid), plan)
    # Both the committed snapshot and the intake caller receive the actual database identities.
    sql_rows = catalog_conn.execute(
        "SELECT id FROM curriculum_plan_row WHERE plan_id=? ORDER BY row_number", (saved["plan_id"],)
    ).fetchall()
    ids = [row["id"] for row in sql_rows]
    assert [row["id"] for row in plan["rows"]] == ids
    catalog_conn.execute("UPDATE curriculum_plan_row SET row_number=9, effort_hours=16 WHERE id=?", (ids[0],))
    catalog_conn.commit()
    restored = get_curriculum_plan(catalog_conn, saved["plan_id"])
    assert restored is not None
    assert [row["source_unit_ids"] for row in restored["rows"]] == [[], ["seed-18"]]
    assert not source_fidelity_errors(restored)
    assert restored["summary"]["total_hours"] == 21
    assert restored["summary"]["revision_workload"]["core_hours"] == 16
    assert restored["summary"]["revision_workload"]["elective_hours"] == 5
