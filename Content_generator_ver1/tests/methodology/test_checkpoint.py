import pytest
from types import SimpleNamespace

from content_gen.methodology import HumanApprovalCheckpointPolicy
from content_gen.methodology.decision import MethodologyGateInterrupt
from content_gen.orchestrator import Orchestrator


class DummyLLM:
    def complete(self, *_args, **_kwargs) -> str:
        return ""


def _full_readme_with_late_practice() -> str:
    theory_padding = " ".join(f"Теоретический фрагмент {index}." for index in range(140))
    return (
        "# Публичные выступления\n\n"
        "Аннотация проекта.\n\n"
        "## Глава 1. Введение и инструкция\n\n"
        "Вводный текст.\n\n"
        "## Глава 2. Теоретический блок\n\n"
        f"{theory_padding}\n\n"
        "## Глава 3. Практический блок\n\n"
        "Практика после длинной теории должна быть видна в финальном preview.\n\n"
        "### Задача 1. Подготовить выступление\n\n"
        "Текст практической задачи."
    )


def _artifact_from_human_checkpoint(
    checkpoint_id: str,
    node_id: str,
    context: dict,
) -> dict:
    policy = HumanApprovalCheckpointPolicy({checkpoint_id})
    with pytest.raises(MethodologyGateInterrupt):
        policy.maybe_raise(node_id, context)
    return context["human_approval_checkpoint"]["artifact"]


def test_human_checkpoint_policy_pauses_after_title_annotation(monkeypatch) -> None:
    monkeypatch.setenv("METHODOLOGY_HUMAN_CHECKPOINTS", "title")
    policy = HumanApprovalCheckpointPolicy.from_env()
    context = {
        "title": "Управление рисками",
        "annotation": {"text": "Краткая аннотация.", "chars": 18},
    }

    with pytest.raises(MethodologyGateInterrupt) as exc_info:
        policy.maybe_raise("title_annotation", context)

    checkpoint = context["human_approval_checkpoint"]
    assert checkpoint["id"] == "title"
    assert checkpoint["artifact"]["title"] == "Управление рисками"
    assert checkpoint["artifact"]["annotation"] == "Краткая аннотация."
    assert exc_info.value.context["error_type"] == "HumanApprovalCheckpoint"
    assert exc_info.value.context["checkpoint"]["resume_from_node"] == "skeleton"


def test_human_checkpoint_policy_maps_legacy_annotation_env_to_title(monkeypatch) -> None:
    monkeypatch.setenv("METHODOLOGY_HUMAN_CHECKPOINTS", "annotation")
    policy = HumanApprovalCheckpointPolicy.from_env()

    assert policy.checkpoints == {"title"}


def test_human_checkpoint_policy_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv("METHODOLOGY_HUMAN_CHECKPOINTS", "off")
    policy = HumanApprovalCheckpointPolicy.from_env(enabled_by_default=True)
    context = {"title": "Title", "annotation": {"text": "Annotation", "chars": 10}}

    policy.maybe_raise("title_annotation", context)

    assert "human_approval_checkpoint" not in context


def test_orchestrator_can_disable_human_checkpoint_even_with_methodology_callback(monkeypatch) -> None:
    monkeypatch.delenv("METHODOLOGY_HUMAN_CHECKPOINTS", raising=False)

    orchestrator = Orchestrator(
        DummyLLM(),
        methodology_progress_callback=lambda _payload: None,
        human_approval_enabled=False,
    )

    assert orchestrator.human_checkpoint_policy.checkpoints == set()


def test_orchestrator_can_enable_human_checkpoint_from_request_flag(monkeypatch) -> None:
    monkeypatch.setenv("METHODOLOGY_HUMAN_CHECKPOINTS", "annotation")

    orchestrator = Orchestrator(
        DummyLLM(),
        methodology_progress_callback=lambda _payload: None,
        human_approval_enabled=True,
    )

    assert orchestrator.human_checkpoint_policy.checkpoints == HumanApprovalCheckpointPolicy.DEFAULT_CHECKPOINTS


def test_human_checkpoint_policy_defaults_to_all_content_checkpoints(monkeypatch) -> None:
    monkeypatch.delenv("METHODOLOGY_HUMAN_CHECKPOINTS", raising=False)

    policy = HumanApprovalCheckpointPolicy.from_env(enabled_by_default=True)

    assert policy.checkpoints == HumanApprovalCheckpointPolicy.DEFAULT_CHECKPOINTS


def test_human_checkpoint_policy_defaults_cover_all_flow_nodes(monkeypatch) -> None:
    monkeypatch.delenv("METHODOLOGY_HUMAN_CHECKPOINTS", raising=False)

    policy = HumanApprovalCheckpointPolicy.from_env(enabled_by_default=True)

    assert set(HumanApprovalCheckpointPolicy.CHECKPOINT_NODE_MAP) == {
        "context",
        "task_planning",
        "title_annotation",
        "skeleton",
        "theory",
        "practice",
        "global_quality",
        "evaluation",
        "translate",
        "finalize",
    }
    default_checkpoint_values = set(HumanApprovalCheckpointPolicy.CHECKPOINT_NODE_MAP.values()) - {"context", "finalize"}
    assert default_checkpoint_values.issubset(policy.checkpoints)
    assert "context" not in policy.checkpoints
    assert "finalize" not in policy.checkpoints


def test_human_checkpoint_policy_pauses_after_context() -> None:
    policy = HumanApprovalCheckpointPolicy({"context"})
    context = {
        "seed": SimpleNamespace(
            language="ru",
            project_type="individual",
            direction="PjM",
            thematic_block="Переговоры",
            audience_level="middle",
            title_seed="Эффективные переговоры",
            project_description="Проект про выбор стратегии разговора.",
            sjm="Команда обсуждает требования заказчика.",
            learning_outcomes=["Выбирать стратегию переговоров"],
            skills=["Коммуникация"],
        ),
        "context_meta": SimpleNamespace(
            track="PjM",
            thematic_block="Переговоры",
            last_order=14,
            narrative_anchor="заказчик меняет требования",
            context_summary="Контекст трека собран.",
            similar_projects=[{"title": "Предыдущий проект"}],
        ),
        "context_analysis": SimpleNamespace(context_summary="Анализ контекста.", narrative_anchor="кейс"),
        "context_bundle": SimpleNamespace(context_source="curriculum", previous_projects_count=3),
        "warnings": [],
    }

    with pytest.raises(MethodologyGateInterrupt) as exc_info:
        policy.maybe_raise("context", context)

    checkpoint = context["human_approval_checkpoint"]
    assert checkpoint["id"] == "context"
    assert checkpoint["resume_from_node"] == "task_planning"
    assert checkpoint["artifact"]["context_review"]["project_description"] == "Проект про выбор стратегии разговора."
    assert checkpoint["artifact"]["context_review"]["storytelling"] == "Команда обсуждает требования заказчика."
    assert checkpoint["artifact"]["context_review"]["facts"] == [
        {"label": "Трек", "value": "PjM"},
        {"label": "Блок программы", "value": "Переговоры"},
        {"label": "Формат проекта", "value": "индивидуальный"},
        {"label": "Уровень аудитории", "value": "средний"},
        {"label": "Источник контекста", "value": "curriculum"},
    ]
    assert "seed_summary" not in checkpoint["artifact"]
    assert "context_summary" not in checkpoint["artifact"]
    assert checkpoint["artifact"]["learning_outcomes"] == ["Выбирать стратегию переговоров"]
    assert checkpoint["artifact"]["similar_projects"] == ["Предыдущий проект"]
    assert exc_info.value.context["checkpoint"]["stage"] == "context"


def test_human_checkpoint_policy_pauses_after_task_planning() -> None:
    policy = HumanApprovalCheckpointPolicy({"task_planning"})
    context = {
        "seed": SimpleNamespace(
            project_type="group",
            direction="PjM",
            thematic_block="Переговоры",
            audience_level="base",
            title_seed="Эффективные переговоры",
            project_description="Проект про выбор стратегии разговора.",
            sjm="Команда обсуждает требования заказчика.",
            learning_outcomes=["Выбирать стратегию переговоров"],
            skills=["Коммуникация"],
        ),
        "context_meta": SimpleNamespace(
            track="PjM",
            thematic_block="Переговоры",
            context_summary="Контекст трека собран.",
            similar_projects=[{"title": "Предыдущий проект"}],
        ),
        "task_plan": SimpleNamespace(
            tasks_count=3,
            complexity="medium",
            level_index=1,
            level_source="context+audience",
            rationale="История трека требует средней сложности.",
            explanation="План строится от кейса к артефакту.",
        ),
        "practice_plan_contract": SimpleNamespace(
            task_count=3,
            project_goal="Собрать стратегию разговора.",
            story_map=SimpleNamespace(
                student_role="младший менеджер",
                working_case="заказчик меняет требования",
                central_tension="нужно сохранить контакт и границы",
                completion="готова стратегия разговора",
            ),
            steps=[
                SimpleNamespace(
                    task_index=1,
                    title_hint="Снять картину переговоров",
                    artifact_location="PjM/part-03/task-01/README.md",
                    p2p_focus=["факты отделены от выводов"],
                )
            ],
        ),
        "artifact_chain_plan": SimpleNamespace(
            steps=[
                SimpleNamespace(
                    task_index=1,
                    artifact_location="PjM/part-03/task-01/README.md",
                    artifact_kind="первичный рабочий артефакт",
                )
            ]
        ),
        "evidence_specs": [
            {
                "path": "materials/task_01_source_notes.md",
                "evidence_type": "raw_case_evidence",
                "contains": ["сырые наблюдения", "факты"],
            }
        ],
        "warnings": [],
    }

    with pytest.raises(MethodologyGateInterrupt):
        policy.maybe_raise("task_planning", context)

    checkpoint = context["human_approval_checkpoint"]
    assert checkpoint["id"] == "task_planning"
    assert checkpoint["resume_from_node"] == "title_annotation"
    assert checkpoint["title"] == "Проверка замысла и плана"
    assert checkpoint["artifact"]["context_review"]["project_description"] == "Проект про выбор стратегии разговора."
    assert checkpoint["artifact"]["planning_review"]["facts"][0] == {"label": "Количество задач", "value": "3"}
    assert checkpoint["artifact"]["planning_review"]["task_flow"][0]["title"] == "Снять картину переговоров"
    assert checkpoint["artifact"]["planning_review"]["evidence"][0]["path"] == "materials/task_01_source_notes.md"
    assert checkpoint["artifact"]["learning_outcomes"] == ["Выбирать стратегию переговоров"]
    assert "task_plan" not in checkpoint["artifact"]
    assert "practice_plan" not in checkpoint["artifact"]
    assert "artifact_chain" not in checkpoint["artifact"]


def test_human_checkpoint_policy_pauses_after_skeleton_for_structure() -> None:
    policy = HumanApprovalCheckpointPolicy({"structure"})
    context = {
        "title": "Проект",
        "annotation": {"text": "Аннотация.", "chars": 10},
        "markdown": (
            "# Проект\n\n"
            "Аннотация.\n\n"
            "## Глава 1. Введение и инструкция\n\n"
            "### Введение\n\nТекст.\n\n"
            "## Глава 2. Теоретический блок\n\n"
            "## Глава 3. Практический блок\n"
        ),
    }

    with pytest.raises(MethodologyGateInterrupt) as exc_info:
        policy.maybe_raise("skeleton", context)

    checkpoint = context["human_approval_checkpoint"]
    assert checkpoint["id"] == "structure"
    assert checkpoint["resume_from_node"] == "theory"
    assert checkpoint["artifact"]["structure_outline"][0]["title"] == "Проект"
    assert "Глава 2" in checkpoint["artifact"]["markdown_excerpt"]
    assert "p2p" not in {item["id"] for item in checkpoint["artifact"]["requirements_matrix"]}
    assert exc_info.value.context["checkpoint"]["stage"] == "skeleton"


def test_human_checkpoint_policy_pauses_after_theory() -> None:
    policy = HumanApprovalCheckpointPolicy({"theory"})
    context = {
        "title": "Проект",
        "markdown": "## Глава 2. Теоретический блок\n\nТекст теории.\n\n## Глава 3. Практический блок\n",
        "theory_parts": [SimpleNamespace(title="Риски", body="один два три", example="четыре")],
    }

    with pytest.raises(MethodologyGateInterrupt) as exc_info:
        policy.maybe_raise("theory", context)

    checkpoint = context["human_approval_checkpoint"]
    assert checkpoint["id"] == "theory"
    assert checkpoint["resume_from_node"] == "practice"
    assert checkpoint["artifact"]["theory_parts"][0]["title"] == "Риски"
    assert checkpoint["artifact"]["theory_parts"][0]["words"] == 4
    assert "Глава 2" in checkpoint["artifact"]["markdown_excerpt"]
    assert "p2p" not in {item["id"] for item in checkpoint["artifact"]["requirements_matrix"]}
    assert exc_info.value.context["checkpoint"]["stage"] == "theory"


def test_human_checkpoint_policy_skips_already_approved_same_artifact() -> None:
    policy = HumanApprovalCheckpointPolicy({"theory"})
    context = {
        "title": "Проект",
        "markdown": "## Глава 2. Теоретический блок\n\nТекст теории.\n\n## Глава 3. Практический блок\n",
        "theory_parts": [SimpleNamespace(title="Риски", body="один два три", example="четыре")],
    }

    with pytest.raises(MethodologyGateInterrupt):
        policy.maybe_raise("theory", context)
    checkpoint = dict(context["human_approval_checkpoint"])
    context["methodology_review_actions"] = [
        {
            "action": "approved",
            "details": {
                "checkpoint_id": checkpoint["id"],
                "checkpoint_hash": checkpoint["artifact_hash"],
            },
        }
    ]

    policy.maybe_raise("theory", context)

    assert context["last_skipped_human_approval_checkpoint"]["artifact_hash"] == checkpoint["artifact_hash"]


def test_human_checkpoint_policy_uses_safe_excerpt_when_chapter_heading_is_missing() -> None:
    policy = HumanApprovalCheckpointPolicy({"theory"})
    context = {
        "title": "Проект",
        "markdown": "README без ожидаемого заголовка главы.",
        "theory_parts": [SimpleNamespace(title="Тема", text="один два три")],
    }

    with pytest.raises(MethodologyGateInterrupt):
        policy.maybe_raise("theory", context)

    checkpoint = context["human_approval_checkpoint"]
    assert checkpoint["artifact"]["markdown_excerpt"] == "README без ожидаемого заголовка главы."


def test_human_checkpoint_policy_pauses_after_practice_with_materials() -> None:
    policy = HumanApprovalCheckpointPolicy({"practice"})
    context = {
        "title": "Проект",
        "markdown": (
            "## Глава 3. Практический блок\n\n"
            "Практика.\n\n"
            "### Задача 1. Собрать артефакт\n\n"
            "| Поле | Значение |\n|---|---|\n| A | B |\n\n"
            "### Задача 2. Проверить артефакт\n\nТекст."
        ),
        "practice_tasks": [
            SimpleNamespace(title="Задача 1", objective="Собрать артефакт"),
            SimpleNamespace(title="Задача 2", objective="Проверить артефакт"),
        ],
        "dataset_files": [{"path": "materials/source.md", "data": b"raw"}],
    }

    with pytest.raises(MethodologyGateInterrupt):
        policy.maybe_raise("practice", context)

    checkpoint = context["human_approval_checkpoint"]
    assert checkpoint["id"] == "practice"
    assert checkpoint["resume_from_node"] == "global_quality"
    assert checkpoint["artifact"]["practice_tasks"][0]["title"] == "Задача 1"
    assert checkpoint["artifact"]["dataset_files"][0]["path"] == "materials/source.md"
    assert "| Поле | Значение |" in checkpoint["artifact"]["markdown_excerpt"]
    assert len(checkpoint["artifact"]["markdown_sections"]) == 2
    assert checkpoint["artifact"]["markdown_sections"][0]["title"] == "Задача 1. Собрать артефакт"


def test_practice_checkpoint_includes_bonus_tasks_and_bonus_excerpt() -> None:
    policy = HumanApprovalCheckpointPolicy({"practice"})
    context = {
        "title": "Проект",
        "markdown": (
            "## Глава 3. Практический блок\n\n"
            "### Задача 1. Собрать артефакт\n\n"
            "Текст основной задачи.\n\n"
            "## Бонус\n\n"
            "### Бонусная задача 1. Усилить решение\n\n"
            "Текст бонусного задания."
        ),
        "practice_tasks": [SimpleNamespace(title="Задача 1", objective="Собрать артефакт")],
        "bonus_tasks": [SimpleNamespace(title="Усилить решение", objective="Добавить проверку")],
    }

    with pytest.raises(MethodologyGateInterrupt):
        policy.maybe_raise("practice", context)

    checkpoint = context["human_approval_checkpoint"]
    assert checkpoint["artifact"]["summary"].startswith("Сгенерировано задач: 2")
    assert checkpoint["artifact"]["practice_tasks"][1]["title"] == "Бонусное задание: Усилить решение"
    assert "## Бонус" in checkpoint["artifact"]["markdown_excerpt"]
    assert checkpoint["artifact"]["markdown_sections"][-1]["title"] == "Бонусная задача 1. Усилить решение"


def test_quality_checkpoint_keeps_full_readme_preview() -> None:
    markdown = _full_readme_with_late_practice()
    context = {
        "title": "Публичные выступления",
        "markdown": markdown,
        "warnings": [],
        "issues": [],
        "rubric_json": {"score": 100, "items": [{"passed": True}]},
    }

    artifact = _artifact_from_human_checkpoint("quality", "global_quality", context)

    assert len(markdown) > 1800
    assert markdown.find("## Глава 3. Практический блок") > 1800
    assert artifact["markdown_excerpt"] == markdown
    assert "## Глава 3. Практический блок" in artifact["markdown_excerpt"]
    section_titles = [section["title"] for section in artifact["markdown_sections"]]
    assert "Глава 1. Введение и инструкция" in section_titles
    assert "Глава 2. Теоретический блок" in section_titles
    assert "Глава 3. Практический блок" in section_titles
    assert "requirements_matrix" in artifact


def test_evaluation_checkpoint_shows_rubric_instead_of_readme_preview() -> None:
    markdown = _full_readme_with_late_practice()
    rubric = {
        "score": 97,
        "items": [
            {"id": "1.1", "title": "Структура", "passed": True},
            {"id": "2.4.3", "title": "Объем теории", "passed": False},
            {"id": "3.2", "title": "Нарративный фокус", "score": 0},
        ],
    }
    context = {
        "title": "Публичные выступления",
        "markdown": markdown,
        "warnings": [],
        "issues": ["Раздел 2.4 требует доработки"],
        "rubric_json": rubric,
    }

    artifact = _artifact_from_human_checkpoint("evaluation", "evaluation", context)

    assert artifact["rubric"] == rubric
    assert artifact["rubric_score"] == 97
    assert artifact["rubric_failed_count"] == 2
    assert artifact["issues_count"] == 1
    assert "markdown_excerpt" not in artifact
    assert "markdown_sections" not in artifact
    assert "requirements_matrix" not in artifact


def test_human_checkpoint_policy_pauses_after_translation() -> None:
    policy = HumanApprovalCheckpointPolicy({"translation"})
    context = {
        "title": "Negotiation Map",
        "target_language": "en",
        "markdown": "# Исходный README\n",
        "translated_markdown": (
            "# Negotiation Map\n\n"
            "## Chapter 1. Introduction\n\n"
            "Translated text.\n"
        ),
    }

    with pytest.raises(MethodologyGateInterrupt):
        policy.maybe_raise("translate", context)

    checkpoint = context["human_approval_checkpoint"]
    assert checkpoint["id"] == "translation"
    assert checkpoint["resume_from_node"] == "finalize"
    assert checkpoint["artifact"]["target_language"] == "en"
    assert "Negotiation Map" in checkpoint["artifact"]["markdown_excerpt"]


def test_human_checkpoint_policy_pauses_after_finalize() -> None:
    policy = HumanApprovalCheckpointPolicy({"finalize"})
    context = {
        "title": "Карта переговоров",
        "markdown": "# Карта переговоров\n\n## Глава 1. Введение и инструкция\n\nТекст.\n",
        "project_spec": SimpleNamespace(
            title="Карта переговоров",
            language="ru",
            theory=[SimpleNamespace(title="Теория")],
            practice=[SimpleNamespace(title="Задача")],
        ),
        "assets": {"README.md": b"data", "checklist.yml": b"data"},
    }

    with pytest.raises(MethodologyGateInterrupt):
        policy.maybe_raise("finalize", context)

    checkpoint = context["human_approval_checkpoint"]
    assert checkpoint["id"] == "finalize"
    assert checkpoint["resume_from_node"] == "completed"
    assert checkpoint["artifact"]["assets_count"] == 2
    assert checkpoint["artifact"]["project_spec_summary"]["practice_count"] == 1


def test_final_checkpoint_requirement_matrix_passes_for_canonical_readme() -> None:
    markdown = (
        "# Карта решений\n\n"
        "Аннотация проекта с понятным результатом.\n\n"
        "## Содержание\n\n"
        "## Глава 1. Введение и инструкция\n\n"
        "### Введение\n\nТекст.\n\n"
        "### Инструкция\n\nТекст.\n\n"
        "## Глава 2. Теоретический блок\n\n"
        "### 2.1. Критерии выбора\n\nТекст.\n\n"
        "## Глава 3. Практический блок\n\n"
        "### Задание 1. Карта решений\n\n"
        "**Что нужно сделать**\n\nЦель: Сопоставь варианты решений.\n\nПодход:\n- Выдели критерии.\n- Сравни варианты.\n\n"
        "**Что должно получиться**\n\n"
        "- [ ] Файл `PjM21_Project/part-03/task-01/decision_map.md` содержит карту решений.\n"
        "- [ ] Выбор обоснован через критерии.\n\n"
        "**Формат сдачи**\n\nПокажи файл `PjM21_Project/part-03/task-01/decision_map.md`.\n\n"
        "**Переход к следующему заданию**\n\nПрактическая цепочка завершается.\n\n"
        "## Заключение\n\nИтог текущего проекта проверяем на p2p-ревью.\n"
    )
    context = {
        "title": "Карта решений",
        "markdown": markdown,
        "practice_tasks": [SimpleNamespace(title="Карта решений", objective="Сопоставь варианты")],
        "rubric_json": {"score": 100, "items": []},
    }

    artifact = _artifact_from_human_checkpoint("quality", "global_quality", context)
    matrix = {item["id"]: item for item in artifact["requirements_matrix"]}

    assert matrix["structure"]["passed"] is True
    assert matrix["theory"]["passed"] is True
    assert matrix["practice_template"]["passed"] is True
    assert matrix["p2p"]["passed"] is True
    assert matrix["final_closure"]["passed"] is True
