import yaml

from content_gen.checklist import build_project_checklist_yaml
from content_gen.models.readme_document import ReadmeDocument
from content_gen.models.schemas import PracticeTask


def test_checklist_yml_is_built_from_final_readme_tasks() -> None:
    markdown = """# Планирование рисков

## Глава 3. Практический блок

### Задание 1. Собрать карту рисков

**Что нужно сделать**
Ситуация: команда готовит небольшой IT-проект.
Цель: создать карту рисков, которую можно проверить на p2p.

**Что должно получиться**
- [ ] В документе есть таблица рисков.
- [ ] Указан путь к артефакту `PjM1/part-03/task-01/README.md`.
- [ ] Для каждого риска есть причина и мера реагирования.

**Формат сдачи**
README.md в папке задачи.
"""
    yml = build_project_checklist_yaml(
        project_title="Планирование рисков",
        language="ru",
        readme_document=ReadmeDocument.from_markdown(markdown),
        practice_tasks=[],
    )

    data = yaml.safe_load(yml)

    assert data["language"] == "ru"
    assert data["quick_actions"] == ["EMPTY_WORK", "CHEAT"]
    question = data["sections"][0]["questions"][0]
    assert question["name"] == "Задание 1. Собрать карту рисков"
    assert "создать карту рисков" in question["description"]
    assert "В документе есть таблица рисков" in question["description"]
    assert "PjM1/part-03/task-01/README.md" in question["description"]


def test_checklist_yml_falls_back_to_typed_practice_tasks() -> None:
    task = PracticeTask(
        title="Подготовить дорожную карту",
        goal="собрать проверяемый план работ",
        expected_artifact="README с дорожной картой",
        artifact_location="Project/part-03/task-01/README.md",
        p2p_criteria=["Есть минимум 5 задач", "У каждой задачи указан ожидаемый результат"],
    )

    yml = build_project_checklist_yaml(
        project_title="Дорожная карта",
        language="ru",
        readme_document=ReadmeDocument.from_markdown("# Дорожная карта\n\n## Глава 2. Теория\n\nТекст."),
        practice_tasks=[task],
    )

    data = yaml.safe_load(yml)
    question = data["sections"][0]["questions"][0]

    assert question["name"] == "Задание 1. Подготовить дорожную карту"
    assert "README с дорожной картой" in question["description"]
    assert "Есть минимум 5 задач" in question["description"]
