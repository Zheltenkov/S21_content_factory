from content_gen.methodology import MethodologyGate
from content_gen.models.flow_state import ProjectContextBundle
from content_gen.models.schemas import PracticeTask, ProjectContextMeta, ProjectSeed
from content_gen.agents.context_analysis import ContextAnalysisResult
from content_gen.artifact_chain import EvidenceSpec
from content_gen.domain_contracts import build_narrative_contract


def _valid_context() -> dict:
    seed = ProjectSeed(
        language="ru",
        project_type="individual",
        thematic_block="PjM",
        audience_level="base",
        required_tools=[],
        title_seed="Test",
        project_description="Desc",
        learning_outcomes=["LO1"],
        skills=["Skill1"],
        curriculum_context={"block_name": "PjM"},
    )
    narrative_contract = build_narrative_contract(seed, seed.curriculum_context, [])
    seed.curriculum_context["narrative_contract"] = narrative_contract.model_dump()
    return {
        "seed": seed,
        "context_meta": ProjectContextMeta(
            track="PjM",
            thematic_block="PjM",
            last_order=1,
            context_summary="Summary",
        ),
        "context_analysis": ContextAnalysisResult(
            is_first_project=False,
            context_summary="Summary",
            skills_alignment={"intersection": ["Skill1"], "new": []},
        ),
        "context_bundle": ProjectContextBundle(
            thematic_block="PjM",
            previous_projects_count=1,
            context_summary="Summary",
            narrative_contract=narrative_contract.model_dump(),
        ),
    }


def test_context_review_passes_for_complete_contract() -> None:
    review = MethodologyGate().review("context", _valid_context())

    assert review.status == "passed"
    assert review.issues == []
    assert review.metrics["has_context_bundle"] is True


def test_context_review_requires_core_methodology_inputs() -> None:
    context = _valid_context()
    context["seed"].learning_outcomes = []
    context["seed"].skills = []
    context["seed"].curriculum_context = None

    review = MethodologyGate().review("context", context)

    assert review.status == "failed"
    assert review.human_review_required is True
    assert {issue.code for issue in review.issues} >= {
        "context.learning_outcomes_empty",
        "context.skills_empty",
        "context.curriculum_context_missing",
    }


def test_practice_review_detects_task_count_mismatch() -> None:
    class TaskPlan:
        tasks_count = 3

    review = MethodologyGate().review(
        "practice",
        {
            "markdown": "## Глава 3. Практика\n\n### Задача 1. Test\n\nBody",
            "practice_tasks": [object()],
            "task_plan": TaskPlan(),
        },
    )

    assert review.status == "warning"
    assert review.issues[0].code == "practice.tasks_count_mismatch"
    assert review.repair_instructions


def test_practice_review_detects_solution_materials_and_missing_dependency() -> None:
    first = PracticeTask(
        title="Анализ интервью",
        input_data="Готовый отчет с классификацией — см. файл `materials/final_report.md`",
        goal="Выдели ключевые проблемы пользователей.",
        expected_artifact="Таблица наблюдений",
        artifact_location="PjM15_PubApp/part-03/task-01/user_observations.md",
    )
    second = PracticeTask(
        title="Матрица решений",
        input_data="Описание критериев — см. файл `materials/task_02_source_notes.md`",
        goal="Сопоставь варианты решения.",
        expected_artifact="Матрица решений",
        artifact_location="PjM15_PubApp/part-03/task-02/decision_matrix.md",
    )

    review = MethodologyGate().review(
        "practice",
        {
            "markdown": "## Глава 3. Практика\n\n### Задача 1. Test\n\nBody",
            "practice_tasks": [first, second],
            "task_plan": None,
        },
    )

    assert review.status == "warning"
    assert {issue.code for issue in review.issues} >= {
        "practice.solution_materials_leak",
        "practice.task_dependency_missing",
    }


def test_theory_review_detects_static_instruction_leak() -> None:
    context = _valid_context()
    context["markdown"] = (
        "## Глава 2. Теоретический блок\n\n"
        "### Часть 1. Риски\n\n"
        "Теперь, когда ты освоил репозиторий и P2P, перейдём к рискам. "
        + "Риск влияет на сроки и бюджет проекта. " * 40
        + "\n\n## Глава 3. Практика\n"
    )
    context["theory_parts"] = [object()]

    review = MethodologyGate().review("theory", context)

    assert any(issue.code == "theory.static_instruction_leak" for issue in review.issues)


def test_practice_review_detects_processed_input_material_phrases() -> None:
    task = PracticeTask(
        title="Классификация наблюдений",
        input_data="Готовый реестр наблюдений с классификацией — см. файл `materials/task_01_source_notes.md`",
        goal="Классифицируй пользовательские наблюдения.",
        expected_artifact="Матрица наблюдений",
        artifact_location="PjM15_PubApp/part-03/task-01/observation_matrix.md",
    )

    review = MethodologyGate().review(
        "practice",
        {
            "markdown": "## Глава 3. Практика\n\n### Задача 1. Test\n\nBody",
            "practice_tasks": [task],
            "task_plan": None,
        },
    )

    assert any(issue.code == "practice.non_raw_input_materials" for issue in review.issues)


def test_dataset_generation_review_requires_evidence_specs_for_materials() -> None:
    task = PracticeTask(
        title="Анализ наблюдений",
        input_data="Сырые заметки — см. файл `materials/task_01_source_notes.md`.",
        goal="Проанализировать наблюдения.",
        expected_artifact="Таблица наблюдений",
        artifact_location="PjM15_PubApp/part-03/task-01/observations.md",
    )

    review = MethodologyGate().review(
        "dataset_generation",
        {
            "practice_tasks": [task],
            "dataset_files": [{"path": "materials/task_01_source_notes.md", "data": b"raw notes"}],
            "evidence_specs": [],
        },
    )

    assert any(issue.code == "dataset_generation.evidence_specs_missing" for issue in review.issues)


def test_dataset_generation_review_passes_for_raw_evidence_contract() -> None:
    task = PracticeTask(
        title="Анализ наблюдений",
        input_data="Сырые заметки — см. файл `materials/task_01_source_notes.md`.",
        goal="Проанализировать наблюдения.",
        expected_artifact="Таблица наблюдений",
        artifact_location="PjM15_PubApp/part-03/task-01/observations.md",
    )
    spec = EvidenceSpec(
        path="materials/task_01_source_notes.md",
        evidence_type="raw_case_evidence",
        contains=["сырые заметки"],
        excludes=["готовый отчет"],
        student_must_derive=["выводы"],
    )

    review = MethodologyGate().review(
        "dataset_generation",
        {
            "practice_tasks": [task],
            "dataset_files": [{"path": "materials/task_01_source_notes.md", "data": b"raw notes"}],
            "evidence_specs": [spec],
        },
    )

    assert review.status == "passed"
