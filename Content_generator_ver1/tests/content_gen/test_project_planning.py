from content_gen.agents.task_planner import TaskPlan
from content_gen.models.schemas import ProjectContextMeta, ProjectSeed
from content_gen.project_planning import ProjectBlueprintPlanner, render_practice_plan_contract_section


def _seed() -> ProjectSeed:
    return ProjectSeed(
        language="ru",
        project_type="individual",
        direction="BSA",
        title_seed="Customer Discovery",
        platform_name="BSA03_Discovery",
        project_description="Студент анализирует интервью и превращает наблюдения в проверяемое решение.",
        learning_outcomes=[
            "Идентифицировать ключевые наблюдения клиента",
            "Сопоставить наблюдения с ограничениями продукта",
        ],
        skills=["интервью", "аналитика", "приоритизация"],
        tasks_count=3,
        sjm="Ты — аналитик продукта. Команда получила интервью клиентов и спорит, что делать дальше.",
    )


def _task_plan() -> TaskPlan:
    return TaskPlan(
        tasks_count=3,
        complexity="medium",
        level_index=1,
        level_source="audience_only",
        rationale="test",
        explanation="test",
    )


def test_project_blueprint_planner_builds_story_and_practice_contract_before_theory() -> None:
    story_map, practice_plan, artifact_chain = ProjectBlueprintPlanner().build(
        _seed(),
        _task_plan(),
        ProjectContextMeta(track="BSA", thematic_block="Discovery"),
    )

    assert story_map.student_role
    assert practice_plan.task_count == 3
    assert len(practice_plan.steps) == 3
    assert practice_plan.steps[0].input_refs == ["materials/task_01_source_notes.md"]
    assert practice_plan.steps[1].depends_on == artifact_chain.steps[0].artifact_location
    assert practice_plan.steps[2].depends_on == artifact_chain.steps[1].artifact_location
    assert practice_plan.coverage_map
    assert practice_plan.theory_support_topics()


def test_practice_plan_prompt_is_generic_and_keeps_raw_evidence_contract() -> None:
    _, practice_plan, _ = ProjectBlueprintPlanner().build(_seed(), _task_plan())
    rendered = render_practice_plan_contract_section(practice_plan).lower()

    assert "practice plan contract" in rendered
    assert "raw evidence" in rendered
    assert "materials/task_01_source_notes.md" in rendered
    assert "swot" not in rendered
    assert "risk_register" not in rendered
