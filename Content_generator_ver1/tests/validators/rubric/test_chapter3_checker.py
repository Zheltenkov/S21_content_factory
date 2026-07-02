"""Tests for Chapter3Checker contract alignment and stability."""

import re

from content_gen.validators.rubric.chapter3_checker import Chapter3Checker


def _get_item(items, item_id: str):
    return next(item for item in items if item.id == item_id)


def _canonical_task(
    *,
    number: int = 1,
    title: str = "Карта решений",
    situation: str = "Команда выбирает решение для первого релиза, но критерии спорные и результат должен быть проверяемым.",
    input_data: str = "Сырые заметки — см. файл `materials/task_01_source_notes.md`",
    goal: str = "Сопоставь варианты решений и подготовь карту выбора.",
    approach: list[str] | None = None,
    result_path: str = "PjM21_Project/part-03/task-01/decision_map.md",
    result_kind: str = "Markdown-файл",
) -> str:
    approach = approach or [
        "Выдели критерии выбора.",
        "Сравни варианты по влиянию на результат.",
    ]
    approach_md = "\n".join(f"- {item}" for item in approach)
    return (
        f"### Задание {number}. {title}\n\n"
        "**Что нужно сделать**\n\n"
        f"Ситуация: {situation}\n\n"
        f"Исходные данные: {input_data}\n\n"
        f"Цель: {goal}\n\n"
        "Подход:\n"
        f"{approach_md}\n\n"
        "**Что должно получиться**\n\n"
        f"- [ ] {result_kind} `{result_path}` содержит проверяемый результат.\n"
        "- [ ] В документе есть минимум 3 наблюдаемых пункта.\n"
        "- [ ] Файл размещён по указанному пути.\n\n"
        "**Формат сдачи**\n\n"
        f"На p2p-ревью покажи артефакт по пути `{result_path}`.\n\n"
        "**Переход к следующему заданию**\n\n"
        "В следующем задании используй этот результат как входные данные.\n"
    )


def test_canonical_task_template_satisfies_goal_approach_result_and_p2p_checks():
    checker = Chapter3Checker(llm_client=None, embedding_function=None, language="ru")
    ch3 = _canonical_task(
        title="Схема взаимодействия",
        situation=(
            "Команда обсуждает новый пользовательский сценарий, но frontend и backend по-разному понимают, "
            "какие данные и когда должны передаваться. Если не собрать схему сейчас, на тестировании появятся баги."
        ),
        input_data="Описание API и ролей — см. файл `materials/api_context.md`",
        goal="Определи ключевые точки взаимодействия frontend и backend и подготовь схему.",
        approach=[
            "Выдели HTTP-запросы и ответы.",
            "Сопоставь роли frontend и backend.",
        ],
        result_path="PjM20_FrontBack/part-03/task-01/interaction_diagram.png",
        result_kind="Схема взаимодействия",
    )

    items = checker.check(ch3, ch2_content="## Глава 2\n### 2.1. API\nHTTP-запросы и ответы.\n")
    assert _get_item(items, "2.5.2").score == 1
    assert _get_item(items, "2.5.3").score == 1
    assert _get_item(items, "2.5.4").score == 1
    assert _get_item(items, "2.5.5").score == 1
    assert _get_item(items, "2.5.6").score == 1


def test_similarity_falls_back_when_embeddings_are_incomplete():
    checker = Chapter3Checker(
        llm_client=None,
        embedding_function=lambda _texts: [[1.0]],  # intentionally incomplete
        language="ru",
    )
    avg, scores = checker._compute_pairwise_similarities("теория", ["практика 1", "практика 2"])
    assert isinstance(avg, float)
    assert len(scores) == 2


def test_structure_check_fails_without_situation_block():
    checker = Chapter3Checker(llm_client=None, embedding_function=None, language="ru")
    ch3 = (
        "### Задание 1. Анализ запроса\n\n"
        "**Что нужно сделать**\n\n"
        "Исходные данные: Письмо заказчика — см. файл `materials/client_request.md`\n\n"
        "Цель: Сформируй список уточняющих вопросов.\n\n"
        "Подход:\n"
        "- Выдели неясные требования.\n"
        "- Подготовь вопросы к заказчику.\n\n"
        "**Что должно получиться**\n\n"
        "- [ ] Список вопросов в файле `PjM20_FrontBack/part-03/task-01/questions.md`.\n"
        "- [ ] Есть минимум 5 уточняющих вопросов.\n"
        "- [ ] Файл размещён по указанному пути.\n\n"
        "**Формат сдачи**\n\n"
        "На p2p-ревью покажи файл `PjM20_FrontBack/part-03/task-01/questions.md`.\n"
    )

    items = checker.check(ch3, ch2_content="## Глава 2\n### 2.1. Требования\n")
    assert _get_item(items, "2.5.2").score == 0


def test_structure_check_accepts_long_situation_without_explicit_risk_token():
    checker = Chapter3Checker(llm_client=None, embedding_function=None, language="ru")
    ch3 = _canonical_task(
        title="Подготовка отчёта",
        situation=(
            "Команда несколько дней обсуждает, как представить результаты аудита для руководителя проекта, "
            "потому что решение должно быть понятным для всей группы и пригодным для последующей сверки на ревью."
        ),
        input_data="Материалы аудита — см. файл `materials/audit.md`",
        goal="Подготовь итоговый отчёт для команды.",
        approach=[
            "Собери наблюдения по единому шаблону.",
            "Сверь структуру отчёта с ожиданиями команды.",
        ],
        result_path="PjM21_Project/part-03/task-01/report.md",
        result_kind="Итоговый документ",
    )

    items = checker.check(ch3, ch2_content="## Глава 2\n### 2.1. Отчёт\n")
    assert _get_item(items, "2.5.2").score == 1


def test_p2p_check_accepts_observable_phrases_used_by_generator():
    checker = Chapter3Checker(llm_client=None, embedding_function=None, language="ru")
    ch3 = _canonical_task(
        title="Бюджет",
        situation="Команда готовит смету проекта и должна показать её ревьюеру в проверяемом виде.",
        input_data="Черновая смета — см. файл `materials/budget.xlsx`",
        goal="Подготовь итоговую смету расходов.",
        approach=["Сверь категории затрат.", "Зафиксируй расчёты."],
        result_path="PjM12_Budget/part-03/task-02/budget.xlsx",
        result_kind="Таблица",
    )

    items = checker.check(ch3, ch2_content="## Глава 2\n### 2.1. Бюджет\n")
    assert _get_item(items, "2.5.6").score == 1


def test_expected_result_check_accepts_result_label_aliases():
    checker = Chapter3Checker(llm_client=None, embedding_function=None, language="ru")
    ch3 = (
        "### Задание 1. Снять напряжение перед выходом\n\n"
        "**Что нужно сделать**\n\n"
        "Ситуация: Ты готовишь публичное выступление и должен собрать проверяемый черновик.\n\n"
        "Цель: Подготовь структуру выступления.\n\n"
        "Подход:\n"
        "- Собери основные тезисы.\n"
        "- Проверь связь тезисов с задачей.\n\n"
        "**Что должно получиться**\n\n"
        "- [ ] Документ `PjM15_PubApp/part-03/task-01/README.md` содержит черновик структуры выступления.\n"
        "- [ ] В документе есть минимум 3 наблюдаемых пункта.\n"
        "- [ ] Файл размещён по указанному пути.\n\n"
        "**Формат сдачи**\n\n"
        "На p2p-ревью покажи документ `PjM15_PubApp/part-03/task-01/README.md`.\n"
    )

    items = checker.check(ch3, ch2_content="## Глава 2\n### 2.1. Выступление\n")

    assert _get_item(items, "2.5.5").score == 1
    assert Chapter3Checker._has_expected_result_text(ch3, [], "")


def test_theory_practice_connection_uses_titles_and_term_overlap_with_low_embeddings():
    def low_similarity_embeddings(texts):
        return [[1.0, 0.0]] + [[0.30, 0.95] for _ in texts[1:]]

    checker = Chapter3Checker(
        llm_client=None,
        embedding_function=low_similarity_embeddings,
        language="ru",
        regex_patterns={"rx_task": re.compile(r"^###\s+Задани(?:е|я)\s+(\d+)\.\s*(.+?)\s*$", re.M)},
    )
    ch2 = (
        "## Глава 2. Теоретический блок\n\n"
        "### 2.1. Дорожная карта\n\n"
        "**Дорожная карта** — это план релиза, который связывает задачи, сроки и ожидаемый результат.\n\n"
        "### 2.2. Зависимости задач\n\n"
        "**Зависимости задач** — это связи, которые показывают, что нельзя делать параллельно.\n"
    )
    ch3 = _canonical_task(
        title="Дорожная карта и зависимости задач",
        situation="Команда переводит сырой бэклог в план релиза и должна показать связи между задачами.",
        input_data="Сырой бэклог — см. файл `materials/backlog.md`",
        goal="Сопоставь задачи, сроки и зависимости задач.",
        approach=[
            "Выдели задачи, которые блокируют другие работы.",
            "Собери дорожную карту первого релиза.",
        ],
        result_path="PjM11_WorkPlan/part-03/task-01/roadmap.md",
        result_kind="Markdown-файл",
    )

    items = checker.check(ch3, ch2_content=ch2)

    connection_item = _get_item(items, "2.5.7")
    assert connection_item.score == 1
    assert connection_item.details["average_similarity"] < connection_item.details["threshold"]
    assert "Дорожная карта" in connection_item.details["matched_terms"]
