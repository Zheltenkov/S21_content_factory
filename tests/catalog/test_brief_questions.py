"""BriefQuestion entity + blocking taxonomy (project-contract epic, slice 7)."""

from __future__ import annotations

from content_factory.catalog.pipeline.curriculum.brief_questions import (
    classify_question,
    classify_questions,
    count_blocking,
)


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
