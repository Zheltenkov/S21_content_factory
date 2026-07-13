"""Artifact policy registry + apply pass (project-contract epic, slice 4)."""

from __future__ import annotations

from content_factory.catalog.pipeline.curriculum.artifact_policy import (
    POLICY_REGISTRY,
    apply_artifact_contracts,
    build_artifact_contract,
    render_acceptance_text,
)
from content_factory.catalog.pipeline.curriculum.domain import (
    CurriculumBlock,
    PlanNode,
    ProjectBlueprint,
    SkillOccurrence,
)
from content_factory.catalog.pipeline.curriculum.project_quality import (
    is_generic_artifact,
    is_generic_criterion,
)


def _project(policy_area: str, artifact: str) -> ProjectBlueprint:
    node = PlanNode(
        tmp_id="n", name="n", group="", block_key="b", bloom=3,
        outcomes_know=(), outcomes_can=(), outcomes_skills=(), tools=(),
    )
    bp = ProjectBlueprint(
        occurrences=[SkillOccurrence(node=node, role="primary")],
        block_key="b",
        artifact=artifact,
    )
    bp.policy_area = policy_area
    return bp


def test_every_policy_area_has_criteria_with_evidence() -> None:
    for area, contract in POLICY_REGISTRY.items():
        assert contract.policy_area == area
        assert contract.acceptance_criteria
        for criterion in contract.acceptance_criteria:
            assert criterion.evidence_type  # every acceptance criterion is backed by evidence


def test_build_contract_none_when_unclassified() -> None:
    assert build_artifact_contract(_project("", "x")) is None
    assert build_artifact_contract(_project("ai_automation", "x")) is not None


def test_apply_replaces_generic_for_classified_keeps_unclassified() -> None:
    generic = "Проверяемый артефакт (практика) по навыку «SQL»"
    classified = _project("engineering_discipline", generic)
    unclassified = _project("", generic)
    block = CurriculumBlock(block_keys=("b",), projects=[classified, unclassified])

    apply_artifact_contracts([block])

    # classified: contract attached, generic artifact + criteria replaced with policy-backed
    assert classified.artifact_contract is not None
    assert not is_generic_artifact(classified.artifact)
    assert not is_generic_criterion(classified.enrichment["validation_criteria"])
    # unclassified: no contract, generic artifact untouched (draft-only, flagged elsewhere)
    assert unclassified.artifact_contract is None
    assert is_generic_artifact(unclassified.artifact)


def test_policy_criteria_override_template_as_methodical_floor() -> None:
    # P0-5: for a classified project the policy acceptance criteria are the methodical floor,
    # authoritative even over a template's themed (possibly weaker) criteria.
    proj = _project("ai_automation", "Проверяемый артефакт (практика) по навыку «X»")
    proj.enrichment["validation_criteria"] = "Тематический критерий из шаблона"
    apply_artifact_contracts([CurriculumBlock(block_keys=("b",), projects=[proj])])
    assert proj.enrichment["validation_criteria"] != "Тематический критерий из шаблона"
    assert "Критерии приёмки" in proj.enrichment["validation_criteria"]


def test_capstone_artifact_always_replaced_with_contract() -> None:
    # P0-3: the capstone production contract must apply even over a non-generic stale artifact.
    cap = _project("capstone", "Итоговый интеграционный артефакт по программе")
    apply_artifact_contracts([CurriculumBlock(block_keys=("b",), projects=[cap])])
    assert cap.artifact_contract is not None
    assert "интеграционный артефакт" not in cap.artifact.casefold()
    assert "MVP" in cap.artifact or "demo" in cap.artifact.casefold()


def test_render_acceptance_text_lists_criteria() -> None:
    text = render_acceptance_text(POLICY_REGISTRY["capstone"])
    assert "Критерии приёмки" in text
    assert text.count("- ") >= 2
