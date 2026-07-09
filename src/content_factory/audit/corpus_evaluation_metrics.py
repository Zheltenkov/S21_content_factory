"""Metric aggregation helpers for audit corpus evaluation."""

from __future__ import annotations

from content_factory.audit.corpus_evaluation_matching import criterion_label
from content_factory.audit.corpus_evaluation_models import (
    CorpusEvaluationKey,
    CorpusEvaluationMatch,
    CostQualityMetrics,
    CriterionMetrics,
    GoldCorpusCase,
    PredictedCorpusItem,
    PredictionSliceMetrics,
)
from content_factory.audit.domain import AuditReport

CHECKER_GROUPS: dict[str, str] = {
    "broken_url_syntax_checker": "deterministic_rules",
    "label_punctuation_checker": "deterministic_rules",
    "local_consistency_checker": "deterministic_rules",
    "markdown_structure_checker": "deterministic_rules",
    "spelling_wording_checker": "editorial_rules",
    "link_checker": "links_and_resources",
    "local_link_checker": "links_and_resources",
    "resource_availability_checker": "links_and_resources",
    "checklist_checker": "checklist_and_artifacts",
    "fact_checker_perplexity": "factcheck",
    "readme_fact_actuality_checker": "factcheck",
    "tech_freshness_checker": "factcheck",
    "dependency_freshness_checker": "factcheck",
    "curriculum_relevance_checker": "methodology",
    "market_fit_checker": "methodology",
    "model_rubric_checker": "methodology",
}

CHECKER_GROUP_LABELS: dict[str, str] = {
    "deterministic_rules": "Детерминированные правила",
    "editorial_rules": "Редакторские правила",
    "links_and_resources": "Ссылки и ресурсы",
    "checklist_and_artifacts": "Чек-лист и артефакты",
    "factcheck": "Фактчек и актуальность",
    "methodology": "Методические критерии",
    "other": "Прочие проверки",
}


def per_criterion_detail_metrics(
    gold_cases: list[GoldCorpusCase],
    predicted_items: list[PredictedCorpusItem],
    matches: list[CorpusEvaluationMatch],
) -> list[CriterionMetrics]:
    """Calculate detailed atomic-defect metrics by criterion."""

    criteria = sorted({item.criterion for item in gold_cases} | {item.criterion for item in predicted_items})
    matched_prediction_ids = {item.found_finding_id for item in matches if item.counted and item.found_finding_id}
    metrics: list[CriterionMetrics] = []
    for criterion in criteria:
        gold = [item for item in gold_cases if item.criterion == criterion]
        predicted = [item for item in predicted_items if item.criterion == criterion]
        true_positive = sum(1 for item in matches if item.criterion == criterion and item.counted)
        matched_predicted = [item for item in predicted if item.finding_id in matched_prediction_ids]
        false_positive = len(predicted) - len(matched_predicted)
        false_negative = len(gold) - true_positive
        metrics.append(
            CriterionMetrics(
                criterion=criterion,
                label=criterion_label(criterion),
                gold_total=len(gold),
                predicted_total=len(predicted),
                true_positive=true_positive,
                false_positive=false_positive,
                false_negative=false_negative,
                precision=safe_ratio(true_positive, true_positive + false_positive),
                recall=safe_ratio(true_positive, true_positive + false_negative),
                f1_score=f1(true_positive, false_positive, false_negative),
            )
        )
    return metrics


def checker_metrics(
    predicted_items: list[PredictedCorpusItem],
    matched_prediction_ids: set[str],
    total_false_positive: int,
) -> list[PredictionSliceMetrics]:
    """Calculate precision for each checker that produced a gold-scope finding."""

    checker_names = sorted({item.checker_name for item in predicted_items})
    return [
        prediction_slice_metrics(
            checker,
            checker,
            [item for item in predicted_items if item.checker_name == checker],
            matched_prediction_ids,
            total_false_positive,
        )
        for checker in checker_names
    ]


def checker_group_metrics(
    predicted_items: list[PredictedCorpusItem],
    matched_prediction_ids: set[str],
    total_false_positive: int,
) -> list[PredictionSliceMetrics]:
    """Calculate precision by checker group so different quality layers stay separate."""

    groups = sorted({checker_group(item.checker_name) for item in predicted_items})
    return [
        prediction_slice_metrics(
            group,
            CHECKER_GROUP_LABELS.get(group, group),
            [item for item in predicted_items if checker_group(item.checker_name) == group],
            matched_prediction_ids,
            total_false_positive,
        )
        for group in groups
    ]


def prediction_slice_metrics(
    slice_name: str,
    label: str,
    predicted_items: list[PredictedCorpusItem],
    matched_prediction_ids: set[str],
    total_false_positive: int,
) -> PredictionSliceMetrics:
    """Calculate precision for an arbitrary prediction slice."""

    true_positive = sum(1 for item in predicted_items if item.finding_id in matched_prediction_ids)
    false_positive = len(predicted_items) - true_positive
    return PredictionSliceMetrics(
        slice_name=slice_name,
        label=label,
        predicted_total=len(predicted_items),
        true_positive=true_positive,
        false_positive=false_positive,
        precision=safe_ratio(true_positive, true_positive + false_positive),
        false_positive_share=safe_ratio(false_positive, total_false_positive),
    )


def checker_group(checker_name: str) -> str:
    """Return the stable product-facing checker group."""

    return CHECKER_GROUPS.get(checker_name, "other")


def is_actionable_prediction(item: PredictedCorpusItem) -> bool:
    """Return whether a finding should enter the methodologist's working focus."""

    if item.verdict in {"pass", "unknown"}:
        return False
    if item.severity in {"critical", "major"}:
        return True
    if item.severity == "minor":
        return checker_group(item.checker_name) not in {"methodology", "factcheck"}
    return False


def cost_quality_metrics(
    report: AuditReport,
    true_positive: int,
    actionable_metrics: PredictionSliceMetrics,
) -> CostQualityMetrics:
    """Connect model-call cost to confirmed findings."""

    usage = report.summary.model_usage
    return CostQualityMetrics(
        model_calls=usage.calls_total,
        cache_hits=usage.cache_hits,
        total_tokens=usage.total_tokens,
        cost_usd=round(float(usage.cost_usd), 6),
        cost_per_gold_true_positive=safe_money_ratio(float(usage.cost_usd), true_positive),
        cost_per_prediction=safe_money_ratio(float(usage.cost_usd), report.summary.findings_total),
        cost_per_actionable_true_positive=safe_money_ratio(float(usage.cost_usd), actionable_metrics.true_positive),
    )


def predicted_keys_from_report(report: AuditReport) -> set[CorpusEvaluationKey]:
    """Return all project/criterion pairs that have at least one audit finding."""

    unit_ids = {unit.unit_id for unit in report.units}
    result: set[CorpusEvaluationKey] = set()
    for finding in report.findings:
        if finding.unit_id not in unit_ids:
            continue
        result.add(CorpusEvaluationKey(project_id=finding.unit_id, criterion=finding.criterion.value))
    return result


def per_criterion_metrics(
    gold_keys: set[CorpusEvaluationKey],
    predicted_keys: set[CorpusEvaluationKey],
) -> list[CriterionMetrics]:
    """Calculate overview project-by-criterion metrics."""

    criteria = sorted({item.criterion for item in gold_keys | predicted_keys})
    metrics: list[CriterionMetrics] = []
    for criterion in criteria:
        gold = {item for item in gold_keys if item.criterion == criterion}
        predicted = {item for item in predicted_keys if item.criterion == criterion}
        true_positive = len(gold & predicted)
        false_positive = len(predicted - gold)
        false_negative = len(gold - predicted)
        metrics.append(
            CriterionMetrics(
                criterion=criterion,
                label=criterion_label(criterion),
                gold_total=len(gold),
                predicted_total=len(predicted),
                true_positive=true_positive,
                false_positive=false_positive,
                false_negative=false_negative,
                precision=safe_ratio(true_positive, true_positive + false_positive),
                recall=safe_ratio(true_positive, true_positive + false_negative),
                f1_score=f1(true_positive, false_positive, false_negative),
            )
        )
    return metrics


def safe_ratio(numerator: int, denominator: int) -> float:
    """Divide safely and return zero for an empty denominator."""

    return round(numerator / denominator, 4) if denominator else 0.0


def safe_money_ratio(cost: float, denominator: int) -> float | None:
    """Calculate a monetary ratio and keep an explicit None for empty denominators."""

    return round(cost / denominator, 6) if denominator else None


def f1(true_positive: int, false_positive: int, false_negative: int) -> float:
    """Calculate F1 from precision and recall."""

    precision = safe_ratio(true_positive, true_positive + false_positive)
    recall = safe_ratio(true_positive, true_positive + false_negative)
    return round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0


def mean(values: list[float]) -> float:
    """Calculate a stable macro metric mean."""

    return round(sum(values) / len(values), 4) if values else 0.0
