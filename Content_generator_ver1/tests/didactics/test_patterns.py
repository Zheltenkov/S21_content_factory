"""Tests for shared didactics regex patterns."""

from content_gen.didactics.patterns import (
    compile_practice_task_parse,
    compile_practice_task_title,
    compile_theory_part_title,
)


def test_theory_title_pattern_accepts_canonical_format_only():
    rx = compile_theory_part_title()
    text = "### 2.1. Основы\n\nТекст\n\n### Часть 2. Практика\n\nТекст"
    matches = rx.findall(text)
    assert len(matches) == 1


def test_practice_title_strict_contract():
    strict_rx = compile_practice_task_title(strict=True)
    assert strict_rx.search("### Задание 1. Настройка среды")
    assert not strict_rx.search("### Задача 1. Настройка среды")


def test_practice_parse_accepts_canonical_zadanie_only():
    rx = compile_practice_task_parse()
    m1 = rx.search("### Задание 3. ETL пайплайн")
    m2 = rx.search("### Задача 4. ETL пайплайн")
    assert m1 and m1.group(1) == "3"
    assert m2 is None
