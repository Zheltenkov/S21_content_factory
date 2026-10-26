"""Данные-примеры: csv и tsv.

Правило принято пользователем: файл трогаем, только если наполнение русское. Английский пример
остаётся как есть — иначе код учащегося, читающий колонку по имени, перестанет работать.
Английские заголовки не переводим никогда, русские — вместе с содержимым.
"""

from __future__ import annotations

import pytest

from content_factory.translation.handlers.tabular import CsvHandler, TsvHandler

ENGLISH = "id,category,description,price\n1,Electronics,Wireless mouse,1200\n"
RUSSIAN = "id,категория,описание,цена\n1,Электроника,Беспроводная мышь,1200\n"
MIXED_HEADERS = "id,category,description,price\n1,Электроника,Беспроводная мышь,1200\n"


@pytest.fixture
def csv() -> CsvHandler:
    return CsvHandler()


def _texts(handler, text: str) -> list[str]:
    return [u.text for u in handler.extract(text.encode("utf-8"))]


def test_an_english_file_is_not_touched_at_all(csv: CsvHandler) -> None:
    assert _texts(csv, ENGLISH) == []
    assert csv.apply(ENGLISH.encode("utf-8"), {}) == ENGLISH.encode("utf-8")


def test_russian_cells_become_units(csv: CsvHandler) -> None:
    texts = _texts(csv, RUSSIAN)

    assert "Электроника" in texts
    assert "Беспроводная мышь" in texts


def test_english_headers_stay_even_in_a_russian_file(csv: CsvHandler) -> None:
    texts = _texts(csv, MIXED_HEADERS)

    assert "category" not in texts and "description" not in texts and "price" not in texts
    assert "Электроника" in texts, "содержимое при этом переводится"


def test_russian_headers_are_translated(csv: CsvHandler) -> None:
    texts = _texts(csv, RUSSIAN)

    assert "категория" in texts
    assert "описание" in texts


def test_numbers_identifiers_and_dates_are_never_units(csv: CsvHandler) -> None:
    source = "id,дата,цена\nA-1,2026-10-26,1200\n"

    texts = _texts(csv, source)

    assert "1200" not in texts
    assert "2026-10-26" not in texts
    assert "A-1" not in texts


def test_without_translations_the_file_is_byte_identical(csv: CsvHandler) -> None:
    source = RUSSIAN.encode("utf-8")

    assert csv.apply(source, {}) == source


def test_a_translated_cell_replaces_only_itself(csv: CsvHandler) -> None:
    source = RUSSIAN.encode("utf-8")
    unit = next(u for u in csv.extract(source) if u.text == "Электроника")

    result = csv.apply(source, {unit.unit_id: "Electronics"}).decode("utf-8")

    assert "1,Electronics,Беспроводная мышь,1200\n" in result
    assert result.startswith("id,категория,описание,цена\n"), "заголовки не тронуты"


def test_a_translation_with_the_separator_is_quoted(csv: CsvHandler) -> None:
    """Запятая в переводе разорвала бы строку на лишнюю колонку."""
    source = RUSSIAN.encode("utf-8")
    unit = next(u for u in csv.extract(source) if u.text == "Беспроводная мышь")

    result = csv.apply(source, {unit.unit_id: "Mouse, wireless"}).decode("utf-8")

    assert '"Mouse, wireless"' in result
    assert len(result.splitlines()[1].split(",")) == 5, "колонок стало бы больше без кавычек"


def test_a_quoted_cell_keeps_its_quotes(csv: CsvHandler) -> None:
    source = 'id,описание\n1,"Мышь, беспроводная"\n'.encode()

    texts = _texts(csv, source.decode("utf-8"))

    assert "Мышь, беспроводная" in texts, "кавычки не часть значения"


def test_tsv_uses_a_tab(csv: CsvHandler) -> None:
    source = "id\tописание\n1\tБеспроводная мышь\n"

    assert "Беспроводная мышь" in _texts(TsvHandler(), source)
