"""Typed contracts for offline generation quality evaluation."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models.criteria_models import CriteriaReport


class EvalThresholds(BaseModel):
    """Pass/fail thresholds for one golden project or a whole run."""

    model_config = ConfigDict(extra="forbid")

    min_total_score: int | None = Field(default=None, ge=0)
    min_score_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    min_structure_pass_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    min_practice_atomicity: float = Field(default=0.0, ge=0.0, le=1.0)
    min_didactics_compliance: float = Field(default=0.0, ge=0.0, le=1.0)
    max_hallucinated_tools: int | None = Field(default=None, ge=0)
    max_retry_count: int | None = Field(default=None, ge=0)
    max_fallback_count: int | None = Field(default=None, ge=0)
    max_fallback_policy_violations: int | None = Field(default=None, ge=0)
    max_cost_usd: float | None = Field(default=None, ge=0.0)
    max_latency_ms: float | None = Field(default=None, ge=0.0)


class GoldenProjectExpectations(BaseModel):
    """Expected observable properties for one project in a golden set."""

    model_config = ConfigDict(extra="forbid")

    required_chapters: list[int] = Field(default_factory=lambda: [1, 2, 3])
    required_task_count: int | None = Field(default=None, ge=0)
    required_task_titles: list[str] = Field(default_factory=list)
    required_criteria_ids: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    rubric_thresholds: EvalThresholds = Field(default_factory=EvalThresholds)

    @model_validator(mode="after")
    def normalize_lists(self) -> "GoldenProjectExpectations":
        """Normalize textual lists while preserving input order."""
        self.required_task_titles = _unique_non_empty(self.required_task_titles)
        self.required_criteria_ids = _unique_non_empty(self.required_criteria_ids)
        self.required_tools = _unique_non_empty(self.required_tools)
        self.allowed_tools = _unique_non_empty(self.allowed_tools)
        self.forbidden_tools = _unique_non_empty(self.forbidden_tools)
        self.required_chapters = sorted({int(chapter) for chapter in self.required_chapters if int(chapter) > 0})
        return self


class GoldenProjectCase(BaseModel):
    """One dataset item with source seed and expected quality contract."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    seed: dict[str, Any] = Field(default_factory=dict)
    expectations: GoldenProjectExpectations = Field(default_factory=GoldenProjectExpectations)
    reference_markdown: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_case(self) -> "GoldenProjectCase":
        """Keep IDs and tags stable for joins and filtering."""
        self.id = self.id.strip()
        self.title = self.title.strip()
        self.tags = _unique_non_empty(self.tags)
        return self


class GoldenDataset(BaseModel):
    """A versioned golden set used for offline model/prompt evaluation."""

    model_config = ConfigDict(extra="forbid")

    name: str = "golden-projects"
    version: str = "v1"
    cases: list[GoldenProjectCase] = Field(default_factory=list)
    defaults: EvalThresholds = Field(default_factory=EvalThresholds)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def ensure_unique_case_ids(self) -> "GoldenDataset":
        """Prevent silent joins against duplicate golden case IDs."""
        ids = [case.id for case in self.cases]
        duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
        if duplicates:
            raise ValueError(f"Duplicate golden case ids: {', '.join(duplicates)}")
        return self


class GeneratedProjectOutput(BaseModel):
    """One generated artifact and optional runtime telemetry for evaluation."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    case_id: str = Field(min_length=1)
    markdown: str = Field(min_length=1)
    model_name: str | None = Field(default=None, validation_alias="model", serialization_alias="model")
    provider: str | None = None
    run_id: str | None = None
    rubric_report: CriteriaReport | None = None
    node_traces: list[dict[str, Any]] = Field(default_factory=list)
    llm_traces: list[dict[str, Any]] = Field(default_factory=list)
    fallback_traces: list[dict[str, Any]] = Field(default_factory=list)
    retry_count: int = Field(default=0, ge=0)
    fallback_count: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0.0)
    latency_ms: float | None = Field(default=None, ge=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvalMetricBreakdown(BaseModel):
    """Computed metrics for one evaluated generated README."""

    model_config = ConfigDict(extra="forbid")

    score_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    rubric_total: int = 0
    rubric_max_score: int = 0
    structure_pass_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    practice_atomicity: float = Field(default=0.0, ge=0.0, le=1.0)
    didactics_compliance: float = Field(default=0.0, ge=0.0, le=1.0)
    hallucinated_tools: list[str] = Field(default_factory=list)
    missing_required_tools: list[str] = Field(default_factory=list)
    missing_required_chapters: list[int] = Field(default_factory=list)
    missing_required_task_titles: list[str] = Field(default_factory=list)
    failed_required_criteria: list[str] = Field(default_factory=list)
    task_count: int = 0
    retry_count: int = 0
    fallback_count: int = 0
    fallback_policy_violations: list[str] = Field(default_factory=list)
    cost_usd: float = 0.0
    latency_ms: float = 0.0


class EvalCaseResult(BaseModel):
    """Evaluation result for one golden case/output pair."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    case_id: str
    title: str
    passed: bool
    metrics: EvalMetricBreakdown = Field(default_factory=EvalMetricBreakdown)
    reasons: list[str] = Field(default_factory=list)
    model_name: str | None = Field(default=None, validation_alias="model", serialization_alias="model")
    provider: str | None = None
    run_id: str | None = None
    error: str | None = None


class EvalRunSummary(BaseModel):
    """Aggregated report for a model/prompt evaluation run."""

    model_config = ConfigDict(extra="forbid")

    dataset_name: str
    dataset_version: str
    total_cases: int
    passed_cases: int
    pass_rate: float = Field(ge=0.0, le=1.0)
    average_score_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    average_structure_pass_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    average_practice_atomicity: float = Field(default=0.0, ge=0.0, le=1.0)
    average_didactics_compliance: float = Field(default=0.0, ge=0.0, le=1.0)
    total_cost_usd: float = 0.0
    total_latency_ms: float = 0.0
    total_retry_count: int = 0
    total_fallback_count: int = 0
    results: list[EvalCaseResult] = Field(default_factory=list)


def _unique_non_empty(values: list[str]) -> list[str]:
    """Return non-empty unique strings with source order preserved."""
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = str(value).strip()
        key = text.casefold()
        if text and key not in seen:
            normalized.append(text)
            seen.add(key)
    return normalized
