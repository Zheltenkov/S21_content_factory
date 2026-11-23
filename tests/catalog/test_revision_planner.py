"""Historical projects, accepted skills and approved changes have independent identities."""

from __future__ import annotations

from typing import Any

import pytest

from content_factory.catalog.pipeline import stage_dag_to_up
from content_factory.catalog.pipeline.models import IndicatorSpec, SkillCandidate


def _unit(identifier: str, subject: str, *, team: bool = False) -> dict[str, Any]:
    return {
        "source_id": identifier, "title": identifier + "_" + subject, "description": subject,
        "source_ref": f"Маршрут!A{identifier}", "block": "Мастерская", "source_hours": 12,
        "team_mode": "team" if team else "individual",
    }


def _skill(identifier: str, subject: str) -> SkillCandidate:
    return SkillCandidate(
        tmp_id=identifier, name=subject, group=subject, coverage_area=subject,
        indicators=[IndicatorSpec(text=f"Собирает {subject}", bloom="apply")],
    )


def _decision(keys: list[str], disposition: str, **overrides: Any) -> dict[str, Any]:
    return {
        "source_ids": keys, "disposition": disposition, "rationale": "Клиент просит предъявить образец ткани.",
        "source_ref": "L15 «сдать образец ткани»", "requirement_refs": ["L15"],
        "title": "Контроль тканого образца", "participant_action": "Выбрать плотность и выткать образец.",
        "artifact": "Образец ткани со схемой нитей", "validation_criteria": "Пир пересчитывает нити на сантиметр.",
        **overrides,
    }


def _run(units: list[dict[str, Any]], **spec: Any) -> dict[str, Any]:
    skills = [_skill("sA", "Плотность ткани"), _skill("sB", "Раскрой рукава")]
    dag = {
        "order": [{"id": s.tmp_id} for s in skills],
        "final_edges": [{"src_id": "sA", "dst_id": "sB", "relation_type": "hard"}],
    }
    return stage_dag_to_up.run({"source_curriculum": units, **spec}, skills, dag)


def test_unplanned_skill_does_not_inherit_a_generic_project_to_deliver() -> None:
    from content_factory.catalog.pipeline.curriculum.revision_planner import apply_revision_rows

    proposal = {"revision_project_id": "draft", "source_unit_ids": [], "route": "needs_design",
                "participant_action": "Измерить отклонение", "hours_basis": "dag_estimate", "prerequisite_project_ids": []}
    row = {"node_names": ["Обработка измерений"], "artifact": "Устроить публичный запуск",
           "artifact_contract": {"deliverables": ["Устроить публичный запуск"]},
           "validation_criteria": "Сдать MVP", "materials": "Сдать MVP", "storytelling": "Публичный запуск"}
    apply_revision_rows([row], {"revision": {"row_specs": [proposal]}})
    assert row["project_summary"] == "Измерить отклонение"
    assert not any(row[key] for key in ("artifact", "validation_criteria", "storytelling", "artifact_contract"))
    assert "Методологу" in row["materials"]


def test_merges_preserve_both_sources_and_required_dag_work_can_be_added() -> None:
    units = [_unit("one", "Плотность ткани", team=True), _unit("two", "Нити в основе", team=True)]
    plan = _run(units, revision_decisions=[_decision(["one", "two"], "merge", effort_hours=17)])
    revised = next(row for row in plan["rows"] if row["source_unit_ids"])
    assert revised["source_unit_ids"] == ["one", "two"]
    assert revised["delivery_format"] == "групповой" and revised["group_size"] >= 2
    assert revised["effort_hours"] == 17
    assert revised["project_name"] == "Контроль тканого образца"
    assert "сантиметр" in revised["validation_criteria"] and "Каждый участник" in revised["validation_criteria"]
    assert all(token in revised["materials"] for token in (
        "Маршрут!Aone", "Маршрут!Atwo", "сдать образец ткани", "сантиметр",
    ))
    assert revised["requirement_refs"] == ["L15"] and "схемой нитей" in revised["project_summary"]
    revision = plan["planner_meta"]["revision"]
    assert revision["source_curriculum"] == units
    assert [decision["disposition"] for decision in revision["dispositions"]] == ["merge", "merge"]
    assert {d["target_project_ids"][0] for d in revision["dispositions"]} == {revised["revision_project_id"]}
    assert not plan["report"]["order_violations"] and not plan["report"]["project_violations"]
    assert {node for row in plan["rows"] for node in row["node_ids"]} == {"sA", "sB"}
    assert any(r["prerequisite_project_ids"] for r in plan["rows"])
    assert any(not r["source_unit_ids"] for r in plan["rows"])


@pytest.mark.parametrize(("approved", "disposition"), [(False, "hold"), (True, "omit")])
def test_omission_requires_explicit_approval_and_retains_lost_outcome(approved: bool, disposition: str) -> None:
    plan = _run(
        [_unit("out", "Плетение корзины")], revision_decisions=[_decision(
            ["out"], "omit", approved=approved, lost_outcomes=["Плетение корзины"],
            review_question="Допускается убрать плетение корзины?",
        )],
    )
    trace = plan["planner_meta"]["revision"]["dispositions"][0]
    assert trace["disposition"] == disposition and not trace["target_project_ids"]
    assert trace["lost_outcomes"] == ["Плетение корзины"] and trace["review_question"]
    assert all(not row["source_unit_ids"] for row in plan["rows"])


def test_unapproved_mix_of_team_and_individual_is_visible_and_preserves_both_forms() -> None:
    units = [_unit("one", "Крой спинки", team=True), _unit("two", "Крой воротника")]
    plan = _run(units, revision_decisions=[_decision(["one", "two"], "merge")])
    mapped = {tuple(r["source_unit_ids"]): r["delivery_format"] for r in plan["rows"] if r["source_unit_ids"]}
    assert mapped == {("one",): "групповой", ("two",): "индивидуальный"}
    assert plan["planner_meta"]["revision"]["diagnostics"]


def test_source_only_fallback_never_claims_accepted_skills_and_keeps_research_trace() -> None:
    units = [_unit("A12", "Красители из растений")]
    requirements = "1. **Красители.** В A12 записать температуру ванны. Основание — Лаборатория!A14."
    plan = _run(units, requirements_text=requirements)
    archived = next(row for row in plan["rows"] if row["source_unit_ids"])
    assert not archived["node_ids"]
    assert archived["research_evidence"][0]["binding"] == "source_id"
    assert "температуру ванны" in archived["materials"]
    assert archived["hours_basis"] == "historical_reference"
    assert archived["outcome_count"] and "проектный навык" not in archived["learning_outcomes"]
    assert plan["summary"]["total_hours"] == sum(row["effort_hours"] for row in plan["rows"])


def test_specialization_and_stable_ids_survive_input_reordering() -> None:
    units = [_unit("old", "Окрашивание шёлка"), _unit("core", "Плотность ткани")]
    spec = {"revision_decisions": [_decision(["old"], "specialize")]}
    markers = []
    for source_units in (units, list(reversed(units))):
        rows = _run(source_units, **spec)["rows"]
        source_rows = {row["source_unit_ids"][0]: row for row in rows if row["source_unit_ids"]}
        assert source_rows["old"]["route"] == "specialization"
        markers.append({key: row["revision_project_id"] for key, row in source_rows.items()})
    assert markers[0] == markers[1]
