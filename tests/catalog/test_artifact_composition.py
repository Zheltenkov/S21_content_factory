"""Artifact slot composition invariants (redirect step 4)."""

from __future__ import annotations

from content_factory.catalog.pipeline.curriculum.artifact_composition import (
    compose_project_artifact,
)
from content_factory.catalog.pipeline.curriculum.artifact_policy import (
    apply_artifact_contracts,
)
from content_factory.catalog.pipeline.curriculum.artifact_skeletons import (
    build_archetype_skeleton,
)
from content_factory.catalog.pipeline.curriculum.domain import (
    CurriculumBlock,
    PlanNode,
    ProjectBlueprint,
    SkillOccurrence,
    TemplateBinding,
)
from content_factory.catalog.pipeline.curriculum.methodology_profile import DEFAULT_PROFILE


def _project(*, artifact: str = "Проверяемый артефакт (практика) по навыку «X»") -> ProjectBlueprint:
    node = PlanNode(
        tmp_id="n",
        name="Разработка решения",
        group="",
        block_key="b",
        bloom=3,
        outcomes_know=(),
        outcomes_can=("Выполняет реализацию решения",),
        outcomes_skills=(),
        tools=(),
    )
    project = ProjectBlueprint(
        occurrences=[SkillOccurrence(node=node, role="primary")],
        block_key="b",
        artifact=artifact,
    )
    project.activity_archetype = "construct"
    project.activity_archetype_confidence = "high"
    project.activity_archetype_version = "activity-archetype/v1"
    return project


def _diagnostic_codes(project: ProjectBlueprint) -> set[str]:
    return {diagnostic.code for diagnostic in project.artifact_merge_diagnostics}


def test_archetype_skeleton_resolves_non_product_domain_without_policy_area() -> None:
    project = _project()

    compose_project_artifact(
        project,
        profile_contract=None,
        profile_policy_set_available=True,
    )

    assert project.artifact_contract is not None
    assert project.artifact_contract.artifact_type == "working_implementation"
    assert project.artifact_contract.policy_area == ""
    assert project.artifact_contract.activity_archetype == "construct"
    assert project.artifact_contract_sources == ("archetype_skeleton",)
    assert "работающая реализация" in project.artifact
    assert "→" in project.enrichment["validation_criteria"]


def test_profile_refines_type_while_skeleton_keeps_universal_slots() -> None:
    project = _project()
    project.policy_area = "ai_automation"

    apply_artifact_contracts(
        [CurriculumBlock(block_keys=("b",), projects=[project])],
        profile=DEFAULT_PROFILE,
    )

    assert project.artifact_contract is not None
    assert project.artifact_contract.artifact_type == "executable_workflow"
    assert "исполняемый workflow" in project.artifact_contract.deliverables
    assert "работающая реализация" in project.artifact_contract.deliverables
    assert project.artifact_contract_sources == ("profile", "archetype_skeleton")
    assert "profile_refines_skeleton_type" in _diagnostic_codes(project)


def test_brief_template_wording_is_preserved_and_augmented() -> None:
    project = _project(artifact="Тематический симулятор переговоров")
    project.policy_area = "product_creation"
    project.template_binding = TemplateBinding(
        template_code="brief-simulator",
        template_version="1",
        source="brief",
    )
    project.enrichment["validation_criteria"] = (
        "Сценарий: выполняется на контрольном входе → получена запись результата "
        "(видеозапись, ручная проверка)"
    )

    apply_artifact_contracts(
        [CurriculumBlock(block_keys=("b",), projects=[project])],
        profile=DEFAULT_PROFILE,
    )

    assert project.artifact.startswith("Тематический симулятор переговоров")
    assert "Обязательный проверяемый состав" in project.artifact
    assert project.enrichment["validation_criteria"].startswith("Критерии из шаблона")
    assert "Критерии приёмки" in project.enrichment["validation_criteria"]
    assert project.artifact_contract_sources[0] == "brief_template"
    assert "template_artifact_augmented" in _diagnostic_codes(project)
    assert "template_criteria_augmented" in _diagnostic_codes(project)


def test_generic_bound_template_is_replaced_with_explicit_diagnostics() -> None:
    project = _project()
    project.template_binding = TemplateBinding(template_code="weak", source="brief")
    project.enrichment["validation_criteria"] = "Артефакт создан, навыки применены"

    compose_project_artifact(
        project,
        profile_contract=None,
        profile_policy_set_available=True,
    )

    assert project.artifact.startswith("Проект сдаётся как")
    assert "generic_template_artifact_replaced" in _diagnostic_codes(project)
    assert "weak_template_criteria_augmented" in _diagnostic_codes(project)
    assert "Артефакт создан, навыки применены" in project.enrichment["validation_criteria"]
    assert "Критерии приёмки" in project.enrichment["validation_criteria"]


def test_experiment_and_integrative_modifiers_extend_the_base_skeleton() -> None:
    project = _project()
    project.activity_archetype = "investigate"
    project.activity_archetype_modifiers = ("experiment", "integrative")

    contract = build_archetype_skeleton(project)

    assert contract is not None
    assert "гипотеза" in contract.deliverables
    assert "сквозной результат" in contract.deliverables
    assert any(criterion.subject == "протокол" for criterion in contract.acceptance_criteria)
    assert any(criterion.subject == "интеграция" for criterion in contract.acceptance_criteria)


def test_unresolved_project_keeps_draft_and_requests_methodologist() -> None:
    project = _project(artifact="Черновой предметный результат")
    project.activity_archetype = ""

    compose_project_artifact(
        project,
        profile_contract=None,
        profile_policy_set_available=True,
    )

    assert project.artifact_contract is None
    assert project.artifact == "Черновой предметный результат"
    assert project.artifact_contract_sources == ("draft",)
    assert "draft_artifact_contract_unresolved" in _diagnostic_codes(project)
