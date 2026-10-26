"""Отчёт о переводе архива.

Отчёт — единственное место, где методист узнаёт, что именно произошло с каждым файлом.
Поэтому проверяется не «отчёт собрался», а что в нём названы и успех, и отказ, и причина.
"""

from __future__ import annotations

from content_factory.translation.report import FileOutcome, build_report


def test_a_translated_file_is_named_with_its_count() -> None:
    report = build_report(
        [FileOutcome(path="README.md", status="переведён", units=62)],
        target_language="en",
    )

    assert "README.md" in report
    assert "62" in report


def test_a_skipped_file_carries_its_reason() -> None:
    report = build_report(
        [FileOutcome(path="logo.png", status="пропущен", units=0, detail="формат не переводится")],
        target_language="en",
    )

    assert "logo.png" in report
    assert "формат не переводится" in report


def test_a_failure_is_not_hidden() -> None:
    """Упавший файл остаётся исходным, и отчёт обязан сказать, что он не переведён."""
    report = build_report(
        [FileOutcome(path="notes.docx", status="ошибка", units=0, detail="файл повреждён")],
        target_language="en",
    )

    assert "notes.docx" in report
    assert "файл повреждён" in report


def test_the_counts_add_up_in_the_summary() -> None:
    report = build_report(
        [
            FileOutcome(path="a.md", status="переведён", units=10),
            FileOutcome(path="b.md", status="переведён", units=5),
            FileOutcome(path="c.png", status="пропущен", units=0, detail="формат не переводится"),
            FileOutcome(path="d.docx", status="ошибка", units=0, detail="повреждён"),
        ],
        target_language="en",
    )

    assert "переведено файлов: 2" in report
    assert "пропущено: 1" in report
    assert "с ошибкой: 1" in report


def test_the_target_language_is_stated() -> None:
    report = build_report([], target_language="en")

    assert "en" in report


def test_a_file_left_unchanged_by_its_handler_is_called_out() -> None:
    """Обработчик возвращает исходные байты, когда перевод сломал бы файл. Это не успех."""
    report = build_report(
        [
            FileOutcome(
                path="src/main.c",
                status="без изменений",
                units=3,
                detail="проверка неизменности кода не прошла",
            )
        ],
        target_language="en",
    )

    assert "src/main.c" in report
    assert "проверка неизменности кода не прошла" in report
    assert "без изменений: 1" in report
