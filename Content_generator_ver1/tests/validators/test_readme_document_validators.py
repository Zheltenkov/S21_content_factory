from content_gen.models.readme_document import ReadmeDocument
from content_gen.validators.practice import PracticeValidator
from content_gen.validators.structure import IntroValidator
from content_gen.validators.theory import TheoryValidator


def _words(count: int) -> str:
    return " ".join(f"слово{i}" for i in range(count))


def test_intro_validator_accepts_typed_chapter_sections() -> None:
    document = ReadmeDocument.from_markdown(
        "# Проект\n\n"
        "Аннотация.\n\n"
        "## Глава 1. Введение и инструкция\n\n"
        "### Введение\n\n"
        f"Этот раздел используется для реальной задачи. {_words(75)}\n\n"
        "### Инструкция\n\n"
        "В проекте обязательно фиксируй результат, допускается черновая работа, "
        "запрещено сдавать неподготовленный артефакт.\n\n"
        "## Глава 2. Теория\n\n"
        "Черновик."
    )

    issues = IntroValidator().validate_document(document)

    assert not [issue for issue in issues if issue.level == "error"]


def test_theory_validator_uses_typed_sections_and_ignores_fenced_headings() -> None:
    body = _words(120)
    document = ReadmeDocument.from_markdown(
        "# Проект\n\n"
        "## Глава 2. Теоретический блок\n\n"
        "### 2.1. Первый раздел\n\n"
        f"{body}\n\n```md\n### 2.99. Не раздел\n```\n\n"
        "**Пример:** Практический пример.\n\n"
        "**Вопросы к практике:**\n- Как применить это в проекте?\n\n"
        "### 2.2. Второй раздел\n\n"
        f"{body}\n\n**Пример:** Практический пример.\n\n"
        "**Вопросы к практике:**\n- Как применить это в проекте?\n\n"
        "### 2.3. Третий раздел\n\n"
        f"{body}\n\n**Пример:** Практический пример.\n\n"
        "**Вопросы к практике:**\n- Как применить это в проекте?\n\n"
        "## Глава 3. Практический блок\n\n"
        "Черновик."
    )

    issues = TheoryValidator().validate_document(document)

    assert not [issue for issue in issues if issue.path == "theory"]
    assert document.section_by_title_fragment("2.99") is None


def test_practice_validator_uses_typed_task_sections() -> None:
    task_body = (
        "**Что нужно сделать**\n\n"
        "Ситуация: команда готовит проверяемый артефакт.\n\n"
        "Исходные данные: заметки лежат в `materials/input.md`.\n\n"
        "Цель: подготовь итоговый документ.\n\n"
        "Подход:\n- Выдели требования.\n- Собери результат.\n\n"
        "**Что должно получиться**\n\n"
        "- [ ] Документ размещен по пути `project/part-03/task-01/README.md`.\n"
        "- [ ] В документе есть минимум 3 проверяемых пункта.\n\n"
        "**Формат сдачи**\n\n"
        "На p2p-ревью покажи файл по пути `project/part-03/task-01/README.md`."
    )
    document = ReadmeDocument.from_markdown(
        "# Проект\n\n"
        "## Глава 3. Практический блок\n\n"
        f"### Задание 1. Первый артефакт\n\n{task_body}\n\n"
        f"### Задание 2. Второй артефакт\n\n{task_body}"
    )

    issues = PracticeValidator().validate_document(document, language="ru", tasks_count_expected=2)

    assert not [issue for issue in issues if issue.path == "practice.tasks"]
    task = document.section_by_title_fragment("Задание 1")
    assert task is not None
    assert task.label_block("Что должно получиться").startswith("- [ ] Документ")
    assert task.block_counts(include_paragraphs=True)["criteria"] == 1
