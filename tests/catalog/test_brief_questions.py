"""BriefQuestion entity + blocking taxonomy (project-contract epic, slice 7)."""

from __future__ import annotations

from content_factory.catalog.pipeline.curriculum.brief_questions import (
    classify_question,
    classify_questions,
    count_blocking,
    question_key,
)
from content_factory.catalog.pipeline.curriculum.journey import _extract_open_questions


def test_audience_practice_demo_role_questions_block() -> None:
    for text in (
        "Какая целевая аудитория программы?",
        "В каком формате практики оценивать задания?",
        "Как измерить demo на demo-day?",
        "Какая целевая роль выпускника?",
    ):
        question = classify_question(text)
        assert question.blocking is True
        assert question.category != "uncategorized"


def test_editorial_question_is_non_blocking() -> None:
    question = classify_question("Можно переформулировать название второго блока?")
    assert question.category == "editorial"
    assert question.blocking is False


def test_storytelling_and_cases_questions_are_editorial() -> None:
    for text in ("Какие кейсы использовать в проектах?", "Какой сторителлинг взять за основу?"):
        question = classify_question(text)
        assert question.category == "editorial"
        assert question.blocking is False


def test_unmatched_question_blocks_by_default() -> None:
    question = classify_question("Нужен ли отдельный вводный проект?")
    assert question.category == "uncategorized"
    assert question.blocking is True  # conservative default


def test_count_blocking_ignores_editorial_and_answered() -> None:
    questions = classify_questions(
        [
            "Какая целевая аудитория?",  # blocking
            "Поправить опечатку в названии?",  # editorial, non-blocking
        ]
    )
    assert count_blocking(questions) == 1


def test_answered_question_not_counted() -> None:
    from dataclasses import replace

    blocking = classify_question("Какая целевая аудитория?")
    answered = replace(blocking, status="answered")
    assert count_blocking((answered,)) == 0


def test_csv_questions_are_extracted_atomically_without_prefix_or_truncation() -> None:
    raw_text = (
        "Аудитория | Кто принимает решение о покупке? Какие ограничения доступа учитывать?\n"
        "Какие темы включить?: Исследование, MVP, запуск\n"
        "Инструменты | Доступен ли участникам тестовый стенд?"
    )

    questions = _extract_open_questions(raw_text)

    assert questions == (
        "Кто принимает решение о покупке?",
        "Какие ограничения доступа учитывать?",
        "Доступен ли участникам тестовый стенд?",
    )


def test_question_extraction_discards_long_declarative_prefix() -> None:
    raw_text = (
        "Портрет участника: способен исследовать рынок, собрать MVP и запустить продукт. "
        "Портрет эксперта Какие дополнительные ресурсы нужны для запуска?"
    )

    assert _extract_open_questions(raw_text) == (
        "Какие дополнительные ресурсы нужны для запуска?",
    )


def test_question_extraction_keeps_context_for_deictic_question() -> None:
    raw_text = (
        "доступ к среде разработки с поддержкой искусственного интеллекта; "
        "а это что такое?"
    )

    assert _extract_open_questions(raw_text) == (
        "Что такое «доступ к среде разработки с поддержкой искусственного интеллекта»?",
    )


def test_question_answers_close_only_matching_stable_question() -> None:
    audience = "Какая целевая аудитория?"
    practice = "В каком формате проверять задания?"

    questions = classify_questions(
        [audience, practice],
        answers={question_key(audience): "Основатели цифровых продуктов уровня junior."},
    )

    assert questions[0].status == "answered"
    assert questions[0].answer.startswith("Основатели")
    assert questions[1].status == "open"
    assert count_blocking(questions) == 1
