from __future__ import annotations

from content_factory.catalog.pipeline.curriculum import CurriculumDesignSpec, CurriculumStageSpec
from content_factory.catalog.viewer import up_builder_state as builder_state_module
from content_factory.catalog.viewer.up_builder_state import (
    BriefOption,
    BuilderSnapshot,
    BuilderStage,
    derive_curriculum_builder_state,
)


def _approved_design() -> CurriculumDesignSpec:
    return CurriculumDesignSpec(
        journey_type="professional_workflow",
        program_goal="Освоить рабочий процесс",
        stages=(
            CurriculumStageSpec(
                code="stage-01",
                title="Основы",
                goal="Освоить основы",
                coverage_areas=("Основы",),
                node_ids=("S1", "S2"),
            ),
        ),
        capstone_required=False,
        capstone_title="Итоговый проект",
        approved=True,
    )


def test_builder_starts_clean_even_when_recent_briefs_exist() -> None:
    state = derive_curriculum_builder_state(
        BuilderSnapshot(),
        [BriefOption(6, "Founder", "Product", None)],
    )

    assert not state.has_brief
    assert state.next_action is not None
    assert state.next_action.label == "Загрузить бриф"
    assert state.next_action.href == "#curriculum-brief-form"
    assert state.progress_percent == 0
    assert state.recent_briefs[0].brief_id == 6


def test_builder_extracts_compact_brief_analysis_from_intake_payload() -> None:
    analysis = builder_state_module._build_brief_analysis(
        {
            "spec": {
                "artifact_type": "learner_brief",
                "role": "Founder",
                "seniority": "junior",
                "domain": "Product",
                "program_goal": "Запустить цифровой продукт.",
                "must_include_areas": ["Customer discovery", "MVP"],
                "sub_queries": ["какие навыки нужны founder"],
            },
            "coverage": {
                "covered_count": 1,
                "partial_count": 1,
                "uncovered_count": 0,
                "rows": [
                    {
                        "area": "Customer discovery",
                        "status": "partial",
                        "candidate_names": ["Понимание клиента"],
                        "dropped_candidate_names": [],
                        "rationale": "Найдено частичное покрытие.",
                    }
                ],
            },
            "atomize": {"raw_count": 4, "atomic_count": 3, "composite_count": 1, "non_skill_count": 0},
            "normalize": {"atomic_input_count": 3, "atomic_output_count": 2, "merged_count": 1, "compacted_count": 0},
        }
    )

    assert analysis is not None
    assert analysis.available
    assert [metric.value for metric in analysis.metrics] == ["learner_brief", "Founder", "junior", "Product"]
    assert analysis.program_goal == "Запустить цифровой продукт."
    assert analysis.coverage_rows[0].status_label == "частично"
    assert analysis.coverage_rows[0].matched_candidates == "Понимание клиента"
    assert analysis.sub_queries == ["какие навыки нужны founder"]


def test_builder_extracts_atomic_skill_candidates_for_inline_review() -> None:
    candidates = builder_state_module._build_skill_candidates(
        [
            {
                "suggestion_id": 9,
                "name": "Проведение интервью",
                "group": "Research",
                "bloom": "apply",
                "decision": "needs_review",
                "entity_type": "skill",
                "atomicity": "atomic",
                "nearest_skill_id": 4,
                "nearest_name": "Интервью с пользователями",
                "match_score": "91.00",
                "confidence": "0.82",
                "recommended_action": {"code": "link", "label": "Связать с каталогом"},
            },
            {
                "suggestion_id": 10,
                "name": "Рамочный блок",
                "decision": "needs_review",
                "entity_type": "block",
                "atomicity": "frame",
            },
        ]
    )

    assert len(candidates) == 1
    assert candidates[0].suggestion_id == 9
    assert candidates[0].is_open
    assert candidates[0].decision_label == "Требует решения"
    assert candidates[0].recommendation_code == "link"


def test_builder_extracts_template_proposals_for_inline_review() -> None:
    proposals = builder_state_module._build_template_proposals(
        [
            {
                "id": 12,
                "status": "open",
                "title": "Отчёт о клиентском исследовании",
                "artifact_family": "analysis",
                "scope_type": "coverage_area",
                "scope_names": ["Customer discovery"],
                "covered_skill_names": ["Проведение интервью", "Анализ потребностей"],
                "confidence": 0.94,
            }
        ]
    )

    assert len(proposals) == 1
    assert proposals[0].proposal_id == 12
    assert proposals[0].is_open
    assert proposals[0].family_label == "Аналитический вывод"
    assert proposals[0].covered_skill_names == ("Проведение интервью", "Анализ потребностей")


def test_builder_keeps_running_job_inside_constructor() -> None:
    state = derive_curriculum_builder_state(
        BuilderSnapshot(latest_job_id=17, latest_job_status="running", latest_job_stage="decompose"),
        [BriefOption(6, "Founder", "Product", None)],
    )

    assert not state.has_brief
    assert state.next_action is not None
    assert state.next_action.label == "Дождаться обработки"
    assert state.next_action.href == "/app/curriculum?job_id=17"
    assert state.stages[0].description == "обрабатывается"
    assert state.stages[0].status == "active"


def test_builder_route_stage_captions_are_compact() -> None:
    done_stage = BuilderStage(1, "Бриф", "загружен и разобран", "done")
    blocked_stage = BuilderStage(6, "УП", "заблокирован DAG", "warn")

    assert done_stage.donut_caption == "разобран"
    assert blocked_stage.donut_caption == "нет DAG"


def test_builder_next_action_prioritizes_open_skill_reviews() -> None:
    state = derive_curriculum_builder_state(
        BuilderSnapshot(
            brief_id=6,
            latest_job_id=14,
            total_suggestions=22,
            accepted_atomic_count=12,
            open_skill_reviews=3,
        ),
        [BriefOption(6, "Founder", "Product", None)],
    )

    assert state.next_action is not None
    assert state.next_action.label == "Проверить навыки"
    assert state.next_action.method == "get"
    assert state.next_action.code == "review_skills"
    assert state.next_action.href == "/app/curriculum?brief_id=6#skills-review"
    assert state.progress_percent == 14
    assert state.stages[1].status == "active"
    assert state.stages[2].description == "после навыков"
    assert state.blockers[0].title == "Открытые решения по навыкам"


def test_builder_moves_from_resolved_skills_to_catalog_then_dag() -> None:
    catalog_state = derive_curriculum_builder_state(
        BuilderSnapshot(
            brief_id=6,
            latest_job_id=14,
            total_suggestions=2,
            accepted_atomic_count=2,
        )
    )

    assert catalog_state.next_action is not None
    assert catalog_state.next_action.code == "apply_catalog"
    assert catalog_state.next_action.method == "post"
    assert not catalog_state.blockers

    dag_state = derive_curriculum_builder_state(
        BuilderSnapshot(
            brief_id=6,
            latest_job_id=14,
            total_suggestions=2,
            accepted_atomic_count=2,
            catalog_applied=True,
            active_promotions=2,
            skill_set_items=2,
        )
    )

    assert dag_state.next_action is not None
    assert dag_state.next_action.code == "build_dag"
    assert dag_state.next_action.method == "post"
    assert not dag_state.blockers


def test_builder_requires_prerequisite_edge_review_before_design() -> None:
    state = derive_curriculum_builder_state(
        BuilderSnapshot(
            brief_id=6,
            latest_job_id=14,
            total_suggestions=2,
            accepted_atomic_count=2,
            catalog_applied=True,
            dag_nodes=2,
            dag_order_count=2,
            open_edge_reviews=3,
            design_spec=_approved_design(),
        )
    )

    assert state.next_action is not None
    assert state.next_action.code == "review_dag_edges"
    assert state.next_action.href.endswith("entity_type=prerequisite_edge")
    assert state.stages[3].status == "active"
    assert state.stages[4].status == "pending"
    assert state.blockers[0].title == "Связи DAG требуют решения"


def test_builder_moves_from_template_review_to_explicit_plan_build() -> None:
    review_state = derive_curriculum_builder_state(
        BuilderSnapshot(
            brief_id=6,
            latest_job_id=14,
            plan_id=30,
            total_suggestions=2,
            accepted_atomic_count=2,
            catalog_applied=True,
            dag_nodes=2,
            dag_order_count=2,
            template_total=2,
            template_open=2,
            design_spec=_approved_design(),
        )
    )

    assert review_state.next_action is not None
    assert review_state.next_action.code == "review_templates"
    assert review_state.next_action.href == "/app/curriculum?brief_id=6#template-review"

    build_state = derive_curriculum_builder_state(
        BuilderSnapshot(
            brief_id=6,
            latest_job_id=14,
            plan_id=30,
            total_suggestions=2,
            accepted_atomic_count=2,
            catalog_applied=True,
            dag_nodes=2,
            dag_order_count=2,
            template_total=2,
            template_accepted=2,
            design_spec=_approved_design(),
        )
    )

    assert build_state.next_action is not None
    assert build_state.next_action.code == "build_plan"
    assert build_state.next_action.method == "post"
    assert build_state.next_action.href == "/app/curriculum/briefs/6/build-plan"


def test_builder_blocks_plan_when_templates_were_accepted_before_valid_dag() -> None:
    state = derive_curriculum_builder_state(
        BuilderSnapshot(
            brief_id=6,
            latest_job_id=14,
            plan_id=30,
            total_suggestions=28,
            accepted_atomic_count=21,
            catalog_applied=True,
            active_promotions=21,
            skill_set_items=34,
            dag_status="catalog_applied",
            dag_nodes=0,
            dag_order_count=0,
            template_total=10,
            template_accepted=10,
            plan_row_count=0,
        )
    )

    assert state.next_action is not None
    assert state.next_action.label == "Построить DAG"
    assert state.next_action.method == "post"
    assert state.next_action.code == "build_dag"
    assert state.next_action.href == "/app/curriculum/jobs/14/build-dag"
    assert state.progress_percent == 43
    assert [blocker.title for blocker in state.blockers] == ["Шаблоны приняты раньше DAG"]
    assert state.stages[3].status == "warn"
    assert state.stages[6].description == "заблокирован DAG"


def test_builder_opens_plan_after_rows_are_built() -> None:
    state = derive_curriculum_builder_state(
        BuilderSnapshot(
            brief_id=6,
            latest_job_id=14,
            plan_id=30,
            total_suggestions=22,
            accepted_atomic_count=21,
            catalog_applied=True,
            dag_nodes=20,
            dag_order_count=20,
            template_total=10,
            template_accepted=10,
            plan_row_count=8,
            plan_design_current=True,
            design_spec=_approved_design(),
        )
    )

    assert state.next_action is not None
    assert state.next_action.label == "Открыть УП"
    assert state.next_action.code == "open_plan"
    assert state.next_action.href == "/app/spravochnik/up/plans/30"
    assert state.progress_percent == 100
    assert not state.blockers
    assert state.stages[-1].status == "done"


def test_builder_rebuilds_existing_plan_after_design_approval() -> None:
    state = derive_curriculum_builder_state(
        BuilderSnapshot(
            brief_id=6,
            latest_job_id=14,
            plan_id=30,
            total_suggestions=22,
            accepted_atomic_count=21,
            catalog_applied=True,
            dag_nodes=20,
            dag_order_count=20,
            template_total=10,
            template_accepted=10,
            plan_row_count=8,
            plan_design_current=False,
            design_spec=_approved_design(),
        )
    )

    assert state.next_action is not None
    assert state.next_action.label == "Пересобрать УП"
    assert state.next_action.code == "build_plan"
    assert state.next_action.method == "post"
    assert state.stages[-1].status == "warn"
    assert state.stages[-1].description == "нужно пересобрать"
    assert [blocker.title for blocker in state.blockers] == ["УП собран по предыдущему каркасу"]


def test_builder_requires_design_approval_after_valid_dag() -> None:
    proposed = CurriculumDesignSpec(
        journey_type="professional_workflow",
        program_goal="Освоить процесс",
        stages=(CurriculumStageSpec("stage-01", "Основы", "Освоить основы", ("Основы",), ("S1",)),),
        capstone_required=True,
        capstone_title="Итоговая работа",
    )
    state = derive_curriculum_builder_state(
        BuilderSnapshot(
            brief_id=6,
            latest_job_id=14,
            total_suggestions=1,
            accepted_atomic_count=1,
            catalog_applied=True,
            dag_nodes=1,
            dag_order_count=1,
            design_spec=proposed,
        )
    )

    assert state.next_action is not None
    assert state.next_action.code == "review_design"
    assert state.next_action.href == "/app/curriculum?brief_id=6#program-design"
    assert state.stages[4].label == "Каркас"
    assert state.stages[4].status == "active"
    assert state.blockers[0].title == "Каркас программы требует решения"
