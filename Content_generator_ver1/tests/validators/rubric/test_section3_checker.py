import json

from content_gen.models.readme_document import ReadmeDocument
from content_gen.validators.rubric.section3_checker import Section3Checker
from content_gen.validators.rubric.similarity import SimilarityCalculator


class NarrativeLLM:
    def complete(self, **kwargs):
        user = kwargs["user"]
        has_drift = "DevOps" in user and "Types and data structures" in user
        return json.dumps({
            "has_unified_focus": not has_drift,
            "anchors": ["бэклог", "дорожная карта", "диаграмма Ганта"],
            "drift": ["DevOps", "Types and data structures"] if has_drift else [],
            "reason": "Найден чужой учебный контекст" if has_drift else "Фокус сохранен",
        })


def test_narrative_focus_reports_curriculum_drift() -> None:
    md = """
# План работ

## Глава 1. Введение и инструкция

### Введение
Проект про бэклог, дорожную карту и диаграмму Ганта. Теперь акцент смещается на DevOps, Types and data structures.

### Инструкция
Следуй общим правилам платформы.

## Глава 2. Теоретический блок

### 2.1. Бэклог
Бэклог помогает команде связать задачи, зависимости и дорожную карту.

## Глава 3. Практический блок

### Задание 1. Собери план
**Что нужно сделать**

Ситуация: Команда готовит план и должна связать бэклог с дорожной картой.

Исходные данные: Сырые заметки — см. файл `materials/backlog.md`.

Цель: Сформировать дорожную карту на основе бэклога.

Подход:
- Выдели задачи.
- Свяжи задачи с этапами.

**Что должно получиться**

- [ ] Markdown-файл с планом работ размещён по пути `Project/part-03/task-01/roadmap.md`.
- [ ] В документе есть задачи и этапы.
- [ ] Файл размещён по указанному пути.

**Формат сдачи**

На p2p-ревью покажи файл `Project/part-03/task-01/roadmap.md`.
"""

    checker = Section3Checker(SimilarityCalculator(), llm_client=NarrativeLLM())
    item = next(item for item in checker.check(md) if item.id == "3.2")

    assert item.score == 0
    assert item.details["drift"] == ["DevOps", "Types and data structures"]
    assert "чужой учебный контекст" in item.comments[0].lower()


def test_check_uses_typed_document_for_coherence_when_available(monkeypatch) -> None:
    document = ReadmeDocument.from_markdown(
        "# Проект\n\n"
        "Аннотация задает рабочий контекст команды и общий продукт.\n\n"
        "## Глава 1. Введение\n\n"
        "Команда продолжает работать над тем же продуктом и уточняет решение.\n\n"
        "## Глава 2. Теория\n\n"
        "Теория объясняет тот же рабочий продукт через понятные решения.\n\n"
        "## Глава 3. Практика\n\n"
        "Практика просит применить эти решения в том же рабочем продукте."
    )
    checker = Section3Checker(SimilarityCalculator())
    monkeypatch.setattr(
        checker,
        "_paragraph_coherence",
        lambda _md: (_ for _ in ()).throw(AssertionError("Markdown coherence path used")),
    )

    items = checker.check("НЕ ДОЛЖНО ИСПОЛЬЗОВАТЬСЯ", document=document)

    assert {item.id for item in items} == {"3.1", "3.2"}
