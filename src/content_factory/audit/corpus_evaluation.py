"""Оценка качества аудита на корпусе проектов с Excel-разметкой."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from content_factory.audit import corpus_evaluation_exports as _corpus_evaluation_exports
from content_factory.audit import corpus_evaluation_false_negatives as _false_negatives
from content_factory.audit import corpus_evaluation_gold as _corpus_evaluation_gold
from content_factory.audit import corpus_evaluation_matching as _corpus_evaluation_matching
from content_factory.audit import corpus_evaluation_metrics as _metrics
from content_factory.audit.corpus_evaluation_models import (
    CorpusEvaluationKey,
    CorpusEvaluationSummary,
    GoldCorpusItem,
    PredictedCorpusItem,
    _ProjectCandidate,
)
from content_factory.audit.corpus_evaluation_models import (
    CorpusEvaluationMatch as CorpusEvaluationMatch,
)
from content_factory.audit.corpus_evaluation_models import (
    GoldCorpusCase as GoldCorpusCase,
)
from content_factory.audit.domain import AuditReport, Finding

_split_gold_detail_cases = _corpus_evaluation_gold._split_gold_detail_cases
_classify_false_negative = _false_negatives.classify_false_negative
_false_negative_analysis = _false_negatives.false_negative_analysis
_false_negative_reason_counts = _false_negatives.false_negative_reason_counts
_match_gold_cases = _corpus_evaluation_matching.match_gold_cases


def evaluate_corpus_report(
    report: AuditReport,
    gold_xlsx_path: Path,
    *,
    matcher: str = "strict",
    judge_backend: str = "offline",
    judge_model: str | None = None,
    judge_api_key: str | None = None,
    judge_topk: int = 6,
    judge_cache_path: str | None = None,
    defects_only: bool = False,
    confidence_floor: float = 0.0,
    mirror_dedupe: bool = False,
    cap_repetitive: int = 0,
) -> CorpusEvaluationSummary:
    """Сравнивает отчёт аудита с Excel-разметкой на уровне конкретных эталонных ошибок."""

    unit_candidates = _project_candidates_from_report(report)
    units_by_id = {unit.unit_id: unit for unit in report.units}
    from content_factory.audit import gold_atomic

    if gold_atomic.is_atomic(gold_xlsx_path):
        gold_items, gold_cases, opinion_case_ids, mapping_notes = gold_atomic.load_atomic(
            gold_xlsx_path, unit_candidates
        )
    else:
        gold_items, mapping_notes = load_gold_items(gold_xlsx_path, unit_candidates)
        gold_cases = _corpus_evaluation_gold.gold_cases_from_items(gold_items)
        opinion_case_ids = set()
    if defects_only:
        if opinion_case_ids:
            gold_cases = [case for case in gold_cases if case.case_id not in opinion_case_ids]
        else:
            from content_factory.audit.aligner import is_opinion

            gold_cases = [case for case in gold_cases if not is_opinion(case.gold_text)]
    predicted_items = _predicted_items_from_report(report, units_by_id)
    evaluated_criteria = sorted({item.criterion for item in gold_cases})
    predicted_items_in_scope = [
        item
        for item in predicted_items
        if item.criterion in evaluated_criteria and _is_strict_evaluation_signal(item)
    ]
    if confidence_floor > 0.0 or mirror_dedupe or cap_repetitive > 0 or matcher == "anchor_judge":
        from content_factory.audit import aligner

        predicted_items_in_scope = aligner.confidence_gate(predicted_items_in_scope, confidence_floor)
        if mirror_dedupe:
            predicted_items_in_scope = aligner.dedupe_mirror(predicted_items_in_scope)
        if cap_repetitive > 0:
            predicted_items_in_scope, _capped = aligner.cap_repetitive(
                predicted_items_in_scope, per_issue_type=cap_repetitive
            )
    if matcher == "anchor_judge":
        from content_factory.audit import aligner

        judge = aligner.build_judge(
            judge_backend,
            api_key=judge_api_key,
            model=judge_model,
            cache_path=judge_cache_path,
        )
        matches, matched_prediction_ids = aligner.match_anchor_judge(
            gold_cases, predicted_items_in_scope, judge, topk=judge_topk
        )
    else:
        matches, matched_prediction_ids = _match_gold_cases(gold_cases, predicted_items_in_scope)
    detailed_false_positive_items = [
        item for item in predicted_items_in_scope if item.finding_id not in matched_prediction_ids
    ]
    detailed_true_positive = sum(1 for item in matches if item.counted)
    detailed_false_negative = len(gold_cases) - detailed_true_positive
    detailed_false_positive = len(detailed_false_positive_items)
    per_criterion = _metrics.per_criterion_detail_metrics(gold_cases, predicted_items_in_scope, matches)
    actionable_metrics = _metrics.prediction_slice_metrics(
        "actionable",
        "Действенные находки",
        [item for item in predicted_items_in_scope if _metrics.is_actionable_prediction(item)],
        matched_prediction_ids,
        detailed_false_positive,
    )
    checker_metrics = _metrics.checker_metrics(predicted_items_in_scope, matched_prediction_ids, detailed_false_positive)
    checker_group_metrics = _metrics.checker_group_metrics(
        predicted_items_in_scope,
        matched_prediction_ids,
        detailed_false_positive,
    )
    false_negative_analysis = _false_negative_analysis(matches)
    false_negative_reason_counts = _false_negative_reason_counts(false_negative_analysis)
    cost_quality = _metrics.cost_quality_metrics(report, detailed_true_positive, actionable_metrics)

    overview_gold_keys = {
        CorpusEvaluationKey(project_id=item.project_id, criterion=criterion)
        for item in gold_items
        for criterion in item.criteria
    }
    overview_predicted_keys = _metrics.predicted_keys_from_report(report)

    overview_true_positive_keys = overview_gold_keys & overview_predicted_keys
    overview_false_positive_keys = overview_predicted_keys - overview_gold_keys
    overview_false_negative_keys = overview_gold_keys - overview_predicted_keys
    overview_per_criterion = _metrics.per_criterion_metrics(overview_gold_keys, overview_predicted_keys)

    return CorpusEvaluationSummary(
        evaluated_criteria=evaluated_criteria,
        gold_total=len(gold_cases),
        predicted_total=len(predicted_items_in_scope),
        true_positive=detailed_true_positive,
        false_positive=detailed_false_positive,
        false_negative=detailed_false_negative,
        precision=_metrics.safe_ratio(detailed_true_positive, detailed_true_positive + detailed_false_positive),
        recall=_metrics.safe_ratio(detailed_true_positive, detailed_true_positive + detailed_false_negative),
        f1_score=_metrics.f1(detailed_true_positive, detailed_false_positive, detailed_false_negative),
        macro_precision=_metrics.mean([item.precision for item in per_criterion]),
        macro_recall=_metrics.mean([item.recall for item in per_criterion]),
        macro_f1_score=_metrics.mean([item.f1_score for item in per_criterion]),
        overview_gold_total=len(overview_gold_keys),
        overview_predicted_total=len(overview_predicted_keys),
        overview_true_positive=len(overview_true_positive_keys),
        overview_false_positive=len(overview_false_positive_keys),
        overview_false_negative=len(overview_false_negative_keys),
        overview_precision=_metrics.safe_ratio(
            len(overview_true_positive_keys),
            len(overview_true_positive_keys) + len(overview_false_positive_keys),
        ),
        overview_recall=_metrics.safe_ratio(
            len(overview_true_positive_keys),
            len(overview_true_positive_keys) + len(overview_false_negative_keys),
        ),
        overview_f1_score=_metrics.f1(
            len(overview_true_positive_keys),
            len(overview_false_positive_keys),
            len(overview_false_negative_keys),
        ),
        overview_macro_precision=_metrics.mean([item.precision for item in overview_per_criterion]),
        overview_macro_recall=_metrics.mean([item.recall for item in overview_per_criterion]),
        overview_macro_f1_score=_metrics.mean([item.f1_score for item in overview_per_criterion]),
        gold_scope_predicted_total=len(predicted_items_in_scope),
        gold_scope_true_positive=detailed_true_positive,
        gold_scope_false_positive=detailed_false_positive,
        gold_scope_false_negative=detailed_false_negative,
        gold_scope_precision=_metrics.safe_ratio(
            detailed_true_positive,
            detailed_true_positive + detailed_false_positive,
        ),
        gold_scope_recall=_metrics.safe_ratio(
            detailed_true_positive,
            detailed_true_positive + detailed_false_negative,
        ),
        gold_scope_f1_score=_metrics.f1(
            detailed_true_positive,
            detailed_false_positive,
            detailed_false_negative,
        ),
        gold_scope_macro_precision=_metrics.mean([item.precision for item in per_criterion]),
        gold_scope_macro_recall=_metrics.mean([item.recall for item in per_criterion]),
        gold_scope_macro_f1_score=_metrics.mean([item.f1_score for item in per_criterion]),
        per_criterion=per_criterion,
        overview_per_criterion=overview_per_criterion,
        checker_metrics=checker_metrics,
        checker_group_metrics=checker_group_metrics,
        actionable_metrics=actionable_metrics,
        cost_quality=cost_quality,
        false_negative_reason_counts=false_negative_reason_counts,
        false_negative_analysis=false_negative_analysis,
        gold_items=gold_items,
        gold_cases=gold_cases,
        matches=matches,
        detailed_false_positive_items=sorted(
            detailed_false_positive_items,
            key=lambda item: (item.project_id, item.criterion, item.line_start or 0, item.finding_id),
        ),
        false_positive_items=sorted(overview_false_positive_keys, key=lambda item: (item.project_id, item.criterion)),
        false_negative_items=sorted(overview_false_negative_keys, key=lambda item: (item.project_id, item.criterion)),
        project_mapping={item.raw_project: item.matched_project for item in gold_items},
        notes=[
            "Основное сравнение выполняется на уровне атомарных ошибок: проект, критерий, строка/диапазон и текст.",
            "В строгую метрику не входят диагностические строки: низкоуверенные unknown, непроверенные ссылки и общие ресурсные предупреждения без имени файла.",
            "Старая метрика проект × критерий сохранена только как обзорная в overview_* полях.",
            "Excel-разметка нормализуется эвристически из колонок 'Проблема' и 'Детали'.",
            *mapping_notes,
        ],
    )


def load_gold_items(
    gold_xlsx_path: Path,
    unit_candidates: list[_ProjectCandidate],
) -> tuple[list[GoldCorpusItem], list[str]]:
    """Compatibility wrapper for gold corpus loading."""

    return _corpus_evaluation_gold.load_gold_items(gold_xlsx_path, unit_candidates)


def write_corpus_evaluation(summary: CorpusEvaluationSummary, output_dir: Path) -> None:
    """Writes JSON/CSV corpus evaluation artifacts through the export module."""

    _corpus_evaluation_exports.write_corpus_evaluation(summary, output_dir)


def _predicted_items_from_report(report: AuditReport, units_by_id: dict[str, Any]) -> list[PredictedCorpusItem]:
    """Преобразует Findings в атомарные найденные случаи для детальной оценки."""

    items: list[PredictedCorpusItem] = []
    for finding in report.findings:
        unit = units_by_id.get(finding.unit_id)
        if unit is None:
            continue
        location = finding.location
        items.append(
            PredictedCorpusItem(
                finding_id=finding.finding_id,
                project_id=finding.unit_id,
                project=unit.name,
                criterion=finding.criterion.value,
                checker_name=finding.checker_name,
                line_start=location.line_start if location else None,
                line_end=location.line_end if location else None,
                file_path=location.file_path if location else None,
                severity=finding.severity.value,
                verdict=finding.verdict.value,
                confidence=finding.confidence,
                issue_type=str(finding.extra.get("issue_type") or ""),
                found_text=_finding_text(finding),
            )
        )
    return items


def _is_strict_evaluation_signal(item: PredictedCorpusItem) -> bool:
    """Оставляет в строгой метрике только находки, похожие на проверяемый дефект."""

    if item.verdict == "pass":
        return False
    if item.verdict == "unknown" and (item.confidence or 0.0) < 0.8:
        return False
    if item.checker_name == "link_checker":
        return item.verdict == "fail"
    if item.checker_name == "resource_availability_checker":
        return item.issue_type in {"missing_local_resource", "unconfirmed_environment_path"}
    return True


def _finding_text(finding: Finding) -> str:
    """Собирает человекочитаемый текст найденной ошибки из цитаты, основания и рекомендации."""

    parts: list[str] = []
    if finding.quote:
        parts.append(str(finding.quote))
    for evidence in finding.evidence[:2]:
        if evidence.detail:
            parts.append(evidence.detail)
    if finding.recommendation:
        parts.append(finding.recommendation)
    issue_type = finding.extra.get("issue_type")
    if issue_type:
        parts.append(str(issue_type))
    return " | ".join(part.strip() for part in parts if part and part.strip())


def _project_candidates_from_report(report: AuditReport) -> list[_ProjectCandidate]:
    """Создаёт кандидаты сопоставления из единиц отчёта."""

    return [
        _ProjectCandidate(
            project_id=unit.unit_id,
            raw_name=unit.name,
            normalized_name=_corpus_evaluation_gold.normalize_project_name(unit.name),
            tokens=frozenset(_corpus_evaluation_gold.project_tokens(unit.name)),
        )
        for unit in report.units
    ]
