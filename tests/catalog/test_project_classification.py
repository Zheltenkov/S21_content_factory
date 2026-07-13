"""Explicit project classification (project-contract epic, slice 3)."""

from __future__ import annotations

from content_factory.catalog.pipeline.curriculum.domain import (
    CurriculumBlock,
    PlanNode,
    ProjectBlueprint,
    SkillOccurrence,
)
from content_factory.catalog.pipeline.curriculum.project_classification import (
    classify_policy_area,
    classify_project_type,
    classify_projects,
)


def _node(name: str, *, group: str = "", block_key: str = "", bloom: int = 3, tools: tuple[str, ...] = ()) -> PlanNode:
    return PlanNode(
        tmp_id=name,
        name=name,
        group=group,
        block_key=block_key,
        bloom=bloom,
        outcomes_know=(),
        outcomes_can=(),
        outcomes_skills=(),
        tools=tools,
    )


def _project(nodes: list[PlanNode], *, kind: str = "integrative") -> ProjectBlueprint:
    return ProjectBlueprint(
        occurrences=[SkillOccurrence(node=n, role="primary") for n in nodes],
        block_key=nodes[0].block_key if nodes else "",
        artifact="",
        project_kind=kind,
    )


def test_policy_area_detects_engineering() -> None:
    assert classify_policy_area([_node("Настройка CI/CD пайплайна", tools=("Git",))]) == "engineering_discipline"


def test_policy_area_detects_ai_automation() -> None:
    assert classify_policy_area([_node("Разработка AI workflow для поддержки", group="Автоматизация")]) == "ai_automation"


def test_policy_area_empty_when_no_hint() -> None:
    assert classify_policy_area([_node("Абстрактная тема без ключевых слов")]) == ""


def test_project_type_lab_project_capstone() -> None:
    assert classify_project_type(_project([_node("SQL")])) == "lab"
    assert classify_project_type(_project([_node("SQL"), _node("REST")])) == "project"
    assert classify_project_type(_project([_node("Итог")], kind="capstone")) == "capstone"


def test_classify_projects_sets_fields_and_capstone_area() -> None:
    proj = _project([_node("Разработка прототипа продукта MVP")])
    cap = _project([_node("Финал")], kind="capstone")
    block = CurriculumBlock(block_keys=("b",), projects=[proj, cap])
    classify_projects([block])
    assert proj.project_type == "lab"
    assert proj.policy_area == "product_creation"
    assert cap.project_type == "capstone"
    assert cap.policy_area == "capstone"
