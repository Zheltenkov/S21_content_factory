"""Deterministic slot composition for project artifact contracts.

Composition order is explicit and inspectable: a bound template supplies themed wording,
the methodology profile supplies domain-specific constraints, the activity skeleton supplies
the universal assessment floor, and the planner draft is only a fallback. No layer silently
deletes stronger criteria; conflicts and augmentations are recorded on the project.
"""

from __future__ import annotations

import re

from .artifact_skeletons import build_archetype_skeleton
from .domain import (
    AcceptanceCriterion,
    ArtifactContract,
    ArtifactContractSource,
    ArtifactMergeDiagnostic,
    ProjectBlueprint,
)
from .project_quality import is_generic_artifact, is_testable_criterion

COMPOSITION_VERSION = "artifact-composition/v1"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").casefold().replace("ё", "е")).strip()


def _unique_strings(*groups: tuple[str, ...]) -> tuple[str, ...]:
    values: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for value in group:
            key = _norm(value)
            if key and key not in seen:
                values.append(value)
                seen.add(key)
    return tuple(values)


def _criterion_key(criterion: AcceptanceCriterion) -> tuple[str, str, str, str]:
    return (
        _norm(criterion.subject),
        _norm(criterion.check),
        _norm(criterion.expected_result),
        _norm(criterion.evidence_type),
    )


def _unique_criteria(*groups: tuple[AcceptanceCriterion, ...]) -> tuple[AcceptanceCriterion, ...]:
    values: list[AcceptanceCriterion] = []
    seen: set[tuple[str, str, str, str]] = set()
    for group in groups:
        for criterion in group:
            key = _criterion_key(criterion)
            if key not in seen:
                values.append(criterion)
                seen.add(key)
    return tuple(values)


def _template_source(project: ProjectBlueprint) -> ArtifactContractSource | None:
    if project.template_binding is None:
        return None
    return "brief_template" if project.template_binding.source == "brief" else "global_template"


def _merge_structured_contracts(
    profile_contract: ArtifactContract | None,
    skeleton: ArtifactContract | None,
) -> ArtifactContract | None:
    if profile_contract is None and skeleton is None:
        return None
    primary = profile_contract or skeleton
    assert primary is not None
    profile_deliverables = profile_contract.deliverables if profile_contract else ()
    skeleton_deliverables = skeleton.deliverables if skeleton else ()
    profile_evidence = profile_contract.evidence_requirements if profile_contract else ()
    skeleton_evidence = skeleton.evidence_requirements if skeleton else ()
    profile_criteria = profile_contract.acceptance_criteria if profile_contract else ()
    skeleton_criteria = skeleton.acceptance_criteria if skeleton else ()
    profile_constraints = profile_contract.publication_constraints if profile_contract else ()
    skeleton_constraints = skeleton.publication_constraints if skeleton else ()
    return ArtifactContract(
        artifact_type=primary.artifact_type,
        policy_area=profile_contract.policy_area if profile_contract else "",
        activity_archetype=skeleton.activity_archetype if skeleton else "",
        deliverables=_unique_strings(profile_deliverables, skeleton_deliverables),
        evidence_requirements=_unique_strings(profile_evidence, skeleton_evidence),
        acceptance_criteria=_unique_criteria(profile_criteria, skeleton_criteria),
        execution_environment=(
            profile_contract.execution_environment
            if profile_contract and profile_contract.execution_environment
            else skeleton.execution_environment if skeleton else ""
        ),
        publication_constraints=_unique_strings(profile_constraints, skeleton_constraints),
        composition_version=COMPOSITION_VERSION,
    )


def render_artifact_line(contract: ArtifactContract) -> str:
    """Render the structured deliverable and its evidence as a readable artifact line."""
    deliverables = ", ".join(contract.deliverables)
    evidence = ", ".join(contract.evidence_requirements)
    line = f"Проект сдаётся как {deliverables}"
    if evidence:
        line += f"; доказательство: {evidence}"
    return line


def render_acceptance_text(contract: ArtifactContract) -> str:
    """Render structured acceptance criteria without delegating checks to prose."""
    lines = ["Критерии приёмки:"]
    for criterion in contract.acceptance_criteria:
        mode = "авто" if criterion.verification_mode == "automatic" else "ручная проверка"
        lines.append(
            f"- {criterion.subject}: {criterion.check} → {criterion.expected_result} "
            f"({criterion.evidence_type}, {mode})"
        )
    return "\n".join(lines)


def _append_missing_requirements(text: str, contract: ArtifactContract) -> tuple[str, bool]:
    normalized = _norm(text)
    missing_deliverables = [item for item in contract.deliverables if _norm(item) not in normalized]
    missing_evidence = [item for item in contract.evidence_requirements if _norm(item) not in normalized]
    additions: list[str] = []
    if missing_deliverables:
        additions.append("Обязательный проверяемый состав: " + ", ".join(missing_deliverables))
    if missing_evidence:
        additions.append("Подтверждение: " + ", ".join(missing_evidence))
    if not additions:
        return text, False
    separator = ". " if text and not text.rstrip().endswith((".", "!", "?")) else " "
    return text.rstrip() + separator + ". ".join(additions), True


def compose_project_artifact(
    project: ProjectBlueprint,
    *,
    profile_contract: ArtifactContract | None,
    profile_policy_set_available: bool,
) -> None:
    """Compose artifact slots and persist provenance plus conflict diagnostics on a project."""
    skeleton = build_archetype_skeleton(project)
    merged = _merge_structured_contracts(profile_contract, skeleton)
    diagnostics: list[ArtifactMergeDiagnostic] = []
    template_source = _template_source(project)
    structured_source_list: list[ArtifactContractSource] = []
    if profile_contract is not None:
        structured_source_list.append("profile")
    if skeleton is not None:
        structured_source_list.append("archetype_skeleton")
    structured_sources = tuple(structured_source_list)

    if project.policy_area and not profile_policy_set_available:
        diagnostics.append(
            ArtifactMergeDiagnostic(
                code="profile_policy_set_unavailable",
                severity="warning",
                slot="contract",
                sources=("profile",),
                resolution="Профильный слой пропущен; использован archetype skeleton или draft.",
            )
        )
    elif project.policy_area and profile_contract is None:
        diagnostics.append(
            ArtifactMergeDiagnostic(
                code="profile_policy_missing",
                severity="warning",
                slot="contract",
                sources=("profile",),
                resolution=f"Для policy_area={project.policy_area} нет контракта в выбранном profile set.",
            )
        )
    if skeleton is None:
        diagnostics.append(
            ArtifactMergeDiagnostic(
                code="activity_skeleton_unavailable",
                severity="warning",
                slot="contract",
                sources=("archetype_skeleton",),
                resolution="Архетип не определён; универсальный слой не применён.",
            )
        )
    if (
        profile_contract is not None
        and skeleton is not None
        and profile_contract.artifact_type != skeleton.artifact_type
    ):
        diagnostics.append(
            ArtifactMergeDiagnostic(
                code="profile_refines_skeleton_type",
                severity="info",
                slot="artifact_type",
                sources=("profile", "archetype_skeleton"),
                resolution=(
                    f"Выбран профильный тип {profile_contract.artifact_type}; "
                    f"универсальный тип {skeleton.artifact_type} сохранён через обязательные slots."
                ),
            )
        )

    original_artifact = project.artifact.strip()
    original_criteria = project.enrichment.get("validation_criteria", "").strip()
    artifact_sources: list[ArtifactContractSource] = []
    criteria_sources: list[ArtifactContractSource] = []
    if merged is not None:
        if template_source and original_artifact and not is_generic_artifact(original_artifact):
            project.artifact, augmented = _append_missing_requirements(original_artifact, merged)
            artifact_sources.append(template_source)
            if augmented:
                diagnostics.append(
                    ArtifactMergeDiagnostic(
                        code="template_artifact_augmented",
                        severity="info",
                        slot="artifact",
                        sources=(template_source, *structured_sources),
                        resolution="Тематический текст сохранён и дополнен обязательными slots.",
                    )
                )
        else:
            project.artifact = render_artifact_line(merged)
            if template_source and original_artifact:
                diagnostics.append(
                    ArtifactMergeDiagnostic(
                        code="generic_template_artifact_replaced",
                        severity="warning",
                        slot="artifact",
                        sources=(template_source, *structured_sources),
                        resolution="Общий текст шаблона заменён проверяемым контрактом.",
                    )
                )
        artifact_sources.extend(structured_sources)

        structured_criteria = render_acceptance_text(merged)
        if template_source and original_criteria:
            project.enrichment["validation_criteria"] = (
                f"Критерии из шаблона:\n{original_criteria}\n\n{structured_criteria}"
            )
            criteria_sources.extend((template_source, *structured_sources))
            template_criteria_testable = is_testable_criterion(original_criteria)
            diagnostics.append(
                ArtifactMergeDiagnostic(
                    code=(
                        "template_criteria_augmented"
                        if template_criteria_testable
                        else "weak_template_criteria_augmented"
                    ),
                    severity="info" if template_criteria_testable else "warning",
                    slot="acceptance_criteria",
                    sources=(template_source, *structured_sources),
                    resolution=(
                        "Критерии шаблона сохранены; профильный и универсальный минимум добавлен."
                        if template_criteria_testable
                        else "Слабые критерии шаблона сохранены для контекста и усилены проверяемым минимумом."
                    ),
                )
            )
        else:
            project.enrichment["validation_criteria"] = structured_criteria
            criteria_sources.extend(structured_sources)
    else:
        fallback_source = template_source or "draft"
        artifact_sources.append(fallback_source)
        criteria_sources.append(fallback_source)
        diagnostics.append(
            ArtifactMergeDiagnostic(
                code="draft_artifact_contract_unresolved",
                severity="warning",
                slot="contract",
                sources=(fallback_source,),
                resolution="Нет профильного контракта и уверенного archetype skeleton; требуется методолог.",
            )
        )

    contract_sources = list(structured_sources)
    if template_source:
        contract_sources.insert(0, template_source)
    if not contract_sources:
        contract_sources.append("draft")
    project.artifact_contract = merged
    project.artifact_contract_sources = tuple(dict.fromkeys(contract_sources))
    project.artifact_slot_sources = {
        "artifact": tuple(dict.fromkeys(artifact_sources)),
        "acceptance_criteria": tuple(dict.fromkeys(criteria_sources)),
        "deliverables": structured_sources,
        "evidence_requirements": structured_sources,
    }
    project.artifact_merge_diagnostics = tuple(diagnostics)
