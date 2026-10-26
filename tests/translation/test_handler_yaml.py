"""YAML: перевод только строковых значений, с сохранением стиля и комментариев.

Файл не пересобирается библиотекой — иначе пропали бы комментарии и блочные скаляры ``>``,
которыми написаны чек-листы Школы 21. Вместо этого у PyYAML берутся координаты скаляров, и текст
подменяется на месте.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from content_factory.translation.handlers.yaml_file import YamlHandler

SAMPLE = """# чек-лист проекта
introduction: >
  - School 21 methodology only makes sense if reviews are taken seriously.

  - Please remain courteous and polite.
quick_actions:
  - EMPTY_WORK
  - CHEAT
language: en
name: Day08
comment: ""
sections:
  - kind: "1"
    name: Main part
"""


@pytest.fixture
def handler() -> YamlHandler:
    return YamlHandler()


def test_keys_are_never_translated(handler: YamlHandler) -> None:
    texts = [u.text for u in handler.extract(SAMPLE.encode("utf-8"))]

    for key in ("introduction", "quick_actions", "language", "name", "sections", "kind"):
        assert key not in texts


def test_codes_without_spaces_are_left_alone(handler: YamlHandler) -> None:
    texts = [u.text for u in handler.extract(SAMPLE.encode("utf-8"))]

    for code in ("EMPTY_WORK", "CHEAT", "en", "Day08", "1", ""):
        assert code not in texts, f"{code!r} — код, а не текст для перевода"


def test_prose_values_become_units(handler: YamlHandler) -> None:
    texts = [u.text for u in handler.extract(SAMPLE.encode("utf-8"))]

    assert "- School 21 methodology only makes sense if reviews are taken seriously." in texts
    assert "- Please remain courteous and polite." in texts
    assert "Main part" in texts


def test_without_translations_the_file_is_byte_identical(handler: YamlHandler) -> None:
    source = SAMPLE.encode("utf-8")

    assert handler.apply(source, {}) == source


def test_the_real_check_list_round_trips_byte_for_byte(handler: YamlHandler) -> None:
    source = Path("DSB10_Intro_to_ML.ID_886521-master/check-list.yml").read_bytes()

    assert handler.apply(source, {}) == source


def test_a_plain_value_is_replaced_in_place(handler: YamlHandler) -> None:
    source = SAMPLE.encode("utf-8")
    unit = next(u for u in handler.extract(source) if u.text == "Main part")

    result = handler.apply(source, {unit.unit_id: "Основная часть"}).decode("utf-8")

    assert "name: Основная часть\n" in result
    assert "language: en\n" in result, "соседние значения не тронуты"


def test_a_folded_block_keeps_its_style_and_indent(handler: YamlHandler) -> None:
    source = SAMPLE.encode("utf-8")
    unit = next(u for u in handler.extract(source) if u.text.startswith("- Please remain"))

    result = handler.apply(source, {unit.unit_id: "- Будь вежлив и доброжелателен."}).decode("utf-8")

    assert "introduction: >\n" in result, "стиль блока сохранён"
    assert "\n  - Будь вежлив и доброжелателен.\n" in result, "отступ блока сохранён"
    assert "\n\n  - Будь" in result, "пустая строка между абзацами на месте"


def test_comments_survive_the_translation(handler: YamlHandler) -> None:
    source = SAMPLE.encode("utf-8")
    unit = next(u for u in handler.extract(source) if u.text == "Main part")

    result = handler.apply(source, {unit.unit_id: "Основная часть"}).decode("utf-8")

    assert result.startswith("# чек-лист проекта\n")


def test_a_quoted_value_stays_quoted(handler: YamlHandler) -> None:
    source = b'title: "Intro to Machine Learning"\n'
    unit = handler.extract(source)[0]

    result = handler.apply(source, {unit.unit_id: "Введение в машинное обучение"}).decode("utf-8")

    assert result == 'title: "Введение в машинное обучение"\n'


def test_broken_yaml_is_a_format_error(handler: YamlHandler) -> None:
    from content_factory.translation.errors import DocumentFormatError

    with pytest.raises(DocumentFormatError):
        handler.extract(b"key: [unclosed\n")
