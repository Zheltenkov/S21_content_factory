"""Определение языка наполнения — дёшево и по файлу целиком.

Нужно в двух местах: по нему решается, трогать ли файл с данными, и по нему же в описи архива
видно, что документы проекта английские, а комментарии в его скриптах — русские.
"""

from __future__ import annotations

import pytest

from content_factory.translation.language import dominant_language, looks_russian


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Введение в машинное обучение", True),
        ("Intro to Machine Learning", False),
        ("", False),
        ("12345", False),
        ("2026-10-26", False),
        ("Python 3.12", False),
        ("Сегодня мы разберём Python 3.12", True),
        ("ID_886521", False),
    ],
)
def test_russian_is_recognised_by_its_letters(text: str, expected: bool) -> None:
    assert looks_russian(text) is expected


def test_a_file_is_judged_as_a_whole() -> None:
    cells = ["Электроника", "Беспроводная мышь", "1200", "Канцелярия"]

    assert dominant_language(cells) == "ru"


def test_an_english_file_is_judged_english() -> None:
    cells = ["Electronics", "Wireless mouse", "1200", "Stationery"]

    assert dominant_language(cells) == "other"


def test_a_mixed_file_goes_by_the_majority() -> None:
    cells = ["Электроника", "Беспроводная мышь", "Wireless keyboard"]

    assert dominant_language(cells) == "ru"


def test_a_file_without_letters_has_no_language() -> None:
    assert dominant_language(["1", "2", "2026-10-26"]) == "unknown"
