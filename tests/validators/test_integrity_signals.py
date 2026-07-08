"""Тесты сигналов целостности (structural v2, N.1–N.5)."""

from content_factory.generation.validators.integrity_signals import (
    all_integrity_signals,
    diagram_topic_match,
    orphaned_quotes,
    project_id_unity,
    table_integrity,
    verbatim_repetition,
)


def test_table_integrity_passes_on_clean_table() -> None:
    md = (
        "| Этап | Артефакт | Срок |\n"
        "| --- | --- | --- |\n"
        "| Планирование | Бэклог | 1 неделя |\n"
        "| Реализация | Отчёт | 2 недели |\n"
    )
    assert table_integrity(md).passed


def test_table_integrity_flags_row_merged_with_prose() -> None:
    merged = "| " + "очень длинная ячейка данных " * 10 + " | вторая колонка |"
    assert len(merged) > 200 and merged.count("|") >= 2
    signal = table_integrity(merged)
    assert not signal.passed
    assert signal.details["merged_rows"]


def test_table_integrity_flags_column_count_mismatch() -> None:
    md = "| a | b | c |\n| --- | --- | --- |\n| x |\n"
    signal = table_integrity(md)
    assert not signal.passed
    assert signal.details["col_issues"] >= 1


def test_verbatim_repetition_passes_on_varied_prose() -> None:
    md = (
        "Команда планирует спринт и фиксирует цели проекта. "
        "Затем аналитик описывает требования к продукту и риски. "
        "Разработчик готовит архитектуру решения для новой задачи."
    )
    assert verbatim_repetition(md).passed


def test_verbatim_repetition_flags_duplicated_sentences() -> None:
    sentence = "Это типовое предложение про рабочий процесс проекта команды. "
    md = sentence * 6
    signal = verbatim_repetition(md)
    assert not signal.passed
    assert signal.details["near_dup"] > 4 or signal.details["repetition_ratio"] > 0.06


def test_diagram_topic_match_passes_when_nodes_match_heading() -> None:
    md = (
        "## Планирование проекта\n\n"
        "```mermaid\nflowchart TD\nПланирование --> Проекта\nПроекта --> Задача\n```\n"
    )
    assert diagram_topic_match(md).passed


def test_diagram_topic_match_flags_off_topic_diagram() -> None:
    md = (
        "## Введение\n\n"
        "```mermaid\nflowchart TD\nКартофель --> Морковь\nМорковь --> Свёкла\n```\n"
    )
    signal = diagram_topic_match(md)
    assert not signal.passed
    assert signal.details["mismatched"]


def test_orphaned_quotes_passes_on_balanced_quotes() -> None:
    assert orphaned_quotes("Он сказал «привет» команде.").passed


def test_orphaned_quotes_flags_orphaned_closing_quote() -> None:
    signal = orphaned_quotes("Текст без открытия, но с закрытием» в конце.")
    assert not signal.passed
    assert signal.details["orphan"] >= 1


def test_project_id_unity_passes_with_single_id() -> None:
    assert project_id_unity("Проект PjM15_PublicSpeaking описан ниже.").passed


def test_project_id_unity_flags_multiple_ids() -> None:
    signal = project_id_unity("Смешаны PjM15_PublicSpeaking и BSA07_DataFlow в теле.")
    assert not signal.passed
    assert len(signal.details["ids"]) == 2


def test_all_integrity_signals_returns_five_ids() -> None:
    ids = [s.id for s in all_integrity_signals("# Проект\n\nТекст.")]
    assert ids == ["N.1", "N.2", "N.3", "N.4", "N.5"]
