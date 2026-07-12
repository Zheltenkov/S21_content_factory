from content_factory.generation.agents.task_planner import TaskPlanner
from content_factory.generation.models.schemas import ProjectSeed


def test_task_planner_preserves_explicit_task_count() -> None:
    seed = ProjectSeed(
        language="ru",
        project_type="individual",
        direction="PjM",
        title_seed="Проверяемый проект",
        platform_name="project",
        project_description="Участник собирает и защищает артефакт.",
        learning_outcomes=["Собирает артефакт"],
        skills=["Анализ"],
        tasks_count=3,
        audience_level="beginner",
    )

    plan = TaskPlanner().plan(seed, None, None)

    assert plan.tasks_count == 3
    assert "явно заданное количество" in plan.explanation
    assert plan.task_count_contract is not None
    assert plan.task_count_contract.requested_tasks_count == 3
    assert plan.task_count_contract.recommended_tasks_count == 5
    assert plan.task_count_contract.resolved_tasks_count == 3
    assert plan.task_count_contract.resolution_source == "user"


def test_task_planner_keeps_automatic_recommendation_inside_didactic_range() -> None:
    seed = ProjectSeed(
        language="ru",
        project_type="individual",
        direction="PjM",
        title_seed="Вводный проект",
        platform_name="intro",
        project_description="Участник собирает вводный артефакт.",
        learning_outcomes=["Собирает артефакт"],
        skills=["Анализ"],
        audience_level="beginner",
    )

    plan = TaskPlanner().plan(seed, None, None)

    assert plan.tasks_count == 5
    assert plan.task_count_contract is not None
    assert plan.task_count_contract.requested_tasks_count is None
    assert plan.task_count_contract.resolved_tasks_count == 5
    assert plan.task_count_contract.resolution_source == "default"
