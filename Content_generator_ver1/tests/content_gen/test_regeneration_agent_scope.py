from content_gen.agents.regeneration import RegenerationAgent


class FakeLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, str]] = []

    def complete(self, *, system: str, user: str, **kwargs):
        self.calls.append({"system": system, "user": user, "kwargs": kwargs})
        if not self.responses:
            raise AssertionError("Unexpected LLM call")
        return self.responses.pop(0)


def test_regeneration_agent_rejects_patch_outside_selected_scopes() -> None:
    original = """# Старое название

## 2.1. Пример
Старый пример показывает переговоры.

## 2.2. Другое
Этот раздел нельзя менять.
"""
    comments = """Правка 1: Название проекта
Диапазон строк: 1-1
Что исправить: Измени название проекта на «Новое название».

Правка 2: 2.1. Пример
Диапазон строк: 3-4
Что исправить: Замени пример в части 2.1.
"""
    llm = FakeLLM(
        [
            """
            {
              "changes": [
                {
                  "location_hint": "название проекта",
                  "old_text": "# Старое название",
                  "new_text": "# Новое название"
                },
                {
                  "location_hint": "пример 2.1",
                  "old_text": "Старый пример показывает переговоры.",
                  "new_text": "Новый пример показывает переговоры с заказчиком."
                },
                {
                  "location_hint": "чужой раздел",
                  "old_text": "Этот раздел нельзя менять.",
                  "new_text": "Сломанный текст."
                }
              ]
            }
            """
        ]
    )

    result = RegenerationAgent(llm).regenerate(original, comments)

    assert "# Новое название" in result.regenerated_md
    assert "Новый пример показывает переговоры с заказчиком." in result.regenerated_md
    assert "Этот раздел нельзя менять." in result.regenerated_md
    assert "Сломанный текст." not in result.regenerated_md
    assert result.validation_report is not None
    assert result.validation_report["scoped"] is True
    assert result.validation_report["requested_patch_count"] == 3
    assert result.validation_report["applied_patch_count"] == 2
    assert result.validation_report["failed_patch_count"] == 1
    assert len(llm.calls) == 1


def test_regeneration_agent_scoped_fallback_replaces_only_selected_section() -> None:
    original = """# README

## 2.1. Пример
Старый пример показывает переговоры.

## 2.2. Другое
Этот раздел нельзя менять.
"""
    comments = """Правка 1: 2.1. Пример
Диапазон строк: 3-4
Что исправить: Замени пример в части 2.1.
"""
    llm = FakeLLM(
        [
            "Ответ без JSON",
            "## 2.1. Пример\nНовый пример показывает переговоры с заказчиком.",
        ]
    )

    result = RegenerationAgent(llm).regenerate(original, comments)

    assert "## 2.1. Пример\nНовый пример показывает переговоры с заказчиком." in result.regenerated_md
    assert "## 2.2. Другое\nЭтот раздел нельзя менять." in result.regenerated_md
    assert result.validation_report is not None
    assert result.validation_report["apply_mode"] == "scoped_rewrite_fallback"
    assert result.validation_report["selected_sections"][0]["title"] == "2.1. Пример"
    assert len(llm.calls) == 2


def test_regeneration_agent_does_not_use_full_fallback_for_selected_scopes() -> None:
    original = """# README

## 2.1. Пример
Старый пример показывает переговоры.

## 2.2. Другое
Этот раздел нельзя менять.
"""
    comments = """Правка 1: 2.1. Пример
Диапазон строк: 3-4
Что исправить: Замени пример в части 2.1.
"""
    llm = FakeLLM(
        [
            "Ответ без JSON",
            "## 2.1. Пример\nСтарый пример показывает переговоры.",
            "# README\n\n## 2.2. Другое\nСломанный текст.",
        ]
    )

    result = RegenerationAgent(llm).regenerate(original, comments)

    assert result.regenerated_md == original
    assert "выбранные части README" in " ".join(result.changes)
    assert result.validation_report is not None
    assert result.validation_report["changed"] is False
    assert result.validation_report["issues"][-1]["code"] == "scoped_fallback_no_change"
    assert len(llm.calls) == 2


def test_regeneration_agent_treats_new_chapter_request_as_structural() -> None:
    original = """# README

## Содержание
- [Глава 1. Введение и инструкция](#глава-1-введение-и-инструкция)
- [Глава 2. Теоретический блок](#глава-2-теоретический-блок)
- [Глава 3. Практический блок](#глава-3-практический-блок)

## Глава 1. Введение и инструкция
Введение.

## Глава 2. Теоретический блок
Теория.

## Глава 3. Практический блок
Практика.
"""
    comments = """Правка 1: Глава 2. Теоретический блок
Диапазон строк: 10-11
Что исправить: Добавь новую главу про финальное ревью.
"""
    llm = FakeLLM(
        [
            """
            {
              "changes": [
                {
                  "location_hint": "новая глава",
                  "old_text": "## Глава 3. Практический блок\\nПрактика.",
                  "new_text": "## Глава 3. Практический блок\\nПрактика.\\n\\n## Глава 4. Финальное ревью\\nПроверь результат перед сдачей."
                }
              ]
            }
            """
        ]
    )

    result = RegenerationAgent(llm).regenerate(original, comments)

    assert "## Глава 4. Финальное ревью" in result.regenerated_md
    assert "## Глава 2. Теоретический блок" in result.regenerated_md
    assert "## Глава 3. Практический блок" in result.regenerated_md
    assert result.validation_report is not None
    assert result.validation_report["change_intent"] == "structural_document_edit"
    assert result.validation_report["scoped"] is False
    assert "РЕЖИМ СТРУКТУРНОЙ ПРАВКИ" in llm.calls[0]["user"]
