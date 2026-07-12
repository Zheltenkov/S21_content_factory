"""View-model dataclasses for the compact UP constructor (Конструктор УП).

Pure data structures for the constructor state: stages, blockers, actions, brief
options, metrics, coverage, snapshot and the assembled state. Extracted from
``up_builder_state`` so the read-model loaders and state-derivation logic can import
these models as a leaf (no DB, no viewer imports). ``up_builder_state`` re-exports
them, so consumers are unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from content_factory.catalog.pipeline.curriculum import CurriculumDesignSpec

StageStatus = Literal["done", "active", "warn", "pending"]
ActionMethod = Literal["get", "post"]


@dataclass(frozen=True)
class BuilderAction:
    """One primary action the methodologist can take next."""

    label: str
    href: str
    method: ActionMethod = "get"
    hint: str = ""
    code: str = "open"


@dataclass(frozen=True)
class BuilderBlocker:
    """A visible reason why the plan cannot move forward."""

    title: str
    description: str
    action: BuilderAction | None = None


@dataclass(frozen=True)
class BuilderStage:
    """Compact workflow step used by the cockpit stepper."""

    number: int
    label: str
    description: str
    status: StageStatus

    @property
    def css_class(self) -> str:
        if self.status == "done":
            return "workflow-step-done"
        if self.status == "active":
            return "workflow-step-active"
        if self.status == "warn":
            return "workflow-step-warn"
        return "workflow-step-pending"

    @property
    def donut_caption(self) -> str:
        """Short status text that fits inside a narrow SVG segment."""

        replacements = {
            "загружен и разобран": "разобран",
            "нужно загрузить": "загрузить",
            "обрабатывается": "в работе",
            "ошибка обработки": "ошибка",
            "готов к применению": "готов",
            "ожидает обработки": "ожидает",
            "нужно построить": "построить",
            "заблокирован DAG": "нет DAG",
            "после справочника": "после справ.",
        }
        return replacements.get(self.description, self.description)


@dataclass(frozen=True)
class BriefOption:
    """Recent brief option in the constructor switcher."""

    brief_id: int
    role: str
    domain: str
    created_at: str | None


@dataclass(frozen=True)
class BuilderMetric:
    """Small label/value pair for compact constructor summaries."""

    label: str
    value: str


@dataclass(frozen=True)
class BuilderCoverageRow:
    """One compact required-area coverage row shown before skill review."""

    area: str
    status: str
    matched_candidates: str
    dropped_candidates: str
    rationale: str

    @property
    def status_label(self) -> str:
        if self.status == "covered":
            return "закрыто"
        if self.status == "partial":
            return "частично"
        return "не закрыто"

    @property
    def status_class(self) -> str:
        return "status-badge" if self.status == "covered" else "status-badge warn"


@dataclass(frozen=True)
class BuilderBriefAnalysis:
    """Methodological analysis extracted from the intake result payload."""

    metrics: list[BuilderMetric]
    program_goal: str
    must_include_areas: list[str]
    coverage_metrics: list[BuilderMetric]
    coverage_rows: list[BuilderCoverageRow]
    sub_queries: list[str]
    atomize_metrics: list[BuilderMetric]
    normalize_metrics: list[BuilderMetric]

    @property
    def available(self) -> bool:
        return bool(
            self.metrics
            or self.program_goal
            or self.must_include_areas
            or self.coverage_metrics
            or self.coverage_rows
            or self.sub_queries
            or self.atomize_metrics
            or self.normalize_metrics
        )


@dataclass(frozen=True)
class BuilderSkillCandidate:
    """One atomic skill prepared for a compact human review in the constructor."""

    suggestion_id: int
    name: str
    source_name: str
    group: str
    coverage_area: str
    bloom: str
    tools: str
    parent_name: str
    decision: str
    resolution: str
    nearest_skill_id: int | None
    nearest_name: str
    nearest_group: str
    match_score: str
    novelty_score: str
    confidence: str
    council_agreement: str
    reasons: str
    recommendation_code: str
    recommendation_label: str
    recommendation_target: str
    recommendation_detail: str
    similarity_recommendation: str

    @property
    def decision_label(self) -> str:
        labels = {
            "accepted": "Принято",
            "needs_review": "Требует решения",
            "rejected": "Отклонено",
            "pending": "Ожидает решения",
            "superseded": "Заменено",
        }
        return labels.get(self.decision, self.decision or "Ожидает решения")

    @property
    def is_open(self) -> bool:
        return self.decision in {"needs_review", "pending"}

    @property
    def is_accepted(self) -> bool:
        return self.decision == "accepted"


@dataclass(frozen=True)
class BuilderTemplateProposal:
    """One reusable artifact template proposed for human review."""

    proposal_id: int
    status: str
    title: str
    artifact_family: str
    scope_type: str
    scope_names: tuple[str, ...]
    artifact_description: str
    project_name_pattern: str
    materials_pattern: str
    storytelling_pattern: str
    validation_criteria: str
    covered_skill_names: tuple[str, ...]
    rationale: str
    confidence: float
    accepted_template_id: int | None

    @property
    def family_label(self) -> str:
        labels = {
            "analysis": "Аналитический вывод",
            "document": "Комплект документов",
            "configuration": "Конфигурация",
            "design": "Проектное решение",
            "production": "Рабочий продукт",
            "practice": "Практическая работа",
        }
        return labels.get(self.artifact_family, self.artifact_family or "Артефакт")

    @property
    def status_label(self) -> str:
        return {
            "open": "Требует решения",
            "accepted": "Принято",
            "rejected": "Отклонено",
        }.get(self.status, self.status)

    @property
    def is_open(self) -> bool:
        return self.status == "open"

    @property
    def is_accepted(self) -> bool:
        return self.status == "accepted"


@dataclass(frozen=True)
class BuilderSnapshot:
    """Raw workflow facts before deriving UI actions."""

    brief_id: int | None = None
    role: str = ""
    domain: str = ""
    seniority: str = ""
    created_at: str | None = None
    latest_job_id: int | None = None
    latest_job_status: str = ""
    latest_job_stage: str = ""
    latest_job_source: str = ""
    plan_id: int | None = None
    plan_status: str = ""
    plan_row_count: int = 0
    plan_design_current: bool = False
    total_suggestions: int = 0
    accepted_atomic_count: int = 0
    pending_atomic_count: int = 0
    open_skill_reviews: int = 0
    open_edge_reviews: int = 0
    active_promotions: int = 0
    skill_set_items: int = 0
    catalog_applied: bool = False
    dag_status: str = ""
    dag_message: str = ""
    dag_nodes: int = 0
    dag_order_count: int = 0
    template_total: int = 0
    template_open: int = 0
    template_accepted: int = 0
    template_rejected: int = 0
    brief_analysis: BuilderBriefAnalysis | None = None
    skill_candidates: tuple[BuilderSkillCandidate, ...] = ()
    template_proposals: tuple[BuilderTemplateProposal, ...] = ()
    design_spec: CurriculumDesignSpec | None = None

    @property
    def dag_valid(self) -> bool:
        return self.dag_nodes > 0 and self.dag_order_count > 0

    @property
    def open_candidates(self) -> tuple[BuilderSkillCandidate, ...]:
        return tuple(candidate for candidate in self.skill_candidates if candidate.is_open)

    @property
    def accepted_candidates(self) -> tuple[BuilderSkillCandidate, ...]:
        return tuple(candidate for candidate in self.skill_candidates if candidate.is_accepted)

    @property
    def rejected_candidates(self) -> tuple[BuilderSkillCandidate, ...]:
        return tuple(candidate for candidate in self.skill_candidates if candidate.decision == "rejected")

    @property
    def open_template_proposals(self) -> tuple[BuilderTemplateProposal, ...]:
        return tuple(proposal for proposal in self.template_proposals if proposal.is_open)

    @property
    def accepted_template_proposals(self) -> tuple[BuilderTemplateProposal, ...]:
        return tuple(proposal for proposal in self.template_proposals if proposal.is_accepted)

    @property
    def rejected_template_proposals(self) -> tuple[BuilderTemplateProposal, ...]:
        return tuple(proposal for proposal in self.template_proposals if proposal.status == "rejected")


@dataclass(frozen=True)
class CurriculumBuilderState:
    """Complete view model for the UP constructor cockpit."""

    snapshot: BuilderSnapshot
    recent_briefs: list[BriefOption]
    stages: list[BuilderStage]
    next_action: BuilderAction | None
    blockers: list[BuilderBlocker]

    @property
    def has_brief(self) -> bool:
        return self.snapshot.brief_id is not None

    @property
    def progress_percent(self) -> int:
        if not self.stages:
            return 0
        completed = sum(1 for stage in self.stages if stage.status == "done")
        return int(round((completed / len(self.stages)) * 100))
