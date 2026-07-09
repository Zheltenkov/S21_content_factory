"""Typed contracts for audit corpus evaluation."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field


class CorpusEvaluationKey(BaseModel, frozen=True):
    """Ключ сравнения: один проект и один критерий."""

    project_id: str
    criterion: str


class GoldCorpusItem(BaseModel):
    """Одна эталонная строка после нормализации Excel."""

    row_number: int
    raw_project: str
    matched_project: str
    project_id: str
    raw_problem: str
    details: str
    criteria: list[str]


class GoldCorpusCase(BaseModel):
    """Один атомарный эталонный случай: проект, критерий и конкретная строка/описание."""

    case_id: str
    row_number: int
    raw_project: str
    matched_project: str
    project_id: str
    criterion: str
    line_start: int | None = None
    line_end: int | None = None
    gold_text: str
    file_hint: str | None = None


class PredictedCorpusItem(BaseModel):
    """Один найденный алгоритмом случай в формате, удобном для сопоставления."""

    finding_id: str
    project_id: str
    project: str
    criterion: str
    checker_name: str
    line_start: int | None = None
    line_end: int | None = None
    file_path: str | None = None
    severity: str | None = None
    verdict: str | None = None
    confidence: float | None = None
    issue_type: str | None = None
    found_text: str


class CorpusEvaluationMatch(BaseModel):
    """Главная строка оценки: эталонная ошибка и сопоставленная найденная ошибка."""

    project: str
    project_id: str
    criterion: str
    label: str
    gold_row_number: int
    gold_line_range: str
    gold_text: str
    found_finding_id: str | None = None
    found_checker: str | None = None
    found_line_range: str
    found_text: str
    match_type: str
    match_score: float
    counted: bool
    reason: str


class CriterionMetrics(BaseModel):
    """Метрики по одному критерию."""

    criterion: str
    label: str
    gold_total: int
    predicted_total: int
    true_positive: int
    false_positive: int
    false_negative: int
    precision: float
    recall: float
    f1_score: float


class PredictionSliceMetrics(BaseModel):
    """Метрики по срезу найденных ошибок: чекер, группа чекеров или действенный слой."""

    slice_name: str
    label: str
    predicted_total: int
    true_positive: int
    false_positive: int
    precision: float
    false_positive_share: float


class CostQualityMetrics(BaseModel):
    """Связка стоимости модельных проверок с качеством результата."""

    model_calls: int
    cache_hits: int
    total_tokens: int
    cost_usd: float
    cost_per_gold_true_positive: float | None = None
    cost_per_prediction: float | None = None
    cost_per_actionable_true_positive: float | None = None


class FalseNegativeAnalysisItem(BaseModel):
    """Одна пропущенная эталонная ошибка с причиной пропуска и следующим шагом."""

    project: str
    project_id: str
    criterion: str
    label: str
    gold_line_range: str
    gold_text: str
    nearest_finding_id: str | None = None
    nearest_checker: str | None = None
    nearest_match_type: str
    nearest_score: float
    reason_code: str
    reason_label: str
    next_step: str


class CorpusEvaluationSummary(BaseModel):
    """Итог оценки по корпусу проектов."""

    evaluated_criteria: list[str]
    gold_total: int
    predicted_total: int
    true_positive: int
    false_positive: int
    false_negative: int
    precision: float
    recall: float
    f1_score: float
    macro_precision: float
    macro_recall: float
    macro_f1_score: float
    overview_gold_total: int
    overview_predicted_total: int
    overview_true_positive: int
    overview_false_positive: int
    overview_false_negative: int
    overview_precision: float
    overview_recall: float
    overview_f1_score: float
    overview_macro_precision: float
    overview_macro_recall: float
    overview_macro_f1_score: float
    gold_scope_predicted_total: int
    gold_scope_true_positive: int
    gold_scope_false_positive: int
    gold_scope_false_negative: int
    gold_scope_precision: float
    gold_scope_recall: float
    gold_scope_f1_score: float
    gold_scope_macro_precision: float
    gold_scope_macro_recall: float
    gold_scope_macro_f1_score: float
    per_criterion: list[CriterionMetrics]
    overview_per_criterion: list[CriterionMetrics]
    checker_metrics: list[PredictionSliceMetrics] = Field(default_factory=list)
    checker_group_metrics: list[PredictionSliceMetrics] = Field(default_factory=list)
    actionable_metrics: PredictionSliceMetrics | None = None
    cost_quality: CostQualityMetrics | None = None
    false_negative_reason_counts: dict[str, int] = Field(default_factory=dict)
    false_negative_analysis: list[FalseNegativeAnalysisItem] = Field(default_factory=list)
    gold_items: list[GoldCorpusItem]
    gold_cases: list[GoldCorpusCase]
    matches: list[CorpusEvaluationMatch]
    detailed_false_positive_items: list[PredictedCorpusItem]
    false_positive_items: list[CorpusEvaluationKey]
    false_negative_items: list[CorpusEvaluationKey]
    project_mapping: dict[str, str]
    notes: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ProjectCandidate:
    """Normalized project identity used to match gold rows to audit units."""

    project_id: str
    raw_name: str
    normalized_name: str
    tokens: frozenset[str]


@dataclass(frozen=True)
class MatchCandidate:
    """Intermediate score for one gold/prediction pair."""

    gold_case_id: str
    prediction_id: str
    match_type: str
    score: float
    reason: str


_ProjectCandidate = ProjectCandidate
_MatchCandidate = MatchCandidate
