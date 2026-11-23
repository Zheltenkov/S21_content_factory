"""Historical order, declared project inputs and accepted primary skills have separate contracts."""

from typing import Any

import pytest

from content_factory.catalog.pipeline import stage_dag_to_up
from content_factory.catalog.pipeline.curriculum.domain import ProjectBlueprint, SkillOccurrence
from content_factory.catalog.pipeline.curriculum.revision_planner import (
    RevisionDecision,
    SourceWork,
    WorkGroup,
    _bind_inputs,
    _schedule_revision,
)
from content_factory.catalog.pipeline.models import IndicatorSpec, SkillCandidate


def skill(identifier: str, name: str) -> SkillCandidate:
    return SkillCandidate(tmp_id=identifier, name=name, coverage_area=name, group=name,
                          indicators=[IndicatorSpec(text=name, bloom="apply")])


def source(identifier: str, title: str, description: str = "") -> dict[str, Any]:
    return {"source_id": identifier, "title": title, "description": description, "source_ref": f"Реестр!{identifier}",
            "source_hours": 8, "block": "Практика", "team_mode": "individual"}


def decision(keys: list[str], action: str = "retain", **fields: Any) -> dict[str, Any]:
    return {"source_ids": keys, "disposition": action, "source_ref": "Запись проектирования",
            "rationale": "Участник использует результат указанной работы.", "title": "Проверка примера",
            "participant_action": "Проверить запись.", "artifact": "Запись с результатом.",
            "validation_criteria": "Повторить расчёт.", **fields}


def compile_plan(
    units: list[dict[str, Any]], decisions: list[dict[str, Any]], skills: list[SkillCandidate] | None = None,
) -> dict[str, Any]:
    skills = skills or [skill("x", "Свойства молекул")]
    dag = {"order": [{"id": s.tmp_id} for s in skills], "final_edges": [
        {"src_id": a.tmp_id, "dst_id": b.tmp_id, "relation_type": "hard"}
        for a, b in zip(skills[:-1], skills[1:], strict=True)
    ]}
    return stage_dag_to_up.run({"source_curriculum": units, "revision_decisions": decisions}, skills, dag)


def test_source_only_consumer_keeps_its_place_when_preceding_source_waits_for_dag_project() -> None:
    units = [source("A", "Регистрация экспериментального протокола", "Передать подписанный документ."),
             source("B", "Сверка результатов", "Взять подписанный документ из предыдущей работы.")]
    plan = compile_plan(units, [], [skill("first", "Классификация изображений"),
                                    skill("second", "Регистрация экспериментального протокола")])
    rows = plan["rows"]
    assert [r["source_unit_ids"] for r in rows] == [[], ["A"], ["B"]]
    assert rows[1]["prerequisite_project_ids"] == [rows[0]["revision_project_id"]]
    assert rows[2]["prerequisite_project_ids"] == []
    assert not plan["report"]["order_violations"]


def test_same_tool_cannot_bind_a_career_exercise_to_a_technical_skill() -> None:
    units = [source("a", "Поиск вакансий", "Составить список вакансий со словом PostgreSQL."),
             source("b", "Численные алгоритмы"), source("c", "Публичное выступление")]
    plan = compile_plan(units, [], [skill("indexes", "Оптимизация индексов PostgreSQL")])
    claimed = [r for r in plan["rows"] if "indexes" in r["node_ids"]]
    assert len(claimed) == 1 and not claimed[0]["source_unit_ids"]


def test_multi_skill_project_needs_evidence_for_each_primary_skill() -> None:
    nodes = stage_dag_to_up.build_plan_nodes([skill("a", "Плотность ткани"), skill("b", "Раскрой рукава")])
    blueprint = ProjectBlueprint(block_key="Шитьё", artifact="Проба", occurrences=[
        SkillOccurrence(node=node, role="primary", touch_index=1, bloom_bucket="can") for node in nodes
    ])
    subjects = ["Плотность ткани", "Расход шерсти"]
    groups = [WorkGroup([SourceWork.model_validate(source(str(i), name))]) for i, name in enumerate(subjects)]
    _bind_inputs(groups, [blueprint], [])
    assert all(g.anchor is None for g in groups)

    # Both independently supported skills may share a single source exercise.
    combined = WorkGroup([SourceWork.model_validate(source("both", "Плотность ткани и раскрой рукава"))])
    _bind_inputs([combined], [blueprint], [])
    assert combined.anchor is blueprint
    assert set(combined.matched_skill_terms) == {"a", "b"}


def test_scaffold_neighbors_can_swap_when_the_second_supplies_the_first() -> None:
    subjects = [skill("fabric", "Плотность ткани"), skill("sleeve", "Раскрой рукава")]
    units = [source("A", "Плотность ткани"), source("B", "Раскрой рукава")]
    choices = [decision(["A"], title="Плотность ткани", prerequisite_source_ids=["B"]),
               decision(["B"], title="Раскрой рукава")]
    dag = {"order": [{"id": s.tmp_id} for s in subjects], "final_edges": []}
    plan = stage_dag_to_up.run({"source_curriculum": units, "revision_decisions": choices}, subjects, dag)
    rows = plan["rows"]
    assert [r["source_unit_ids"] for r in rows] == [["B"], ["A"]]
    assert rows[1]["prerequisite_project_ids"] == [rows[0]["revision_project_id"]]
    assert not plan["planner_meta"]["revision"]["decision_errors"]

    # An editor or serializer dropping the link must be caught even with the correct row order.
    from content_factory.catalog.pipeline.curriculum.source_fidelity import source_fidelity_errors
    rows[1]["prerequisite_project_ids"] = []
    assert any("потеряна обязательная связь" in error for error in source_fidelity_errors(plan))


def test_explicit_entry_can_follow_a_different_historical_work_in_the_source_register() -> None:
    plan = compile_plan([source("A", "Измерение"), source("B", "Знакомство")],
                        [decision(["B"], is_entry_project=True)])
    assert plan["rows"][0]["source_unit_ids"] == ["B"]


def test_cycle_is_reported_without_dropping_decisions_from_the_draft() -> None:
    plan = compile_plan([source("A", "Измерение"), source("B", "Сверка")], [
        decision(["A"], title="Измерение", prerequisite_source_ids=["B"]),
        decision(["B"], title="Сверка", prerequisite_source_ids=["A"]),
    ])
    first, second = [r for r in plan["rows"] if r["source_unit_ids"]]
    assert first["prerequisite_project_ids"] == [second["revision_project_id"]]
    assert second["prerequisite_project_ids"] == [first["revision_project_id"]]
    assert any("Цикл" in e for e in plan["planner_meta"]["revision"]["decision_errors"])


def test_either_earlier_primary_practice_can_supply_a_skill() -> None:
    prep, advanced = stage_dag_to_up.build_plan_nodes([skill("p", "Сбор образцов"), skill("a", "Сверка образцов")])
    entries = []
    for key, node in [("blocked", prep), ("consumer", advanced), ("available", prep)]:
        p = ProjectBlueprint(block_key="Лаборатория", artifact="Журнал", title=key, occurrences=[
            SkillOccurrence(node=node, role="primary", touch_index=1, bloom_bucket="can")])
        entries.append((p, {"source_unit_ids": [key], "revision_project_id": key, "route": "core"}, "Лаборатория", ""))
    errors = []
    order, links = _schedule_revision(entries, [0, 1, 2], {0: ["consumer"]}, errors,
                                      [{"src_id": "p", "dst_id": "a", "relation_type": "hard"}])
    assert order == [2, 1, 0] and links["consumer"] == {"available"} and not errors


def test_final_project_nomination_alone_does_not_prove_its_accepted_skills() -> None:
    [node] = stage_dag_to_up.build_plan_nodes([skill("a", "Раскрой рукава")])
    final = ProjectBlueprint(block_key="Защита", artifact="Образец", project_kind="capstone", occurrences=[
        SkillOccurrence(node=node, role="primary", touch_index=1, bloom_bucket="can")])
    group = WorkGroup([SourceWork.model_validate(source("id", "Журнал полива"))],
                      decision=RevisionDecision.model_validate(decision(["id"], title="Журнал полива", is_capstone=True)))
    _bind_inputs([group], [final], [])
    assert group.anchor is None


def test_generic_instruction_words_in_different_cases_do_not_establish_subject_coverage() -> None:
    [node] = stage_dag_to_up.build_plan_nodes([skill("k", "Проектирование проверки серебряного сплава")])
    practice = ProjectBlueprint(block_key="Материалы", artifact="Образец", occurrences=[
        SkillOccurrence(node=node, role="primary", touch_index=1, bloom_bucket="can")])
    group = WorkGroup([SourceWork.model_validate(source("x", "Проверки проектов"))])
    _bind_inputs([group], [practice], [])
    assert group.anchor is None


def test_declared_input_reorders_sources_after_a_merge_without_any_accepted_dag_edge() -> None:
    units = [source("B", "Сверка"), source("A1", "Описание"), source("A2", "Проба")]
    choices = [decision(["B"], prerequisite_source_ids=["A1", "A2"]), decision(["A1", "A2"], "merge")]
    plan = compile_plan(units, choices)
    merged, consumer = [r for r in plan["rows"] if r["source_unit_ids"]]
    assert merged["source_unit_ids"] == ["A1", "A2"] and consumer["source_unit_ids"] == ["B"]
    assert consumer["prerequisite_project_ids"] == [merged["revision_project_id"]]
    assert not plan["planner_meta"]["revision"]["decision_errors"]


@pytest.mark.parametrize(("choices", "message"), [
    ([decision(["A"], prerequisite_source_ids=["missing"])], "отсутствует конечный проект"),
    ([decision(["A"], prerequisite_source_ids=["A"])], "на себя"),
    ([decision(["A"], prerequisite_source_ids=["B"]), decision(["B"], prerequisite_source_ids=["A"])], "Цикл"),
    ([decision(["A"], prerequisite_source_ids=["B"]), decision(["B"], "specialize")], "зависит от специализации"),
])
def test_invalid_source_dependencies_leave_a_draft_and_publication_error(
    choices: list[dict[str, Any]], message: str,
) -> None:
    plan = compile_plan([source("A", "Сверка"), source("B", "Протокол")], choices)
    assert len(plan["rows"]) == 3
    assert any(message in error for error in plan["planner_meta"]["revision"]["decision_errors"])
    assert any(message in f["message"] for f in plan["report"]["publication_gate"]["failures"])
