"""Тесты evidence-сигналов дидактической оси."""

from content_factory.generation.evaluation.didactic.signals import collect_signals


def test_collect_signals_clean_document() -> None:
    md = (
        "# Проект\n\n## Глава 2. Теория\n\n### 2.1. Раздел\n"
        "Команда планирует спринт и фиксирует цели. "
        "Аналитик описывает требования и риски продукта.\n\n"
        "**Пример:** команда сверяет решение с требованиями.\n"
    )
    signals = collect_signals(md)
    assert signals["repetition_ratio"] == 0.0
    assert signals["near_dup"] == 0
    assert signals["broken_tables"] == 0
    assert signals["example_count"] == 1
    assert signals["directive_hits"] == 0


def test_collect_signals_flags_repetition_and_directives() -> None:
    sentence = "Это типовое предложение про рабочий процесс проекта команды снова. "
    md = sentence * 6 + "\nСделай шаг, нажми кнопку, введите данные."
    signals = collect_signals(md)
    assert signals["near_dup"] > 4 or signals["repetition_ratio"] > 0.0
    assert signals["directive_hits"] >= 2


def test_collect_signals_counts_broken_table() -> None:
    merged = "| " + "очень длинная слитая ячейка " * 10 + " | вторая колонка |"
    assert len(merged) > 200
    signals = collect_signals(merged)
    assert signals["broken_tables"] >= 1
