from content_gen.regeneration_pipeline import (
    RegenerationValidationReport,
    apply_typed_patch_set,
    build_regeneration_pipeline_input,
    parse_typed_patch_set,
)


def test_build_regeneration_pipeline_input_preserves_selected_sections_and_instructions() -> None:
    markdown = """# Старое название

## 2.1. Пример
Старый пример.
"""
    comments = """Правка 1: 2.1. Пример
Диапазон строк: 3-4
Что исправить: Сделай пример конкретнее.
Что оставить: Заголовок и учебный смысл.
"""

    pipeline_input = build_regeneration_pipeline_input(
        original_md=markdown,
        comments=comments,
        language="ru",
    )

    assert pipeline_input.is_scoped is True
    assert pipeline_input.change_intent == "local_section_edit"
    assert pipeline_input.allowed_line_ranges() == [(3, 4, "2.1. Пример")]
    assert pipeline_input.selected_sections[0].instruction.change == "Сделай пример конкретнее."
    assert pipeline_input.selected_sections[0].instruction.keep == "Заголовок и учебный смысл."


def test_parse_typed_patch_set_rejects_invalid_schema() -> None:
    patch_set, issues = parse_typed_patch_set(
        """
        {
          "changes": [
            {
              "location_hint": "пример",
              "old_text": "Старый пример.",
              "new_text": "Новый пример.",
              "unexpected": "must fail"
            }
          ]
        }
        """
    )

    assert patch_set is None
    assert issues[0].severity == "error"
    assert issues[0].code == "patch_schema_invalid"


def test_apply_typed_patch_set_reports_partial_scoped_application() -> None:
    markdown = """# README

Этот раздел нельзя менять.

## 2.1. Пример
Старый пример.
"""
    comments = """Правка 1: 2.1. Пример
Диапазон строк: 5-6
Что исправить: Замени пример.
"""
    pipeline_input = build_regeneration_pipeline_input(
        original_md=markdown,
        comments=comments,
        language="ru",
    )
    patch_set, issues = parse_typed_patch_set(
        """
        {
          "changes": [
            {
              "location_hint": "пример",
              "old_text": "Старый пример.",
              "new_text": "Новый пример."
            },
            {
              "location_hint": "чужой раздел",
              "old_text": "Этот раздел нельзя менять.",
              "new_text": "Сломанный текст."
            }
          ]
        }
        """
    )
    report = RegenerationValidationReport.from_input(pipeline_input)
    for issue in issues:
        report.issues.append(issue)

    assert patch_set is not None
    result = apply_typed_patch_set(
        markdown=markdown,
        patch_set=patch_set,
        pipeline_input=pipeline_input,
        report=report,
    )

    assert "Новый пример." in result.result_md
    assert "Сломанный текст." not in result.result_md
    assert report.requested_patch_count == 2
    assert report.applied_patch_count == 1
    assert report.failed_patch_count == 1
    assert report.issues[-1].code == "patch_not_applied"


def test_build_regeneration_pipeline_input_marks_new_chapter_as_structural() -> None:
    markdown = """# README

## Содержание
- [Глава 1. Введение и инструкция](#глава-1-введение-и-инструкция)
- [Глава 2. Теоретический блок](#глава-2-теоретический-блок)
- [Глава 3. Практический блок](#глава-3-практический-блок)

## Глава 1. Введение и инструкция
Текст.

## Глава 2. Теоретический блок
Текст.

## Глава 3. Практический блок
Текст.
"""
    comments = """Правка 1: Глава 2. Теоретический блок
Диапазон строк: 10-11
Что исправить: Добавь новую главу про критерии финального ревью.
"""

    pipeline_input = build_regeneration_pipeline_input(
        original_md=markdown,
        comments=comments,
        language="ru",
    )

    assert pipeline_input.change_intent == "structural_document_edit"
    assert pipeline_input.is_structural is True
    assert pipeline_input.is_scoped is False
    assert pipeline_input.selected_sections[0].title == "Глава 2. Теоретический блок"
