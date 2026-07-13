"""ProjectTitlePolicy (project-contract epic, slice 5)."""

from __future__ import annotations

from content_factory.catalog.pipeline.curriculum.domain import (
    CurriculumBlock,
    PlanNode,
    ProjectBlueprint,
    SkillOccurrence,
)
from content_factory.catalog.pipeline.curriculum.title_policy import (
    apply_title_policy,
    build_project_title,
    title_violations,
)


def _node(name: str) -> PlanNode:
    return PlanNode(
        tmp_id=name, name=name, group="", block_key="", bloom=3,
        outcomes_know=(), outcomes_can=(), outcomes_skills=(), tools=(),
    )


def _project(nodes: list[PlanNode], *, title: str = "") -> ProjectBlueprint:
    return ProjectBlueprint(
        occurrences=[SkillOccurrence(node=n, role="primary") for n in nodes],
        block_key="b",
        artifact="",
        title=title,
    )


def test_violations_flag_long_wordy_and_stage_echo() -> None:
    assert title_violations("Прототип продукта с AI") == ()
    assert "too_long" in title_violations("С" * 80)
    assert "too_many_words" in title_violations("один два три четыре пять шесть семь восемь девять десять")
    assert "part_enumeration" in title_violations("Разработка сервиса часть 2")
    assert "echoes_stage" in title_violations("Аналитика данных", stage_title="Блок 3. Аналитика данных")


def test_build_title_from_skill_not_stage() -> None:
    proj = _project([_node("Настройка CI и релизного контура")])
    title = build_project_title(proj, stage_title="Блок 1. Инженерная культура")
    assert title
    assert title_violations(title, stage_title="Блок 1. Инженерная культура") == ()


def test_build_title_converges_on_min_words() -> None:
    # A 2-word skill title is padded with the block theme so it satisfies the min-word rule.
    proj = _project([_node("Прототипирование продукта")])
    title = build_project_title(proj, stage_title="Блок 2. AI-инструменты в маркетинге")
    assert title_violations(title, stage_title="Блок 2. AI-инструменты в маркетинге") == ()
    assert len(title.split()) >= 3


def test_apply_regenerates_only_violating_titles() -> None:
    good = _project([_node("Проектирование REST API сервиса")], title="Проектирование REST API сервиса")
    bad = _project([_node("Автоматизация процессов поддержки")], title="Ф" * 120)  # too long
    block = CurriculumBlock(block_keys=("b",), projects=[good, bad], title="Блок 1. Бэкенд")

    apply_title_policy([block])

    assert good.title == "Проектирование REST API сервиса"  # compliant → untouched
    assert bad.title != "Ф" * 120  # violating → regenerated
    assert title_violations(bad.title, stage_title="Блок 1. Бэкенд") == ()
