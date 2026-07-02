from content_gen.agents.task_planner import TaskPlan
from content_gen.methodology.gate import MethodologyGate
from content_gen.methodology.repair import MethodologyRepairController
from content_gen.models.schemas import ProjectSeed


def _seed(tasks_count: int | None = 3) -> ProjectSeed:
    return ProjectSeed(
        language="ru",
        project_type="individual",
        thematic_block="PjM",
        audience_level="base",
        required_tools=[],
        title_seed="Test",
        project_description="Desc",
        learning_outcomes=["LO1"],
        skills=["Skill1"],
        tasks_count=tasks_count,
        curriculum_context={"block": "PjM"},
    )


def _task_plan(tasks_count: int = 3) -> TaskPlan:
    return TaskPlan(
        tasks_count=tasks_count,
        complexity="medium",
        level_index=1,
        level_source="audience_only",
        rationale="rationale",
        explanation="explanation",
        curriculum_context={},
    )


def test_repair_clamps_task_plan_and_syncs_seed() -> None:
    seed = _seed(tasks_count=3)
    task_plan = _task_plan(tasks_count=7)
    context = {"seed": seed, "task_plan": task_plan}
    review = MethodologyGate().review("task_planning", context)

    repair = MethodologyRepairController().repair("task_planning", context, review)

    assert repair is not None
    assert repair.status == "applied"
    assert context["task_plan"].tasks_count == 5
    assert context["seed"].tasks_count == 5
    assert "task_plan.tasks_count" in repair.updated_fields


def test_repair_builds_missing_blueprint_from_skeleton() -> None:
    context = {
        "seed": _seed(tasks_count=2),
        "task_plan": _task_plan(tasks_count=2),
        "markdown": (
            "# Title\n\n"
            "## Глава 1. Введение и инструкция\n\n"
            "### Введение\n\n"
            "Body\n\n"
            "## Глава 2. Теория\n\n"
            "### Часть 1. Topic\n\n"
            "Body\n\n"
            "## Глава 3. Практика\n\n"
            "### Задача 1. Task\n\n"
            "Body\n"
        ),
    }
    review = MethodologyGate().review("skeleton", context)

    repair = MethodologyRepairController().repair("skeleton", context, review)

    assert repair is not None
    assert repair.status == "applied"
    assert context["blueprint"].planned_tasks_count == 2
    assert context["blueprint"].chapter_titles["theory"].startswith("## Глава 2")


def test_repair_parses_theory_parts_from_markdown() -> None:
    context = {
        "markdown": (
            "## Глава 2. Теория\n\n"
            "### Часть 1. First\n\n"
            "Текст первой части.\n\n"
            "### Часть 2. Second\n\n"
            "Текст второй части.\n\n"
            "## Глава 3. Практика\n\n"
        ),
        "theory_parts": [],
    }
    review = MethodologyGate().review("theory", context)

    repair = MethodologyRepairController().repair("theory", context, review)

    assert repair is not None
    assert repair.status == "applied"
    assert [part.title for part in context["theory_parts"]] == ["First", "Second"]


def test_repair_parses_practice_tasks_from_markdown() -> None:
    context = {
        "task_plan": _task_plan(tasks_count=2),
        "markdown": (
            "## Глава 3. Практика\n\n"
            "### Задача 1. First task\n\n"
            "Сделайте первый артефакт.\n\n"
            "### Задача 2. Second task\n\n"
            "Сделайте второй артефакт.\n"
        ),
        "practice_tasks": [],
    }
    review = MethodologyGate().review("practice", context)

    repair = MethodologyRepairController().repair("practice", context, review)

    assert repair is not None
    assert repair.status == "applied"
    assert [task.title for task in context["practice_tasks"]] == ["First task", "Second task"]


def test_repair_parses_canonical_theory_and_practice_markdown() -> None:
    markdown = (
        "## Глава 2. Теоретический блок\n\n"
        "### 2.1. Критерии\n\nТекст теории.\n\n"
        "## Глава 3. Практический блок\n\n"
        "### Задание 1. Карта решений\n\n"
        "**Что нужно сделать**\n\nЦель: Сопоставь варианты.\n\n"
        "**Что должно получиться**\n\n"
        "- [ ] Файл `PjM21_Project/part-03/task-01/decision_map.md` содержит карту.\n"
        "- [ ] Выбор обоснован.\n\n"
        "**Формат сдачи**\n\nПокажи файл.\n"
    )

    theory_parts = MethodologyRepairController.parse_theory_parts(markdown)
    practice_tasks = MethodologyRepairController.parse_practice_tasks(markdown)

    assert theory_parts[0].title == "Критерии"
    assert practice_tasks[0].title == "Карта решений"
    assert "Сопоставь варианты" in practice_tasks[0].goal
    assert practice_tasks[0].artifact_location == "PjM21_Project/part-03/task-01/decision_map.md"


def test_repair_skips_context_issues_without_deterministic_policy() -> None:
    context = {"seed": _seed(tasks_count=3)}
    context["seed"].learning_outcomes = []
    review = MethodologyGate().review("context", context)

    repair = MethodologyRepairController().repair("context", context, review)

    assert repair is not None
    assert repair.status == "skipped"
    assert repair.skipped_reason == "no deterministic repair registered for these issue codes"
