"""Additive activity-archetype classification invariants (redirect step 3)."""

from __future__ import annotations

from content_factory.catalog.pipeline.curriculum.archetype_classification import (
    CLASSIFICATION_VERSION,
    activity_archetype_decision_key,
    classify_activity_archetype,
    classify_activity_archetypes,
)
from content_factory.catalog.pipeline.curriculum.domain import (
    CurriculumBlock,
    PlanNode,
    ProjectBlueprint,
    SkillOccurrence,
    TemplateBinding,
)


def _node(
    name: str,
    *,
    bloom: int = 3,
    outcomes: tuple[str, ...] = (),
    tools: tuple[str, ...] = (),
) -> PlanNode:
    return PlanNode(
        tmp_id=name,
        name=name,
        group="",
        block_key="test",
        bloom=bloom,
        outcomes_know=(),
        outcomes_can=outcomes,
        outcomes_skills=(),
        tools=tools,
    )


def _project(
    *nodes: PlanNode,
    kind: str = "integrative",
    title: str = "",
    artifact: str = "",
    template_code: str = "",
) -> ProjectBlueprint:
    return ProjectBlueprint(
        occurrences=[SkillOccurrence(node=node, role="primary") for node in nodes],
        block_key="test",
        artifact=artifact,
        title=title,
        project_kind=kind,
        artifact_template_code=template_code,
    )


def test_high_confidence_construct_is_assigned() -> None:
    project = _project(
        _node(
            "Разработка сервиса",
            outcomes=("Выполняет реализацию работающего сервиса",),
            bloom=3,
        )
    )

    result = classify_activity_archetype(project)

    assert result.assigned == "construct"
    assert result.suggested == "construct"
    assert result.confidence == "high"
    assert result.version == CLASSIFICATION_VERSION


def test_ambiguous_result_keeps_suggestion_without_assignment() -> None:
    project = _project(_node("Проектирование и реализация решения", bloom=5))

    result = classify_activity_archetype(project)

    assert result.assigned is None
    assert result.suggested == "design"
    assert result.confidence in {"medium", "low"}
    assert any(reason.startswith("альтернатива:") for reason in result.reasons)


def test_experiment_requires_paired_explicit_signals() -> None:
    experiment = _project(
        _node(
            "Анализ данных эксперимента",
            outcomes=("Проводит анализ измерений по протоколу",),
            bloom=4,
        )
    )
    plain_test = _project(_node("Тестирование API", bloom=3))

    assert "experiment" in classify_activity_archetype(experiment).modifiers
    assert "experiment" not in classify_activity_archetype(plain_test).modifiers


def test_capstone_is_integrative_modifier_not_primary_archetype() -> None:
    project = _project(
        _node("Разработка решения", outcomes=("Выполняет реализацию решения",), bloom=3),
        kind="capstone",
    )

    result = classify_activity_archetype(project)

    assert result.assigned == "construct"
    assert "integrative" in result.modifiers


def test_generated_title_artifact_and_template_do_not_affect_classification() -> None:
    node = _node("Исследование потребностей", outcomes=("Проводит анализ интервью",), bloom=4)
    plain = _project(node)
    contaminated = _project(
        node,
        title="Разработка работающего прототипа",
        artifact="Репозиторий, CI и запускаемый сервис",
        template_code="engineering-template",
    )

    assert classify_activity_archetype(plain) == classify_activity_archetype(contaminated)


def test_unknown_activity_degrades_to_unclassified() -> None:
    result = classify_activity_archetype(_project(_node("Предметная область без действия", bloom=2)))

    assert result.assigned is None
    assert result.suggested is None
    assert result.confidence == "none"


def test_accepted_brief_artifact_family_is_strong_archetype_evidence() -> None:
    project = _project(_node("Работа с потребностями клиента", bloom=4))
    project.artifact_family = "analysis"
    project.template_binding = TemplateBinding(
        template_code="customer-research",
        source="brief",
    )

    result = classify_activity_archetype(project)

    assert result.assigned == "investigate"
    assert result.confidence == "high"
    assert any("brief-template" in reason for reason in result.reasons)


def test_repeat_skill_is_not_equal_to_primary_activity_signal() -> None:
    primary = _node(
        "Презентация решения",
        outcomes=("Проводит презентацию решения для заказчика",),
        bloom=3,
    )
    repeat = _node(
        "Развёртывание и мониторинг сервиса",
        outcomes=("Эксплуатирует сервис",),
        bloom=3,
    )
    project = ProjectBlueprint(
        occurrences=[
            SkillOccurrence(node=primary, role="primary"),
            SkillOccurrence(node=repeat, role="reinforcement", touch_index=2),
        ],
        block_key="test",
        artifact="Запись презентации",
    )

    result = classify_activity_archetype(project)

    assert result.assigned == "perform"


def test_batch_confirmation_overrides_ambiguous_suggestion_by_stable_key() -> None:
    project = _project(_node("Неоднозначная практика", bloom=2))
    key = activity_archetype_decision_key(project)

    classify_activity_archetypes(
        [CurriculumBlock(block_keys=("test",), projects=[project])],
        confirmations={key: "perform"},
    )

    assert project.activity_archetype == "perform"
    assert project.activity_archetype_source == "methodologist"
    assert project.activity_archetype_confidence == "high"
    assert project.activity_archetype_version == "manual/v1"
    assert project.activity_archetype_decision_key == key


def test_block_pass_is_additive_and_preserves_methodologist_override() -> None:
    automatic = _project(
        _node("Разработка сервиса", outcomes=("Выполняет реализацию сервиса",), bloom=3)
    )
    automatic.policy_area = "operations"
    confirmed = _project(_node("Неоднозначная практика", bloom=2))
    confirmed.activity_archetype = "perform"
    confirmed.activity_archetype_suggestion = "perform"
    confirmed.activity_archetype_confidence = "high"
    confirmed.activity_archetype_source = "methodologist"
    confirmed.activity_archetype_version = "manual/v1"

    classify_activity_archetypes([CurriculumBlock(block_keys=("test",), projects=[automatic, confirmed])])

    assert automatic.activity_archetype == "construct"
    assert automatic.policy_area == "operations"
    assert confirmed.activity_archetype == "perform"
    assert confirmed.activity_archetype_source == "methodologist"
    assert confirmed.activity_archetype_version == "manual/v1"
