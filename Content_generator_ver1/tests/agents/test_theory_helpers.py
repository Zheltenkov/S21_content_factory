from content_gen.agents.theory import _normalize_definition_bold, _sanitize_theory_body_text
from content_gen.models.schemas import ProjectSeed
from content_gen.utils.text_analysis import count_prose_words, count_words


def _make_seed() -> ProjectSeed:
    return ProjectSeed(
        language="ru",
        project_type="individual",
        thematic_block="PjM",
        audience_level="base",
        required_tools=["Miro"],
        title_seed="IT-инфраструктура",
        project_description="подобрать модель инфраструктуры для проекта с ограниченным бюджетом и требованиями к безопасности",
        learning_outcomes=["Уметь обосновывать выбор инфраструктурного решения"],
        skills=["Инфраструктура", "Безопасность"],
    )


def test_sanitize_theory_body_text_removes_lecture_like_bridge():
    seed = _make_seed()
    body = (
        "Теперь, когда ты познакомился с вводной частью, важно перейти к теории. "
        "Инфраструктура проекта — это основа, от которой зависит надежность решения. "
        "Если бюджет ограничен, тебе придется выбирать между гибкостью и стоимостью. "
        "Подумай, как это повлияет на решение команды."
    )

    cleaned = _sanitize_theory_body_text(
        body=body,
        title="Типы хостинга",
        seed=seed,
        anchors=["инфраструктура", "безопасность", "бюджет"],
        lo=110,
        hi=260,
    )

    assert "теперь, когда" not in cleaned.lower()
    assert "бюджет" in cleaned.lower()
    assert "инфраструктура" in cleaned.lower()


def test_sanitize_theory_body_text_pads_short_parts_to_minimum():
    seed = _make_seed()
    body = (
        "Customer Journey Map — это путь пользователя через продукт. "
        "Он помогает увидеть, где человек теряет интерес и где у команды появляется риск принять неверное решение."
    )

    cleaned = _sanitize_theory_body_text(
        body=body,
        title="Customer Journey Map",
        seed=seed,
        anchors=["customer journey map", "пользователь", "риск"],
        lo=110,
        hi=260,
    )

    assert count_words(cleaned, seed.language) >= 110
    assert "огранич" in cleaned.lower() or "риск" in cleaned.lower()


def test_sanitize_theory_body_text_does_not_append_raw_project_description_as_cause():
    seed = _make_seed()
    seed.project_description = (
        "Ученики в парах ищут реальную проблему пользователя, выбирают идею "
        "технологического продукта, проверяют её на простоту и реализуемость."
    )
    body = (
        "MVP помогает быстро проверить идею до большой разработки. "
        "Команда фиксирует гипотезу и смотрит, есть ли подтверждение от пользователя."
    )

    cleaned = _sanitize_theory_body_text(
        body=body,
        title="Проверка идеи на MVP",
        seed=seed,
        anchors=["не встречается"],
        lo=20,
        hi=160,
    )

    assert "потому что от этого зависит ученики" not in cleaned.lower()
    assert "чтобы связать теорию с практическими решениями" in cleaned.lower()


def test_sanitize_theory_body_text_pads_by_validator_prose_words_not_tables():
    seed = _make_seed()
    body = (
        "План работ — это способ связать задачи, сроки и зависимости. "
        "Он нужен, чтобы команда видела порядок выполнения и не теряла критические ограничения.\n\n"
        "| Колонка | Что фиксировать | Почему важно |\n"
        "| --- | --- | --- |\n"
        "| Задача | Краткое действие | Помогает сверить объем |\n"
        "| Срок | Дата или интервал | Показывает риск задержки |\n"
        "| Зависимость | Предыдущий результат | Убирает конфликт порядка |\n\n"
        "```mermaid\n"
        "flowchart TD\n"
        "A[Бэклог] --> B[Структура работ]\n"
        "B --> C[Дорожная карта]\n"
        "C --> D[План зависимостей]\n"
        "```\n"
    )

    cleaned = _sanitize_theory_body_text(
        body=body,
        title="План работ",
        seed=seed,
        anchors=["план", "задачи", "зависимости", "срок"],
        lo=110,
        hi=260,
    )

    assert count_prose_words(cleaned, seed.language) >= 110
    assert "| Колонка | Что фиксировать | Почему важно |" in cleaned


def test_normalize_definition_bold_wraps_plain_terms():
    text = "Customer Journey Map — это схема, которая показывает путь пользователя."
    normalized = _normalize_definition_bold(text)
    assert "**Customer Journey Map** — это" in normalized


def test_sanitize_theory_body_text_preserves_inline_tables_as_markdown_rows():
    seed = _make_seed()
    body = (
        "Разберём ключевые категории затрат. "
        "**Категории расходов** | Категория | Описание | |----------------|----------------| "
        "| Люди | Труд команды | | Сервисы | Подписки и лицензии | "
        "Эта таблица нужна для быстрой сверки бюджета."
    )

    cleaned = _sanitize_theory_body_text(
        body=body,
        title="Категории расходов",
        seed=seed,
        anchors=["расходы", "бюджет", "категории"],
        lo=110,
        hi=260,
    )

    assert "\n| Категория | Описание |" in cleaned
    assert "\n|----------------|----------------|" in cleaned
