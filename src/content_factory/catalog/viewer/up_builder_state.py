"""Curriculum-constructor state model for the compact UP cockpit.

The constructor UI should not infer workflow state from scattered template
conditions. This module builds one view model from catalog facts: intake job,
reviews, catalog apply state, DAG payload, template proposals, and plan rows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, cast

from content_factory.catalog.db import CatalogConnection
from content_factory.catalog.pipeline.artifact_templates import load_curriculum_artifact_template_proposals
from content_factory.catalog.pipeline.curriculum import CurriculumDesignSpec
from content_factory.catalog.viewer._common import fetch_all, fetch_one, table_exists
from content_factory.catalog.viewer.curriculum_ops import get_curriculum_plan
from content_factory.catalog.viewer.intake_dag import count_brief_template_proposals, get_brief_catalog_apply_state
from content_factory.catalog.viewer.intake_reviews import count_open_prerequisite_edge_reviews_for_brief
from content_factory.catalog.viewer.intake_workspace import hydrate_job_result_payload

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


def load_curriculum_builder_state(
    conn: CatalogConnection,
    *,
    brief_id: int | None = None,
    job_id: int | None = None,
    recent_limit: int = 8,
    select_latest: bool = False,
) -> CurriculumBuilderState:
    """Load constructor state for a selected or latest brief."""

    recent_briefs = _load_recent_briefs(conn, recent_limit)
    if job_id is not None:
        snapshot = _load_snapshot_by_job(conn, job_id)
    else:
        selected_id = brief_id if brief_id is not None else (recent_briefs[0].brief_id if select_latest and recent_briefs else None)
        snapshot = _load_snapshot(conn, selected_id) if selected_id is not None else BuilderSnapshot()
    return derive_curriculum_builder_state(snapshot, recent_briefs)


def derive_curriculum_builder_state(
    snapshot: BuilderSnapshot,
    recent_briefs: list[BriefOption] | None = None,
) -> CurriculumBuilderState:
    """Derive stages, blockers, and one primary next action from raw facts."""

    recent = recent_briefs or []
    stages = _build_stages(snapshot)
    blockers = _build_blockers(snapshot)
    next_action = _choose_next_action(snapshot)
    return CurriculumBuilderState(snapshot=snapshot, recent_briefs=recent, stages=stages, next_action=next_action, blockers=blockers)


def _load_recent_briefs(conn: CatalogConnection, limit: int) -> list[BriefOption]:
    if not table_exists(conn, "profile_brief"):
        return []
    rows = fetch_all(
        conn,
        """
        SELECT id, role, domain, created_at
        FROM profile_brief
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [
        BriefOption(
            brief_id=int(row["id"]),
            role=str(row.get("role") or "Без роли"),
            domain=str(row.get("domain") or "Без домена"),
            created_at=str(row.get("created_at")) if row.get("created_at") else None,
        )
        for row in rows
    ]


def _load_snapshot(conn: CatalogConnection, brief_id: int) -> BuilderSnapshot:
    brief = _load_brief(conn, brief_id)
    latest_job = _load_latest_job(conn, brief_id)
    latest_job_payload = _as_dict(latest_job.get("result_payload") if latest_job else None)
    latest_job_payload = hydrate_job_result_payload(conn, latest_job_payload) or latest_job_payload
    plan = _load_latest_plan(conn, brief_id)
    suggestions = _count_skill_suggestions(conn, brief_id)
    review_counts = _count_skill_reviews(conn, brief_id)
    open_edge_reviews = count_open_prerequisite_edge_reviews_for_brief(conn, brief_id)
    catalog_state = get_brief_catalog_apply_state(conn, brief_id)
    proposal_counts = count_brief_template_proposals(conn, brief_id)
    template_proposals = _build_template_proposals(load_curriculum_artifact_template_proposals(conn, brief_id))
    dag_payload = _as_dict(latest_job_payload.get("dag"))
    persisted = _as_dict(latest_job_payload.get("persisted"))
    brief_analysis = _build_brief_analysis(latest_job_payload)
    skill_candidates = _build_skill_candidates(latest_job_payload.get("candidates"))
    candidate_review_count = sum(1 for candidate in skill_candidates if candidate.is_open)

    active_promotions = _to_int(catalog_state.get("active_promotions"))
    skill_set_items = _to_int(catalog_state.get("skill_set_items"))
    catalog_applied = (
        active_promotions > 0
        or skill_set_items > 0
        or _to_int(persisted.get("catalog_promoted")) > 0
        or proposal_counts["total"] > 0
        or (plan is not None and int(plan.get("id") or 0) > 0)
        or str(dag_payload.get("status") or "") == "catalog_applied"
    )
    order = dag_payload.get("order")
    order_count = len(order) if isinstance(order, list) else 0
    design_spec = _load_design_spec(conn, brief_id, dag_payload) if order_count else None
    plan_design = _as_dict(plan.get("design_spec") if plan else None)
    plan_design_current = bool(
        design_spec
        and design_spec.ready
        and plan_design.get("approved") is True
        and str(plan_design.get("design_hash") or "") == design_spec.design_hash
    )

    return BuilderSnapshot(
        brief_id=brief_id,
        role=str(brief.get("role") or ""),
        domain=str(brief.get("domain") or ""),
        seniority=str(brief.get("seniority") or ""),
        created_at=str(brief.get("created_at")) if brief.get("created_at") else None,
        latest_job_id=_to_optional_int(latest_job.get("id") if latest_job else None),
        latest_job_status=str(latest_job.get("status") or "") if latest_job else "",
        latest_job_stage=str(latest_job.get("current_stage") or "") if latest_job else "",
        latest_job_source=str(latest_job.get("source_name") or "") if latest_job else "",
        plan_id=_to_optional_int(plan.get("id") if plan else None),
        plan_status=str(plan.get("status") or "") if plan else "",
        plan_row_count=_to_int(plan.get("row_count") if plan else 0),
        plan_design_current=plan_design_current,
        total_suggestions=suggestions["total"],
        accepted_atomic_count=suggestions["accepted_atomic"],
        pending_atomic_count=suggestions["pending_atomic"],
        open_skill_reviews=max(review_counts["open"], candidate_review_count),
        open_edge_reviews=open_edge_reviews,
        active_promotions=active_promotions,
        skill_set_items=skill_set_items,
        catalog_applied=catalog_applied,
        dag_status=str(dag_payload.get("status") or ""),
        dag_message=str(dag_payload.get("message") or ""),
        dag_nodes=_to_int(dag_payload.get("nodes")),
        dag_order_count=order_count,
        template_total=proposal_counts["total"],
        template_open=proposal_counts["open"],
        template_accepted=proposal_counts["accepted"],
        template_rejected=proposal_counts["rejected"],
        brief_analysis=brief_analysis,
        skill_candidates=tuple(skill_candidates),
        template_proposals=tuple(template_proposals),
        design_spec=design_spec,
    )


def _load_design_spec(
    conn: CatalogConnection,
    brief_id: int,
    dag_payload: dict[str, Any],
) -> CurriculumDesignSpec | None:
    from content_factory.catalog.pipeline import stage_dag_to_up
    from content_factory.catalog.pipeline.curriculum import build_curriculum_design_spec
    from content_factory.catalog.viewer.intake_dag import load_accepted_skill_candidates, load_brief_spec_for_plan

    candidates, _ = load_accepted_skill_candidates(conn, brief_id)
    if not candidates:
        return None
    nodes = stage_dag_to_up.build_plan_nodes(candidates)
    return build_curriculum_design_spec(load_brief_spec_for_plan(conn, brief_id), nodes, dag_payload)


def _load_snapshot_by_job(conn: CatalogConnection, job_id: int) -> BuilderSnapshot:
    if not table_exists(conn, "intake_job"):
        return BuilderSnapshot()
    job = fetch_one(
        conn,
        """
        SELECT id, status, current_stage, source_name, result_payload, created_at
        FROM intake_job
        WHERE id = ?
        """,
        (job_id,),
    )
    if not job:
        return BuilderSnapshot()

    payload = _loads_json(job.get("result_payload"))
    brief_id = _to_optional_int(payload.get("brief_id"))
    if brief_id is not None:
        return _load_snapshot(conn, brief_id)

    return BuilderSnapshot(
        latest_job_id=_to_optional_int(job.get("id")),
        latest_job_status=str(job.get("status") or ""),
        latest_job_stage=str(job.get("current_stage") or ""),
        latest_job_source=str(job.get("source_name") or ""),
        created_at=str(job.get("created_at")) if job.get("created_at") else None,
    )


def _load_brief(conn: CatalogConnection, brief_id: int) -> dict[str, Any]:
    if not table_exists(conn, "profile_brief"):
        return {}
    return fetch_one(
        conn,
        """
        SELECT id, role, seniority, domain, created_at
        FROM profile_brief
        WHERE id = ?
        """,
        (brief_id,),
    ) or {}


def _load_latest_job(conn: CatalogConnection, brief_id: int) -> dict[str, Any] | None:
    if not table_exists(conn, "intake_job"):
        return None
    row = fetch_one(
        conn,
        """
        SELECT id, status, current_stage, source_name, result_payload, created_at, updated_at
        FROM intake_job
        WHERE json_valid(result_payload)
          AND CAST(json_extract(result_payload, '$.brief_id') AS INTEGER) = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (brief_id,),
    )
    if not row:
        return None
    row["result_payload"] = _loads_json(row.get("result_payload"))
    return row


def _load_latest_plan(conn: CatalogConnection, brief_id: int) -> dict[str, Any] | None:
    if not table_exists(conn, "curriculum_plan"):
        return None
    row = fetch_one(
        conn,
        """
        SELECT id
        FROM curriculum_plan
        WHERE brief_id = ?
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (brief_id,),
    )
    if not row:
        return None
    return cast(dict[str, Any], get_curriculum_plan(conn, int(row["id"])))


def _count_skill_suggestions(conn: CatalogConnection, brief_id: int) -> dict[str, int]:
    if not table_exists(conn, "skill_suggestion"):
        return {"total": 0, "accepted_atomic": 0, "pending_atomic": 0}
    row = fetch_one(
        conn,
        """
        SELECT
            COUNT(*) AS total_count,
            SUM(CASE WHEN entity_type = 'skill' AND atomicity = 'atomic' AND decision = 'accepted' THEN 1 ELSE 0 END)
                AS accepted_atomic_count,
            SUM(CASE WHEN entity_type = 'skill' AND atomicity = 'atomic' AND decision = 'needs_review' THEN 1 ELSE 0 END)
                AS pending_atomic_count
        FROM skill_suggestion
        WHERE brief_id = ?
        """,
        (brief_id,),
    )
    if not row:
        return {"total": 0, "accepted_atomic": 0, "pending_atomic": 0}
    return {
        "total": _to_int(row.get("total_count")),
        "accepted_atomic": _to_int(row.get("accepted_atomic_count")),
        "pending_atomic": _to_int(row.get("pending_atomic_count")),
    }


def _count_skill_reviews(conn: CatalogConnection, brief_id: int) -> dict[str, int]:
    if not table_exists(conn, "review_queue"):
        return {"open": 0}
    row = fetch_one(
        conn,
        """
        SELECT SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_count
        FROM review_queue
        WHERE source_ref = ?
          AND entity_type = 'skill'
          AND NOT (
              json_valid(details)
              AND json_extract(details, '$.review_kind') = 'prerequisite_edge'
          )
        """,
        (f"brief:{brief_id}",),
    )
    return {"open": _to_int(row.get("open_count") if row else 0)}


def _build_stages(snapshot: BuilderSnapshot) -> list[BuilderStage]:
    brief_status: StageStatus = "active"
    brief_description = "нужно загрузить"
    if snapshot.brief_id:
        brief_status = "done"
        brief_description = "загружен и разобран"
    elif snapshot.latest_job_id and snapshot.latest_job_status in {"pending", "running"}:
        brief_status = "active"
        brief_description = "обрабатывается"
    elif snapshot.latest_job_id and snapshot.latest_job_status == "failed":
        brief_status = "warn"
        brief_description = "ошибка обработки"

    skill_review_status: StageStatus = "pending"
    if snapshot.open_skill_reviews > 0:
        skill_review_status = "active"
    elif snapshot.total_suggestions > 0:
        skill_review_status = "done"

    catalog_status: StageStatus = "pending"
    if snapshot.catalog_applied:
        catalog_status = "done"
    elif skill_review_status == "done":
        catalog_status = "active"

    dag_status: StageStatus = "pending"
    if snapshot.dag_valid and snapshot.open_edge_reviews == 0:
        dag_status = "done"
    elif snapshot.dag_valid and snapshot.open_edge_reviews > 0:
        dag_status = "active"
    elif snapshot.catalog_applied:
        dag_status = "warn"

    design_status: StageStatus = "pending"
    if snapshot.open_edge_reviews > 0:
        design_status = "pending"
    elif snapshot.design_spec and snapshot.design_spec.ready:
        design_status = "done"
    elif snapshot.design_spec and snapshot.design_spec.uncovered_required_areas:
        design_status = "warn"
    elif snapshot.dag_valid and snapshot.design_spec:
        design_status = "active"

    template_status: StageStatus = "pending"
    if snapshot.template_open > 0 and design_status == "done":
        template_status = "active"
    elif snapshot.template_accepted > 0:
        template_status = "done" if design_status == "done" else "warn"
    elif design_status == "done" and snapshot.plan_id is not None:
        template_status = "active"

    plan_status: StageStatus = "pending"
    if snapshot.plan_row_count > 0:
        plan_status = "done" if design_status == "done" and snapshot.plan_design_current else "warn"
    elif snapshot.template_accepted > 0 and not snapshot.dag_valid:
        plan_status = "warn"
    elif snapshot.template_accepted > 0 and snapshot.dag_valid:
        plan_status = "active"

    return [
        BuilderStage(1, "Бриф", brief_description, brief_status),
        BuilderStage(2, "Навыки", _skill_stage_description(snapshot), skill_review_status),
        BuilderStage(3, "Справочник", _catalog_stage_description(snapshot), catalog_status),
        BuilderStage(4, "DAG", _dag_stage_description(snapshot), dag_status),
        BuilderStage(5, "Каркас", _design_stage_description(snapshot), design_status),
        BuilderStage(6, "Шаблоны", _template_stage_description(snapshot), template_status),
        BuilderStage(7, "УП", _plan_stage_description(snapshot), plan_status),
    ]


def _build_blockers(snapshot: BuilderSnapshot) -> list[BuilderBlocker]:
    blockers: list[BuilderBlocker] = []
    if snapshot.open_skill_reviews > 0:
        blockers.append(
            BuilderBlocker(
                title="Открытые решения по навыкам",
                description=f"Осталось принять, связать или отклонить навыки: {snapshot.open_skill_reviews}.",
                action=_skill_review_action(snapshot),
            )
        )
    if snapshot.open_edge_reviews > 0:
        blockers.append(
            BuilderBlocker(
                title="Связи DAG требуют решения",
                description=f"Осталось проверить зависимостей между навыками: {snapshot.open_edge_reviews}.",
                action=_edge_review_action(snapshot),
            )
        )
    if snapshot.dag_valid and snapshot.design_spec and not snapshot.design_spec.ready:
        if snapshot.design_spec.uncovered_required_areas:
            description = "Не покрыты обязательные области: " + ", ".join(
                snapshot.design_spec.uncovered_required_areas
            )
        else:
            description = "Проверьте этапы, итоговый проект и открытые вопросы брифа, затем примите каркас."
        blockers.append(
            BuilderBlocker(
                title="Каркас программы требует решения",
                description=description,
                action=_design_review_action(snapshot),
            )
        )
    if snapshot.dag_valid and snapshot.template_open > 0:
        blockers.append(
            BuilderBlocker(
                title="Шаблоны УП требуют решения",
                description=f"Открыто шаблонов: {snapshot.template_open}. Примите нужные и отклоните лишние.",
                action=_template_review_action(snapshot),
            )
        )
    if snapshot.template_accepted > 0 and snapshot.plan_row_count == 0 and not snapshot.dag_valid:
        blockers.append(
            BuilderBlocker(
                title="Шаблоны приняты раньше DAG",
                description="Система сохранила решения по шаблонам, но не смогла собрать строки УП без DAG.",
            )
        )
    if (
        snapshot.plan_row_count > 0
        and snapshot.design_spec
        and snapshot.design_spec.ready
        and not snapshot.plan_design_current
    ):
        blockers.append(
            BuilderBlocker(
                title="УП собран по предыдущему каркасу",
                description="Принятый каркас изменился. Пересоберите УП, чтобы этапы и итоговый проект попали в строки плана.",
                action=_build_plan_action(snapshot),
            )
        )
    return blockers


def _choose_next_action(snapshot: BuilderSnapshot) -> BuilderAction | None:
    if snapshot.brief_id is None:
        if snapshot.latest_job_id and snapshot.latest_job_status in {"pending", "running"}:
            return BuilderAction(
                "Дождаться обработки",
                f"/app/curriculum?job_id={snapshot.latest_job_id}",
                hint="Intake-задача выполняется; конструктор обновится после завершения.",
                code="wait_job",
            )
        if snapshot.latest_job_id and snapshot.latest_job_status == "failed":
            return BuilderAction(
                "Загрузить бриф заново",
                "#curriculum-brief-form",
                hint="Предыдущая обработка завершилась ошибкой. Проверьте текст или выберите другой файл.",
                code="upload_brief",
            )
        return BuilderAction(
            "Загрузить бриф",
            "#curriculum-brief-form",
            hint="Вставьте текст или выберите файл, затем запустите обработку вручную.",
            code="upload_brief",
        )
    if snapshot.latest_job_status in {"pending", "running"}:
        return _job_action(snapshot, "Открыть обработку брифа")
    if snapshot.open_skill_reviews > 0:
        return _skill_review_action(snapshot)
    if not snapshot.catalog_applied and snapshot.accepted_atomic_count > 0:
        return _apply_catalog_action(snapshot)
    if snapshot.catalog_applied and not snapshot.dag_valid:
        return _build_dag_action(snapshot)
    if snapshot.open_edge_reviews > 0:
        return _edge_review_action(snapshot)
    if snapshot.dag_valid and snapshot.design_spec and not snapshot.design_spec.ready:
        return _design_review_action(snapshot)
    if snapshot.dag_valid and snapshot.template_total == 0:
        return _generate_templates_action(snapshot)
    if snapshot.template_open > 0:
        return _template_review_action(snapshot)
    if snapshot.template_accepted > 0 and (
        snapshot.plan_row_count == 0 or not snapshot.plan_design_current
    ):
        return _build_plan_action(snapshot)
    if snapshot.template_total > 0 and snapshot.template_accepted == 0 and snapshot.plan_row_count == 0:
        return _generate_templates_action(snapshot)
    if snapshot.plan_row_count > 0:
        return _plan_action(snapshot)
    return _job_action(snapshot, "Открыть рабочий стол брифа")


def _skill_review_action(snapshot: BuilderSnapshot) -> BuilderAction:
    if snapshot.brief_id is not None:
        return BuilderAction(
            "Проверить навыки",
            f"/app/curriculum?brief_id={snapshot.brief_id}#skills-review",
            code="review_skills",
        )
    return _job_action(snapshot, "Проверить навыки", anchor="#candidate-review")


def _edge_review_action(snapshot: BuilderSnapshot) -> BuilderAction:
    return BuilderAction(
        "Проверить связи DAG",
        "/app/spravochnik/reviews?status=open&entity_type=prerequisite_edge",
        hint="Примите или отклоните предложенные зависимости перед утверждением каркаса.",
        code="review_dag_edges",
    )


def _apply_catalog_action(snapshot: BuilderSnapshot) -> BuilderAction | None:
    if snapshot.latest_job_id is None:
        return None
    return BuilderAction(
        "Применить навыки в справочник",
        f"/app/curriculum/jobs/{snapshot.latest_job_id}/apply-catalog",
        method="post",
        hint="Создаст набор навыков брифа и предложения шаблонов УП.",
        code="apply_catalog",
    )


def _build_dag_action(snapshot: BuilderSnapshot) -> BuilderAction | None:
    if snapshot.latest_job_id is None:
        return None
    return BuilderAction(
        "Построить DAG",
        f"/app/curriculum/jobs/{snapshot.latest_job_id}/build-dag",
        method="post",
        hint="Пересчитает зависимости и попробует собрать строки УП.",
        code="build_dag",
    )


def _generate_templates_action(snapshot: BuilderSnapshot) -> BuilderAction | None:
    if snapshot.plan_id is None:
        return None
    return BuilderAction(
        "Сгенерировать шаблоны УП",
        f"/app/curriculum/plans/{snapshot.plan_id}/template-proposals/generate",
        method="post",
        code="generate_templates",
    )


def _design_review_action(snapshot: BuilderSnapshot) -> BuilderAction | None:
    if snapshot.brief_id is None:
        return None
    return BuilderAction(
        "Проверить каркас программы",
        f"/app/curriculum?brief_id={snapshot.brief_id}#program-design",
        code="review_design",
    )


def _template_review_action(snapshot: BuilderSnapshot) -> BuilderAction | None:
    if snapshot.brief_id is None:
        return None
    return BuilderAction(
        "Проверить шаблоны УП",
        f"/app/curriculum?brief_id={snapshot.brief_id}#template-review",
        code="review_templates",
    )


def _build_plan_action(snapshot: BuilderSnapshot) -> BuilderAction | None:
    if snapshot.brief_id is None:
        return None
    label = "Пересобрать УП" if snapshot.plan_row_count > 0 else "Собрать УП"
    return BuilderAction(
        label,
        f"/app/curriculum/briefs/{snapshot.brief_id}/build-plan",
        method="post",
        hint="Соберёт строки УП из валидного DAG и принятых шаблонов.",
        code="build_plan",
    )


def _plan_action(snapshot: BuilderSnapshot) -> BuilderAction | None:
    if snapshot.plan_id is None:
        return None
    return BuilderAction("Открыть УП", f"/app/spravochnik/up/plans/{snapshot.plan_id}", code="open_plan")


def _job_action(snapshot: BuilderSnapshot, label: str, *, anchor: str = "") -> BuilderAction:
    if snapshot.latest_job_id is None:
        return BuilderAction(label, "/app/curriculum", code="open_job")
    builder_anchor = "#skills-review" if anchor == "#candidate-review" else anchor
    return BuilderAction(
        label,
        f"/app/curriculum?job_id={snapshot.latest_job_id}{builder_anchor}",
        code="open_job",
    )


def _skill_stage_description(snapshot: BuilderSnapshot) -> str:
    if snapshot.open_skill_reviews:
        return f"открыто {snapshot.open_skill_reviews}"
    if snapshot.total_suggestions:
        return f"принято {snapshot.accepted_atomic_count}"
    return "ожидает обработки"


def _catalog_stage_description(snapshot: BuilderSnapshot) -> str:
    if snapshot.catalog_applied:
        return f"набор {snapshot.skill_set_items}"
    if snapshot.open_skill_reviews:
        return "после навыков"
    if snapshot.accepted_atomic_count:
        return "готов к применению"
    return "после проверки"


def _dag_stage_description(snapshot: BuilderSnapshot) -> str:
    if snapshot.open_edge_reviews:
        return f"связей на проверке {snapshot.open_edge_reviews}"
    if snapshot.dag_valid:
        return f"узлов {snapshot.dag_nodes}"
    if snapshot.catalog_applied:
        return "нужно построить"
    return "после справочника"


def _design_stage_description(snapshot: BuilderSnapshot) -> str:
    if snapshot.design_spec and snapshot.design_spec.ready:
        return f"этапов {len(snapshot.design_spec.stages)}"
    if snapshot.design_spec and snapshot.design_spec.uncovered_required_areas:
        return f"не покрыто {len(snapshot.design_spec.uncovered_required_areas)}"
    if snapshot.design_spec:
        return "нужно принять"
    return "после DAG"


def _template_stage_description(snapshot: BuilderSnapshot) -> str:
    if snapshot.template_open:
        return f"открыто {snapshot.template_open}"
    if snapshot.template_accepted:
        return f"принято {snapshot.template_accepted}"
    if snapshot.template_total:
        return f"всего {snapshot.template_total}"
    return "после DAG"


def _plan_stage_description(snapshot: BuilderSnapshot) -> str:
    if snapshot.plan_row_count:
        if snapshot.design_spec and snapshot.design_spec.ready and not snapshot.plan_design_current:
            return "нужно пересобрать"
        return f"строк {snapshot.plan_row_count}"
    if snapshot.template_accepted and not snapshot.dag_valid:
        return "заблокирован DAG"
    return "пока пуст"


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
