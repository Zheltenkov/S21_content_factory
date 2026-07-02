from content_gen.agents.base.llm_client import LLMClientProtocol
from content_gen.agents.practice import PracticeAgent
from content_gen.config.active_goals import has_active_goal_verb
from content_gen.models.schemas import PracticeTask, ProjectSeed
from content_gen.validators.practice_checks import PracticeChecks


class MockLLMClient(LLMClientProtocol):
    def complete(self, system: str, user: str, response_format=None, **kwargs) -> str:
        return "mock response"


class PracticeMarkdownLLMClient(LLMClientProtocol):
    def complete(self, system: str, user: str, response_format=None, **kwargs) -> str:
        return (
            "### Задание 1. Снять шум перед выступлением\n\n"
            "**Ситуация:** Ты готовишь выступление, но в заметках смешаны факты, эмоции и второстепенные детали.\n\n"
            "**Ограничение / риск:** Если не отделить главное, речь получится длинной и потеряет фокус.\n\n"
            "**Входные данные:** Сырые заметки — см. файл `materials/task_01_source_notes.md`.\n\n"
            "**Цель:** Выделить опорные тезисы для выступления.\n\n"
            "**Подход:**\n"
            "- Отдели факты от оценок.\n"
            "- Сгруппируй тезисы по смыслу.\n\n"
            "**Ожидаемый результат:** создать финальный текст выступления в Markdown\n\n"
            "### Задание 2. Собрать каркас речи по SERMON\n\n"
            "**Ситуация:** После очистки заметок нужно собрать речь так, чтобы она держала понятную структуру.\n\n"
            "**Ограничение / риск:** Если структура будет размытой, слушатель не поймёт основной вывод.\n\n"
            "**Входные данные:** Результат предыдущей задачи.\n\n"
            "**Цель:** Собрать каркас речи по SERMON.\n\n"
            "**Подход:**\n"
            "- Соотнеси тезисы с элементами SERMON.\n"
            "- Зафиксируй переходы между частями.\n"
        )


def _seed() -> ProjectSeed:
    return ProjectSeed(
        language="ru",
        project_type="individual",
        project_description="Подготовка к публичным выступлениям",
        learning_outcomes=["Подготовить артефакт для выступления"],
        skills=["SERMON", "сторителлинг"],
        platform_name="PjM15_PubApp",
    )


def test_fix_result_artifact_prefers_explicit_project_path():
    agent = PracticeAgent(MockLLMClient())
    result = (
        "Структурированная презентация в файле `PjM15_PubApp/part-03/task-01/presentation_structure.md`. "
        "Размещен в репозитории по пути repo/part-03/task-01/README.md"
    )

    fixed_result, artifact_location = agent._fix_result_artifact(result, _seed(), 0)

    assert artifact_location == "PjM15_PubApp/part-03/task-01/presentation_structure.md"
    assert "repo/part-03/task-01/README.md" not in fixed_result
    assert "presentation_structure.md" in fixed_result


def test_fix_result_artifact_adds_location_to_markdown_text_deliverable():
    agent = PracticeAgent(MockLLMClient())

    fixed_result, artifact_location = agent._fix_result_artifact(
        "создать финальный текст выступления в Markdown",
        _seed(),
        0,
    )

    assert "финальный текст выступления" in fixed_result
    assert artifact_location == "PjM15_PubApp/part-03/task-01/README.md"
    assert artifact_location in fixed_result


def test_project_seed_replaces_generic_repo_template_with_project_path():
    seed = ProjectSeed(
        language="ru",
        project_type="individual",
        project_description="Планирование работ по бэклогу проекта",
        learning_outcomes=["Сформировать план работ"],
        skills=["планирование"],
        platform_name="PjM11_BacklogWorkPlan",
        repo_path_template="repo/part-03/task-{num:02d}/README.md",
    )

    assert seed.repo_path_template == "PjM11_BacklogWorkPlan/part-03/task-{num:02d}/README.md"


def test_fix_goal_active_form_converts_imperfective_formirovat():
    agent = PracticeAgent(MockLLMClient())

    fixed_goal = agent._fix_goal_active_form(
        "Формировать дорожную карту проекта на основе структуры работ.",
        "ru",
    )

    assert fixed_goal.startswith("Сформировать дорожную карту")
    assert has_active_goal_verb(fixed_goal)


def test_ensure_task_artifact_contract_replaces_generic_expected_artifact():
    agent = PracticeAgent(MockLLMClient())
    task = PracticeTask(
        title="Построить план зависимостей",
        situation="Заказчик просит понять, какие работы блокируют релиз.",
        constraints_or_risk="Если зависимости не видны, команда сорвёт срок.",
        input_data="Результат предыдущей задачи.",
        goal="Сформировать план зависимостей.",
        expected_artifact="Артефакт размещён по пути `repo/part-03/task-03/README.md`.",
        artifact_location="",
    )

    [fixed_task] = agent._ensure_task_artifact_contract([task], _seed(), "ru")

    assert "repo/part-03" not in fixed_task.expected_artifact
    assert "планом зависимостей" in fixed_task.expected_artifact.lower()
    assert "заказчика" in fixed_task.expected_artifact.lower()
    assert fixed_task.artifact_location == "PjM15_PubApp/part-03/task-01/README.md"
    assert fixed_task.artifact_location in fixed_task.expected_artifact


def test_ensure_task_artifact_contract_fills_missing_expected_result_and_p2p():
    agent = PracticeAgent(MockLLMClient())
    task = PracticeTask(
        title="Собрать каркас речи по SERMON",
        input_data="Сырые заметки — см. файл `materials/task_01_source_notes.md`.",
        goal="Собрать каркас выступления по SERMON.",
        expected_artifact="",
        artifact_location="",
    )

    [fixed_task] = agent._ensure_task_artifact_contract([task], _seed(), "ru")

    assert fixed_task.expected_artifact.strip()
    assert fixed_task.artifact_location == "PjM15_PubApp/part-03/task-01/README.md"
    assert fixed_task.artifact_location in fixed_task.expected_artifact
    assert len(fixed_task.p2p_criteria) >= 3


def test_generate_repairs_vague_and_missing_expected_results():
    agent = PracticeAgent(PracticeMarkdownLLMClient())
    seed = _seed().model_copy(update={"tasks_count": 2})

    result = agent.generate(seed, theory_summary="1. Структурирование речи по технологии SERMON")

    assert len(result.tasks) == 2
    for task in result.tasks:
        assert task.expected_artifact.strip()
        assert task.artifact_location
        assert task.artifact_location in task.expected_artifact
        assert len(task.p2p_criteria) >= 3


def test_ensure_p2p_criteria_adds_explicit_path_and_observable_checks():
    agent = PracticeAgent(MockLLMClient())
    criteria = agent._ensure_p2p_criteria(
        ["Есть выводы по задаче"],
        "PjM15_PubApp/part-03/task-02/report.md",
        "Отчёт в формате Markdown",
        ["Работа с критикой", "Структурирование речи по технологии SERMON"],
        "ru",
    )

    assert any("по указанному пути" in item.lower() for item in criteria)
    assert len(criteria) >= 3
    assert any("в документе" in item.lower() or "понятия из теории" in item.lower() for item in criteria)


def test_normalize_approach_bullets_injects_theory_anchor_when_missing():
    agent = PracticeAgent(MockLLMClient())
    bullets = agent._normalize_approach_bullets(
        ["собери тезисы выступления", "подготовь итоговый вариант текста"],
        ["Структурирование речи по технологии SERMON"],
        "ru",
    )

    assert any("sermon" in bullet.lower() for bullet in bullets)


def test_ensure_sjm_task_anchors_keeps_customer_visible():
    agent = PracticeAgent(MockLLMClient())
    seed = _seed().model_copy(
        update={
            "sjm": "Ты — project manager. Заказчик просит согласовать реалистичный план за 2 недели.",
        }
    )
    tasks = [
        PracticeTask(
            title="Разобрать заметки",
            situation="Команда получила сырой список задач и спорит о приоритетах.",
            constraints_or_risk="Есть риск потерять важную зависимость.",
            goal="Сформировать первичный список работ.",
            expected_artifact="Документ размещён в `PjM15_PubApp/part-03/task-01/README.md`.",
        ),
        PracticeTask(
            title="Собрать план",
            situation="Нужно связать задачи в последовательность.",
            constraints_or_risk="Срок ограничен.",
            goal="Создать план работ.",
            expected_artifact="План размещён в `PjM15_PubApp/part-03/task-02/README.md`.",
        ),
    ]

    fixed = agent._ensure_sjm_task_anchors(tasks, seed, "ru")

    assert "заказчик" in fixed[0].situation.lower()
    assert "заказчик" in fixed[1].situation.lower()


def test_enforce_learning_activity_contract_replaces_solution_materials_with_task_chain():
    agent = PracticeAgent(MockLLMClient())
    tasks = [
        PracticeTask(
            title="Анализ интервью",
            input_data="Готовый отчет с классификацией — см. файл `materials/final_report.md`",
            goal="Выдели ключевые проблемы пользователей.",
            expected_artifact="Таблица наблюдений размещена в `PjM15_PubApp/part-03/task-01/user_observations.md`",
            artifact_location="PjM15_PubApp/part-03/task-01/user_observations.md",
        ),
        PracticeTask(
            title="Матрица решений",
            input_data="Готовая матрица — см. файл `materials/decision_matrix.md`",
            goal="Сопоставь варианты решения по выбранным критериям.",
            expected_artifact="Матрица решений размещена в `PjM15_PubApp/part-03/task-02/decision_matrix.md`",
            artifact_location="PjM15_PubApp/part-03/task-02/decision_matrix.md",
        ),
    ]

    normalized = agent._enforce_learning_activity_contract(tasks, "ru")

    assert "materials/final_report.md" not in normalized[0].input_data
    assert "materials/task_01_source_notes.md" in normalized[0].input_data
    assert "materials/decision_matrix.md" not in normalized[1].input_data
    assert "PjM15_PubApp/part-03/task-01/user_observations.md" in normalized[1].input_data


def test_finalize_bonus_tasks_applies_practice_contracts_and_bonus_paths():
    agent = PracticeAgent(MockLLMClient())
    seed = _seed().model_copy(
        update={
            "sjm": "Ты работаешь с заказчиком, которому нужен проверяемый план улучшения выступления.",
        }
    )
    bonus = PracticeTask(
        title="Подготовить расширенную версию защиты",
        situation="Команда должна показать заказчику более убедительную защиту решения, иначе согласование затянется.",
        constraints_or_risk="Если бонус останется общим текстом, ревьюер не сможет проверить качество улучшения.",
        input_data="Готовый отчет по выступлению — см. файл `materials/final_report.md`",
        goal="Сформировать расширенную структуру защиты решения.",
        approach_bullets=[
            "Сопоставь тезисы с ожиданиями заказчика.",
            "Зафиксируй улучшения и аргументы для защиты.",
        ],
        expected_artifact="Документ размещён в `repo/part-03/task-99/README.md`.",
    )

    [fixed] = agent.finalize_bonus_tasks([bonus], seed, "ru")
    result = PracticeChecks(language="ru", check_task_count=False).check([fixed])

    assert fixed.artifact_location == "PjM15_PubApp/part-03/bonus-01/README.md"
    assert fixed.artifact_location in fixed.expected_artifact
    assert "materials/final_report.md" not in fixed.input_data
    assert len(fixed.p2p_criteria) >= 3
    assert not result.hard_issues
