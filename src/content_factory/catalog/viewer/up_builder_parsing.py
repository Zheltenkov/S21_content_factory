"""Payload parsers for the UP-constructor state (raw JSON facts -> view models).

Pure functions that turn intake-job / plan / DAG JSON payloads into the typed view
models. No DB and no viewer imports — a leaf that imports only the models, so the
read-model loaders and state-derivation logic in ``up_builder_state`` can import it.
``up_builder_state`` re-exports the shared parsers, so its loaders/derivation and the
tests are unchanged.
"""

from __future__ import annotations

import json
from typing import Any

from content_factory.catalog.viewer.up_builder_models import (
    BuilderBriefAnalysis,
    BuilderCoverageRow,
    BuilderMetric,
    BuilderSkillCandidate,
    BuilderTemplateProposal,
)


def _loads_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _build_brief_analysis(payload: dict[str, Any]) -> BuilderBriefAnalysis | None:
    spec = _as_dict(payload.get("spec"))
    coverage = _as_dict(payload.get("coverage"))
    atomize = _as_dict(payload.get("atomize"))
    normalize = _as_dict(payload.get("normalize"))
    if not spec and not coverage and not atomize and not normalize:
        return None

    analysis = BuilderBriefAnalysis(
        metrics=[
            metric
            for metric in [
                _metric("Тип", spec.get("artifact_type")),
                _metric("Роль", spec.get("role")),
                _metric("Грейд", spec.get("seniority")),
                _metric("Домен", spec.get("domain")),
                _metric("Оператор", spec.get("operator_role")),
            ]
            if metric is not None
        ],
        program_goal=_to_text(spec.get("program_goal")),
        must_include_areas=_list_text(spec.get("must_include_areas"), limit=16),
        coverage_metrics=[
            metric
            for metric in [
                _metric("Закрыто", coverage.get("covered_count")),
                _metric("Частично", coverage.get("partial_count")),
                _metric("Не закрыто", coverage.get("uncovered_count")),
            ]
            if metric is not None
        ],
        coverage_rows=_build_coverage_rows(coverage.get("rows")),
        sub_queries=_list_text(spec.get("sub_queries"), limit=8),
        atomize_metrics=[
            metric
            for metric in [
                _metric("Кандидатов", atomize.get("raw_count")),
                _metric("Атомарных", atomize.get("atomic_count")),
                _metric("Композитов", atomize.get("composite_count")),
                _metric("Не-навыков", atomize.get("non_skill_count")),
            ]
            if metric is not None
        ],
        normalize_metrics=[
            metric
            for metric in [
                _metric("До слияния", normalize.get("atomic_input_count")),
                _metric("После", normalize.get("atomic_output_count")),
                _metric("Дублей", normalize.get("merged_count")),
                _metric("Передроблений", normalize.get("compacted_count")),
            ]
            if metric is not None
        ],
    )
    return analysis if analysis.available else None


def _build_skill_candidates(value: Any) -> list[BuilderSkillCandidate]:
    """Convert hydrated intake candidates into a stable constructor view model."""

    if not isinstance(value, list):
        return []
    candidates: list[BuilderSkillCandidate] = []
    for raw_candidate in value:
        candidate = _as_dict(raw_candidate)
        if candidate.get("entity_type") != "skill" or candidate.get("atomicity") != "atomic":
            continue
        suggestion_id = _to_int(candidate.get("suggestion_id"))
        if suggestion_id <= 0:
            continue
        recommendation = _as_dict(candidate.get("recommended_action"))
        similarity_hint = _as_dict(candidate.get("similarity_hint"))
        candidates.append(
            BuilderSkillCandidate(
                suggestion_id=suggestion_id,
                name=_to_text(candidate.get("name")) or "Навык без названия",
                source_name=_to_text(candidate.get("source_name")),
                group=_to_text(candidate.get("group")),
                coverage_area=_to_text(candidate.get("coverage_area")),
                bloom=_to_text(candidate.get("bloom")),
                tools=_join_text_or_scalar(candidate.get("tools")),
                parent_name=_to_text(candidate.get("parent_name")),
                decision=_to_text(candidate.get("decision")) or "pending",
                resolution=_to_text(candidate.get("resolution")),
                nearest_skill_id=_to_optional_int(candidate.get("nearest_skill_id")),
                nearest_name=_to_text(candidate.get("nearest_name")),
                nearest_group=_to_text(candidate.get("nearest_group")),
                match_score=_to_text(candidate.get("match_score")) or "—",
                novelty_score=_to_text(candidate.get("novelty_score")) or "—",
                confidence=_to_text(candidate.get("confidence")) or "—",
                council_agreement=_to_text(candidate.get("council_agreement")) or "—",
                reasons=_join_text_or_scalar(candidate.get("reasons")) or "Причины не указаны",
                recommendation_code=_to_text(recommendation.get("code")) or "review",
                recommendation_label=_to_text(recommendation.get("label")) or "Нужно решение методолога",
                recommendation_target=_to_text(recommendation.get("target")),
                recommendation_detail=_to_text(recommendation.get("detail")),
                similarity_recommendation=_to_text(similarity_hint.get("recommendation")),
            )
        )
    return sorted(candidates, key=lambda item: (not item.is_open, item.name.casefold(), item.suggestion_id))


def _build_template_proposals(value: Any) -> list[BuilderTemplateProposal]:
    """Convert stored template proposals into a stable constructor view model."""

    if not isinstance(value, list):
        return []
    proposals: list[BuilderTemplateProposal] = []
    for raw_proposal in value:
        proposal = _as_dict(raw_proposal)
        proposal_id = _to_int(proposal.get("id"))
        if proposal_id <= 0:
            continue
        try:
            confidence = float(proposal.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        proposals.append(
            BuilderTemplateProposal(
                proposal_id=proposal_id,
                status=_to_text(proposal.get("status")) or "open",
                title=_to_text(proposal.get("title")) or "Шаблон УП",
                artifact_family=_to_text(proposal.get("artifact_family")) or "practice",
                scope_type=_to_text(proposal.get("scope_type")) or "coverage_area",
                scope_names=tuple(_list_text(proposal.get("scope_names"), limit=16)),
                artifact_description=_to_text(proposal.get("artifact_description")),
                project_name_pattern=_to_text(proposal.get("project_name_pattern")),
                materials_pattern=_to_text(proposal.get("materials_pattern")),
                storytelling_pattern=_to_text(proposal.get("storytelling_pattern")),
                validation_criteria=_to_text(proposal.get("validation_criteria")),
                covered_skill_names=tuple(_list_text(proposal.get("covered_skill_names"), limit=24)),
                rationale=_to_text(proposal.get("rationale")),
                confidence=max(0.0, min(1.0, confidence)),
                accepted_template_id=_to_optional_int(proposal.get("accepted_template_id")),
                repeatable=bool(proposal.get("repeatable")),
                published_at=_to_text(proposal.get("published_at")) or None,
            )
        )
    return sorted(proposals, key=lambda item: (not item.is_open, item.title.casefold(), item.proposal_id))


def _build_coverage_rows(value: Any) -> list[BuilderCoverageRow]:
    if not isinstance(value, list):
        return []
    rows: list[BuilderCoverageRow] = []
    for raw_row in value[:10]:
        row = _as_dict(raw_row)
        rows.append(
            BuilderCoverageRow(
                area=_to_text(row.get("area")) or "Область без названия",
                status=_to_text(row.get("status")) or "uncovered",
                matched_candidates=_join_text(row.get("candidate_names")),
                dropped_candidates=_join_text(row.get("dropped_candidate_names")),
                rationale=_to_text(row.get("rationale")) or "—",
            )
        )
    return rows


def _metric(label: str, value: Any) -> BuilderMetric | None:
    text = _to_text(value)
    if not text:
        return None
    return BuilderMetric(label=label, value=text)


def _list_text(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = _to_text(item)
        if text:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def _join_text(value: Any) -> str:
    items = _list_text(value, limit=8)
    return ", ".join(items) if items else "—"


def _join_text_or_scalar(value: Any) -> str:
    if isinstance(value, list | tuple):
        return ", ".join(_to_text(item) for item in value if _to_text(item))
    return _to_text(value)


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _to_optional_int(value: Any) -> int | None:
    parsed = _to_int(value)
    return parsed if parsed > 0 else None
