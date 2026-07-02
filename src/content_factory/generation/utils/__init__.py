"""Утилиты для генератора контента."""

from .text_analysis import (
    clean_markdown_for_counting,
    count_words,
    extract_text_between_markers,
    has_practice_questions,
    has_term_definitions,
)

__all__ = [
    "count_words",
    "has_term_definitions",
    "has_practice_questions",
    "clean_markdown_for_counting",
    "extract_text_between_markers",
]
