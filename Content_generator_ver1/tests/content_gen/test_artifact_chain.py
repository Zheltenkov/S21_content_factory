from content_gen.artifact_chain import GenericArtifactChainPlanner
from content_gen.models.schemas import PracticeTask, ProjectSeed


def _seed() -> ProjectSeed:
    return ProjectSeed(
        language="ru",
        project_type="individual",
        title_seed="Discovery Process",
        platform_name="PjM15_Discovery",
        project_description="Проект про анализ пользовательских наблюдений и выбор решения.",
        learning_outcomes=["Сопоставить наблюдения и подготовить решение"],
        skills=["аналитика", "приоритизация"],
        tasks_count=2,
    )


def test_artifact_chain_planner_enforces_raw_input_and_previous_artifact_dependency() -> None:
    planner = GenericArtifactChainPlanner()
    seed = _seed()
    plan = planner.plan(seed, 2)
    tasks = [
        PracticeTask(
            title="Собрать наблюдения",
            input_data="",
            goal="Выделить рабочие наблюдения из кейса.",
            expected_artifact="Таблица наблюдений",
        ),
        PracticeTask(
            title="Сопоставить решения",
            input_data="Описание критериев выбора.",
            goal="Сопоставить варианты решения.",
            expected_artifact="Матрица решения",
        ),
    ]

    planned_tasks, updated_plan = planner.apply(tasks, seed, plan)

    assert "materials/task_01_source_notes.md" in planned_tasks[0].input_data
    assert planned_tasks[0].artifact_location in planned_tasks[1].input_data
    assert updated_plan.evidence_specs[0].path == "materials/task_01_source_notes.md"
    assert updated_plan.evidence_specs[0].student_must_derive


def test_artifact_chain_prompt_is_generic() -> None:
    plan = GenericArtifactChainPlanner().plan(_seed(), 2)
    rendered = plan.to_prompt_context().lower()

    assert "artifact chain contract" in rendered
    assert "swot" not in rendered
    assert "risk_register" not in rendered


def test_artifact_chain_ignores_placeholder_repo_template_when_project_root_known() -> None:
    seed = ProjectSeed.model_construct(
        language="ru",
        project_type="individual",
        title_seed="План работ",
        platform_name="PjM11_BacklogWorkPlan",
        repo_path_template="repo/part-03/task-{num:02d}/README.md",
        project_description="Планирование работ по бэклогу.",
        learning_outcomes=["Сформировать план работ"],
        skills=["планирование"],
        tasks_count=1,
    )

    plan = GenericArtifactChainPlanner().plan(seed, 1)

    assert plan.steps[0].artifact_location == "PjM11_BacklogWorkPlan/part-03/task-01/README.md"
