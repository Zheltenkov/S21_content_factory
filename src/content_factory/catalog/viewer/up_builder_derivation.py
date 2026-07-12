"""State derivation for the UP constructor: raw snapshot -> stages, blockers, action.

Pure logic that turns a ``BuilderSnapshot`` (assembled by the repository loaders) into
the derived view: stage list + statuses, blockers, and the single primary next action.
No DB and no viewer imports — a leaf that imports only the view models, so it composes
with the loaders in ``up_builder_state``. ``up_builder_state`` re-exports
``derive_curriculum_builder_state`` (its loader entry and the tests call it), so
consumers are unchanged.
"""

from __future__ import annotations

from content_factory.catalog.viewer.up_builder_models import (
    BriefOption,
    BuilderAction,
    BuilderBlocker,
    BuilderSnapshot,
    BuilderStage,
    CurriculumBuilderState,
    StageStatus,
)


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
