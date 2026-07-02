from content_gen.utils.regeneration_scope import (
    detect_regeneration_change_intent,
    parse_regeneration_edit_scopes,
    render_structural_change_contract,
    render_scope_contract,
    slice_markdown_by_scope,
)


def test_parse_regeneration_scopes_excludes_saved_history_and_narrows_project_title() -> None:
    markdown = """# Старое название

Аннотация не должна входить в правку названия.

## 2.1. Пример
Старый пример.
"""
    comments = """Сохранённая правка 1: 2.1. Пример
Диапазон строк: 5-6
Что исправить: Уже применено.

Правка 1: Название проекта
Диапазон строк: 1-3
Что исправить: Измени название проекта.
"""

    scopes = parse_regeneration_edit_scopes(comments, markdown)

    assert len(scopes) == 1
    assert scopes[0].title == "Название проекта"
    assert scopes[0].start_line == 1
    assert scopes[0].end_line == 1
    assert slice_markdown_by_scope(markdown, scopes[0]) == "# Старое название"


def test_parse_regeneration_scopes_can_infer_numbered_section_from_free_text() -> None:
    markdown = """# README

## 2.1. Пример
Старый пример.

## 2.2. Другое
Не менять.
"""

    scopes = parse_regeneration_edit_scopes("Исправь пример в части 2.1", markdown)

    assert len(scopes) == 1
    assert scopes[0].title == "2.1. Пример"
    assert scopes[0].start_line == 3
    assert scopes[0].end_line == 5


def test_render_scope_contract_contains_hard_boundary_language() -> None:
    markdown = "# README\n\n## 2.1. Пример\nСтарый пример."
    comments = """Правка 1: 2.1. Пример
Диапазон строк: 3-4
Что исправить: Замени пример.
"""
    scopes = parse_regeneration_edit_scopes(comments, markdown)

    contract = render_scope_contract(scopes, markdown)

    assert "РАЗРЕШЁННЫЕ ОБЛАСТИ ПРАВОК" in contract
    assert "строки 3-4" in contract
    assert "остальные строки README должны остаться байт-в-байт" in contract


def test_detect_regeneration_change_intent_distinguishes_structural_chapter_request() -> None:
    markdown = "# README\n\n## Глава 2. Теоретический блок\nТекст."

    assert detect_regeneration_change_intent("Добавь новую главу про финальное ревью", markdown) == (
        "structural_document_edit"
    )
    assert detect_regeneration_change_intent("Добавь больше материала в раздел 2.1", markdown) == (
        "local_section_edit"
    )


def test_render_structural_change_contract_preserves_core_chapters() -> None:
    markdown = """# README

## Глава 1. Введение и инструкция
Текст.

## Глава 2. Теоретический блок
Текст.

## Глава 3. Практический блок
Текст.
"""

    contract = render_structural_change_contract([], markdown)

    assert "РЕЖИМ СТРУКТУРНОЙ ПРАВКИ" in contract
    assert "Не сдвигай номера глав 1-3" in contract
    assert "Глава 2. Теоретический блок" in contract
