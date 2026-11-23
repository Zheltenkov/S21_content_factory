"""Bounded competency-agent workflow with deterministic validation and safe fallbacks."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from .competency_contracts import (
    CompetencyAssessmentEvidence,
    CompetencyCritique,
    CompetencyDesignResponse,
    CompetencyDraft,
    CompetencyEvidence,
    CompetencyPipelineTrace,
    CompetencyTableResult,
    CompetencyTableValidation,
    ProfessionalStandardMatch,
    ProfessionalStandardMatchResponse,
    ProfessionalStandardSelection,
    ProgramCompetencyPolicy,
    SourceCompetencyItem,
    policy_for_program_type,
)
from .competency_prompts import critic_prompt, designer_prompt, matcher_prompt
from .professional_standards import (
    ProfessionalStandardCatalog,
    ProfessionalStandardRecord,
    load_configured_professional_standard_catalog,
)
from .up_models import UpModule, UpPlan, UpRow

PROFSTANDARD_MATCH_PROMPT_VERSION = "rpd-profstandard-match-v1"
COMPETENCY_DESIGN_PROMPT_VERSION = "rpd-competency-design-v2"
COMPETENCY_CRITIC_PROMPT_VERSION = "rpd-competency-critic-v2"
COMPETENCY_REPAIR_PROMPT_VERSION = "rpd-competency-repair-v2"
PROFESSIONAL_STANDARD_REVIEW_MARKER = (
    "Требуется подтверждение методистом: укажите применимый профстандарт, "
    "квалификационные требования или анализ профессиональной деятельности"
)

_INTERNAL_PK_HEADER = [
    "Код",
    "Профессиональная компетенция",
    "Исходный уровень",
    "Качественное изменение",
    "После обучения слушатель способен",
    "Основание",
    "Оценка",
]


def build_competency_table(
    up: UpPlan,
    client: Any,
    *,
    profile_id: str,
    program_type: str,
    catalog: ProfessionalStandardCatalog | None = None,
    selection: ProfessionalStandardSelection | None = None,
) -> CompetencyTableResult:
    """Run matcher → designer → critic and return a profile-safe editable table."""
    if profile_id != "dpo_pk_v1":
        raise ValueError(f"Competency table contract is not registered for profile: {profile_id}")

    policy = policy_for_program_type(program_type)
    source_items = source_competency_items(up)
    resolved_catalog = catalog or load_configured_professional_standard_catalog()
    approved_codes = list((selection or ProfessionalStandardSelection()).approved_codes)
    candidates = _candidate_records(resolved_catalog, up, approved_codes)
    matches = _match_candidates(up, candidates, client)
    evidence = _allowed_evidence(source_items, resolved_catalog, approved_codes)

    drafts, status, issues, fallback_reason = _design_and_validate(
        up,
        policy,
        source_items,
        evidence,
        client,
    )
    rows = _rows_from_drafts(drafts, source_items, evidence, policy)
    covered = list(
        dict.fromkeys(
            source_id
            for draft in drafts
            for source_id in draft.covered_source_ids
            if source_id in {item.source_id for item in source_items}
        )
    )
    trace = CompetencyPipelineTrace(
        program_type=policy.program_type,
        family=policy.family,
        status=status,
        source_item_ids=[item.source_id for item in source_items],
        covered_source_ids=covered,
        catalog_sha256=resolved_catalog.source_sha256,
        catalog_load_error=resolved_catalog.load_error,
        candidate_standard_codes=[candidate.code for candidate in candidates],
        matches=matches,
        approved_standard_codes=approved_codes,
        validation_issues=issues,
        fallback_reason=fallback_reason,
    )
    return CompetencyTableResult(header=list(_INTERNAL_PK_HEADER), rows=rows, trace=trace)


def source_competency_items(up: UpPlan) -> list[SourceCompetencyItem]:
    """Aggregate duplicate UP skills while retaining module and outcome lineage."""
    order: list[str] = []
    names: dict[str, str] = {}
    modules: dict[str, list[str]] = defaultdict(list)
    outcomes: dict[str, list[str]] = defaultdict(list)
    assessments: dict[str, list[CompetencyAssessmentEvidence]] = defaultdict(list)
    for module_index, module in enumerate(up.modules, start=1):
        for row_index, row in enumerate(module.rows, start=1):
            row_outcomes = [value.strip() for value in (row.outcomes_can, row.outcomes_skills) if value.strip()]
            assessment = _assessment_evidence(module, row, module_index, row_index)
            for skill in row.skills:
                name = " ".join(skill.split()).strip()
                key = name.casefold()
                if not name:
                    continue
                if key not in names:
                    names[key] = name
                    order.append(key)
                if module.title and module.title not in modules[key]:
                    modules[key].append(module.title)
                for outcome in row_outcomes:
                    if outcome not in outcomes[key]:
                        outcomes[key].append(outcome)
                if all(item.assessment_id != assessment.assessment_id for item in assessments[key]):
                    assessments[key].append(assessment)
    return [
        SourceCompetencyItem(
            source_id=f"UP-SKILL-{index:03d}",
            name=names[key],
            modules=modules[key],
            outcomes=outcomes[key],
            assessments=assessments[key],
        )
        for index, key in enumerate(order, start=1)
    ]


def _assessment_evidence(
    module: UpModule,
    row: UpRow,
    module_index: int,
    row_index: int,
) -> CompetencyAssessmentEvidence:
    """Project one real UP row into a closed-world assessment option."""
    row_title = " ".join(row.name.split())
    module_title = " ".join(module.title.split())
    title = row_title or module_title
    subject = f"«{row_title}»" if row_title else f"по модулю «{module_title}»"
    if row.control() == "p2p-проверка":
        method = f"Практическое задание {subject} с взаимной проверкой"
    else:
        method = f"Защита проекта {subject}"
    return CompetencyAssessmentEvidence(
        assessment_id=f"UP-ASSESS-{module_index:03d}-{row_index:03d}",
        title=title,
        method=method,
    )


def validate_competency_table(
    header: list[str],
    rows: list[list[str]],
    trace: CompetencyPipelineTrace | None,
) -> CompetencyTableValidation:
    """Validate editable table invariants without forbidding deliberate row deletion."""
    family = trace.family if trace is not None else "ПК"
    schema = [] if header == _INTERNAL_PK_HEADER else ["header"]
    codes = [row[0].strip() if row else "" for row in rows]
    missing_codes = [str(index) for index, code in enumerate(codes, start=1) if not code]
    pattern = re.compile(rf"^{re.escape(family)}-\d+$")
    invalid_codes = [code for code in codes if code and not pattern.fullmatch(code)]
    duplicate_codes = sorted({code for code in codes if code and codes.count(code) > 1})
    missing_descriptions = [
        code or str(index)
        for index, (code, row) in enumerate(zip(codes, rows, strict=True), start=1)
        if len(row) < 2 or not row[1].strip()
    ]
    semantic_columns = (
        (2, "Исходный уровень"),
        (3, "Качественное изменение"),
        (4, "После обучения"),
        (6, "Оценка"),
    )
    missing_semantic_fields = [
        f"{code or index}:{label}"
        for index, (code, row) in enumerate(zip(codes, rows, strict=True), start=1)
        for column, label in semantic_columns
        if len(row) <= column or not row[column].strip()
    ]
    professional = trace is None or trace.program_type.value in {
        "dpo_pk",
        "dpo_retraining",
        "university",
    }
    basis_review_required = [
        code or str(index)
        for index, (code, row) in enumerate(zip(codes, rows, strict=True), start=1)
        if professional
        and (len(row) < 6 or not row[5].strip() or row[5].strip().startswith(PROFESSIONAL_STANDARD_REVIEW_MARKER))
    ]
    schema.extend(
        f"columns:row:{index}" for index, row in enumerate(rows, start=1) if len(row) != len(_INTERNAL_PK_HEADER)
    )
    return CompetencyTableValidation(
        schema_violations=schema,
        missing_codes=missing_codes,
        invalid_codes=invalid_codes,
        duplicate_codes=duplicate_codes,
        missing_descriptions=missing_descriptions,
        missing_semantic_fields=missing_semantic_fields,
        basis_review_required=basis_review_required,
    )


def _candidate_records(
    catalog: ProfessionalStandardCatalog,
    up: UpPlan,
    approved_codes: list[str],
) -> list[ProfessionalStandardRecord]:
    candidates = catalog.search(up)
    by_code = {candidate.code: candidate for candidate in candidates}
    for code in approved_codes:
        record = catalog.by_code(code)
        if record is not None:
            by_code.setdefault(code, record)
    return list(by_code.values())


def _match_candidates(
    up: UpPlan,
    candidates: list[ProfessionalStandardRecord],
    client: Any,
) -> list[ProfessionalStandardMatch]:
    if not candidates or not callable(getattr(client, "complete_structured", None)):
        return []
    system, user = matcher_prompt(up, candidates)
    response = _structured_call(
        client,
        ProfessionalStandardMatchResponse,
        system,
        user,
        role="rpd_profstandard_match",
        prompt_version=PROFSTANDARD_MATCH_PROMPT_VERSION,
    )
    if response is None:
        return []
    allowed_codes = {candidate.code for candidate in candidates}
    function_codes = {candidate.code: {function.code for function in candidate.functions} for candidate in candidates}
    result: list[ProfessionalStandardMatch] = []
    for match in response.matches:
        if match.standard_code not in allowed_codes:
            continue
        valid_functions = [
            code for code in match.relevant_function_codes if code in function_codes.get(match.standard_code, set())
        ]
        result.append(match.model_copy(update={"relevant_function_codes": valid_functions}))
    return result


def _allowed_evidence(
    source_items: list[SourceCompetencyItem],
    catalog: ProfessionalStandardCatalog,
    approved_codes: list[str],
) -> list[CompetencyEvidence]:
    evidence = [
        CompetencyEvidence(
            evidence_id=item.source_id,
            source="up",
            text=(
                f"Навык УП: {item.name}; модули: {'; '.join(item.modules) or 'не указаны'}; "
                f"результаты: {'; '.join(item.outcomes) or 'не указаны'}"
            ),
        )
        for item in source_items
    ]
    for code in approved_codes:
        standard = catalog.by_code(code)
        if standard is None:
            continue
        evidence.append(
            CompetencyEvidence(
                evidence_id=f"PS:{standard.code}",
                source="professional_standard",
                text=f"ПС {standard.code} «{standard.title}»",
            )
        )
        for function in standard.functions:
            details = "; ".join(function.required_skills[:8])
            evidence.append(
                CompetencyEvidence(
                    evidence_id=f"PS:{standard.code}:TF:{function.code}",
                    source="professional_standard",
                    text=(
                        f"ПС {standard.code} «{standard.title}», ТФ {function.code} «{function.title}»"
                        + (f"; необходимые умения: {details}" if details else "")
                    ),
                )
            )
    return evidence


def _design_and_validate(
    up: UpPlan,
    policy: ProgramCompetencyPolicy,
    source_items: list[SourceCompetencyItem],
    evidence: list[CompetencyEvidence],
    client: Any,
) -> tuple[list[CompetencyDraft], str, list[str], str]:
    if not source_items:
        return [], "fallback", [], "source_skills_missing"
    if not callable(getattr(client, "complete_structured", None)):
        return _fallback_drafts(source_items), "fallback", [], "structured_llm_unavailable"

    drafts = _design_call(up, policy, source_items, evidence, client)
    if drafts is None:
        return _fallback_drafts(source_items), "fallback", [], "designer_failed"
    issues = _draft_issues(drafts, source_items, evidence, policy)
    critique = _critique_call(policy, source_items, evidence, drafts, client) if not issues else None
    if critique is not None and not critique.valid:
        issues.extend(_critique_issues(critique))
    if not issues:
        return drafts, "validated", [], ""

    repaired = _design_call(
        up,
        policy,
        source_items,
        evidence,
        client,
        repair_issues=list(dict.fromkeys(issues)),
    )
    repaired_issues = (
        _draft_issues(repaired, source_items, evidence, policy) if repaired is not None else ["repair_failed"]
    )
    if repaired is not None and not repaired_issues:
        repaired_critique = _critique_call(policy, source_items, evidence, repaired, client)
        if repaired_critique is None or repaired_critique.valid:
            return repaired, "repaired", [], ""
        repaired_issues.extend(_critique_issues(repaired_critique))
    return (
        _fallback_drafts(source_items),
        "fallback",
        list(dict.fromkeys(repaired_issues or issues)),
        "validation_failed",
    )


def _design_call(
    up: UpPlan,
    policy: ProgramCompetencyPolicy,
    source_items: list[SourceCompetencyItem],
    evidence: list[CompetencyEvidence],
    client: Any,
    *,
    repair_issues: list[str] | None = None,
) -> list[CompetencyDraft] | None:
    system, user = designer_prompt(
        up,
        policy,
        source_items,
        evidence,
        repair_issues=repair_issues,
    )
    response = _structured_call(
        client,
        CompetencyDesignResponse,
        system,
        user,
        role="rpd_competency_design",
        prompt_version=(COMPETENCY_REPAIR_PROMPT_VERSION if repair_issues else COMPETENCY_DESIGN_PROMPT_VERSION),
    )
    return list(response.competencies) if response is not None else None


def _critique_call(
    policy: ProgramCompetencyPolicy,
    source_items: list[SourceCompetencyItem],
    evidence: list[CompetencyEvidence],
    drafts: list[CompetencyDraft],
    client: Any,
) -> CompetencyCritique | None:
    system, user = critic_prompt(policy, source_items, evidence, drafts)
    return _structured_call(
        client,
        CompetencyCritique,
        system,
        user,
        role="rpd_competency_validate",
        prompt_version=COMPETENCY_CRITIC_PROMPT_VERSION,
    )


def _structured_call(
    client: Any,
    output_model: type[Any],
    system: str,
    user: str,
    *,
    role: str,
    prompt_version: str,
) -> Any | None:
    try:
        result = client.complete_structured(
            output_model=output_model,
            system=system,
            user=user,
            llm_role=role,
            prompt_version=prompt_version,
        )
        return output_model.model_validate(result)
    except Exception:  # noqa: BLE001 - deterministic fallback is an explicit recovery policy
        return None


def _draft_issues(
    drafts: list[CompetencyDraft],
    source_items: list[SourceCompetencyItem],
    evidence: list[CompetencyEvidence],
    policy: ProgramCompetencyPolicy,
) -> list[str]:
    issues: list[str] = []
    if not drafts:
        issues.append("competencies_empty")
    if len(drafts) > policy.max_competencies:
        issues.append(f"competencies_over_limit:{len(drafts)}")
    allowed_sources = {item.source_id for item in source_items}
    allowed_evidence = {item.evidence_id for item in evidence}
    covered = {source_id for draft in drafts for source_id in draft.covered_source_ids}
    missing = sorted(allowed_sources - covered)
    if missing:
        issues.append("uncovered:" + ",".join(missing))
    unsupported_sources = sorted(covered - allowed_sources)
    if unsupported_sources:
        issues.append("unsupported_source_ids:" + ",".join(unsupported_sources))
    referenced = {evidence_id for draft in drafts for evidence_id in draft.evidence_refs}
    unsupported_evidence = sorted(referenced - allowed_evidence)
    if unsupported_evidence:
        issues.append("unsupported_evidence:" + ",".join(unsupported_evidence))
    assessment_by_source = {
        item.source_id: {assessment.assessment_id for assessment in item.assessments} for item in source_items
    }
    allowed_assessments = set().union(*assessment_by_source.values()) if assessment_by_source else set()
    referenced_assessments = {assessment_id for draft in drafts for assessment_id in draft.assessment_refs}
    unsupported_assessments = sorted(referenced_assessments - allowed_assessments)
    if unsupported_assessments:
        issues.append("unsupported_assessment_refs:" + ",".join(unsupported_assessments))
    for index, draft in enumerate(drafts, start=1):
        linked_assessments = set().union(
            *(assessment_by_source.get(source_id, set()) for source_id in draft.covered_source_ids)
        )
        unrelated = sorted(set(draft.assessment_refs) - linked_assessments)
        if unrelated:
            issues.append(f"assessment_source_mismatch:{index}:" + ",".join(unrelated))
    titles = [" ".join(draft.title.casefold().split()) for draft in drafts if draft.title.strip()]
    if len(titles) != len(set(titles)):
        issues.append("duplicate_titles")
    if any(not draft.title.strip() or not draft.after_training.strip() for draft in drafts):
        issues.append("required_text_missing")
    if any(not draft.baseline.strip() for draft in drafts):
        issues.append("baseline_missing")
    if any(not draft.qualitative_change.strip() for draft in drafts):
        issues.append("qualitative_change_missing")
    if any(not draft.assessment.strip() or not draft.assessment_refs for draft in drafts):
        issues.append("assessment_missing")
    return issues


def _critique_issues(critique: CompetencyCritique) -> list[str]:
    return list(
        dict.fromkeys(
            [
                *critique.issues,
                *(f"uncovered:{value}" for value in critique.uncovered_source_ids),
                *(f"unsupported_evidence:{value}" for value in critique.unsupported_evidence_refs),
                *(f"duplicate:{value}" for value in critique.duplicate_pairs),
            ]
        )
    )


def _fallback_drafts(source_items: list[SourceCompetencyItem]) -> list[CompetencyDraft]:
    drafts: list[CompetencyDraft] = []
    for item in source_items:
        after_training = "; ".join(item.outcomes) or f"Применяет навык «{item.name}» в практической задаче"
        observable_result = _fallback_observable_result(item, after_training)
        assessment_refs = [item.assessments[0].assessment_id] if item.assessments else []
        assessment = item.assessments[0].method if item.assessments else "Практическое задание"
        drafts.append(
            CompetencyDraft(
                title=item.name,
                baseline=(
                    f"Выполняет отдельные действия по направлению «{item.name}» по образцу, "
                    "без устойчивого алгоритма и самостоятельной проверки результата"
                ),
                qualitative_change=(
                    "Переходит от отдельных действий по образцу к самостоятельному достижению "
                    f"проверяемого результата: {observable_result}"
                ),
                after_training=after_training,
                assessment=assessment,
                assessment_refs=assessment_refs,
                covered_source_ids=[item.source_id],
                evidence_refs=[item.source_id],
            )
        )
    return drafts


def _fallback_observable_result(item: SourceCompetencyItem, after_training: str) -> str:
    """Prefer a substantive UP outcome over tautological skill-demonstration boilerplate."""
    parts = [part.strip() for part in re.split(r"[\n.;]+", after_training) if part.strip()]
    substantive = next(
        (part for part in parts if not part.casefold().startswith(("демонстрирует навык", "применяет навык"))),
        "",
    )
    return substantive or (
        f"самостоятельно применяет «{item.name}» в практической задаче, обосновывает действия и проверяет результат"
    )


def _rows_from_drafts(
    drafts: list[CompetencyDraft],
    source_items: list[SourceCompetencyItem],
    evidence: list[CompetencyEvidence],
    policy: ProgramCompetencyPolicy,
) -> list[list[str]]:
    evidence_by_id = {item.evidence_id: item for item in evidence}
    assessment_by_id = {
        assessment.assessment_id: assessment.method for item in source_items for assessment in item.assessments
    }
    rows: list[list[str]] = []
    for index, draft in enumerate(drafts, start=1):
        professional_refs = [
            evidence_by_id[ref].text
            for ref in draft.evidence_refs
            if ref in evidence_by_id and evidence_by_id[ref].source == "professional_standard"
        ]
        if professional_refs:
            basis = "; ".join(dict.fromkeys(professional_refs))
        elif policy.professional_standard_applicable:
            basis = PROFESSIONAL_STANDARD_REVIEW_MARKER
        else:
            basis = "Цели и планируемые результаты учебного плана"
        grounded_assessment = "; ".join(
            dict.fromkeys(assessment_by_id[ref] for ref in draft.assessment_refs if ref in assessment_by_id)
        )
        rows.append(
            [
                f"{policy.family}-{index}",
                draft.title.strip(),
                draft.baseline.strip(),
                draft.qualitative_change.strip(),
                draft.after_training.strip(),
                basis,
                grounded_assessment or draft.assessment.strip(),
            ]
        )
    return rows


__all__ = [
    "COMPETENCY_CRITIC_PROMPT_VERSION",
    "COMPETENCY_DESIGN_PROMPT_VERSION",
    "COMPETENCY_REPAIR_PROMPT_VERSION",
    "PROFESSIONAL_STANDARD_REVIEW_MARKER",
    "PROFSTANDARD_MATCH_PROMPT_VERSION",
    "build_competency_table",
    "source_competency_items",
    "validate_competency_table",
]
