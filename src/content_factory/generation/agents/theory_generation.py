"""Parsing and repair helpers for TheoryAgent generation output."""

from __future__ import annotations

import re
from typing import Any

from ..config.thresholds import THRESHOLDS
from ..models.schemas import ProjectSeed
from .theory_sanitizer import _sanitize_theory_body_text


def pick_theory_parts_count(desired: int) -> int:
    """Clamp desired theory parts count to configured bounds."""
    lo, hi = THRESHOLDS["theory_parts"]
    if desired < lo:
        return lo
    if desired > hi:
        return hi
    return desired


def theory_anchor_terms(seed: ProjectSeed) -> list[str]:
    """Collect semantic anchor terms from seed inputs."""
    terms: set[str] = set()
    for item in seed.required_tools or []:
        if item:
            terms.add(item.lower())
    for item in seed.skills or []:
        if item:
            terms.add(item.lower())
    for item in seed.learning_outcomes or []:
        if item:
            terms.update(re.findall(r"\w+", item.lower()))
    if seed.project_description:
        terms.update(word for word in re.findall(r"\w+", seed.project_description.lower()) if len(word) > 4)
    return [term for term in terms if len(term) >= 4]


def semantic_cover(body: str, learning_outcomes: list[str]) -> list[str]:
    """Detect rough learning-outcome coverage by token overlap."""
    covered: list[str] = []
    body_low = body.lower()
    for lo_text in learning_outcomes:
        if any(token in body_low for token in re.findall(r"\w+", lo_text.lower())):
            covered.append(lo_text)
    return list(dict.fromkeys(covered))[:4]


def validate_bridge_questions(questions: list[str], seed: ProjectSeed, anchors: list[str]) -> bool:
    """Validate generated bridge questions for specificity and project anchoring."""
    if not questions:
        return False
    bad_starts = ("что такое", "перечисли", "объясни", "расскажи", "определи")
    has_anchor = False
    for question in questions:
        question_low = question.strip().lower()
        if len(question_low) < 25 or len(question_low) > 180:
            return False
        if question_low.startswith(bad_starts):
            return False
        if anchors and any(anchor in question_low for anchor in anchors):
            has_anchor = True
    if anchors and not has_anchor:
        return False
    return True


def strip_theory_chapter_heading(markdown: str) -> str:
    """Remove accidental Chapter 2 headers from an LLM response."""
    text = re.sub(r"^##\s+Глава\s+2[^\n]*\n", "", markdown or "", flags=re.M)
    text = re.sub(r"^###\s+Глава\s+2[^\n]*\n", "", text, flags=re.M)
    text = re.sub(r"^\*\*Глава\s+2[^\*]+\*\*\s*\n?", "", text, flags=re.M)
    text = re.sub(r"^\s*\*\*Глава\s+2[^\*]+\*\*\s*\n?", "", text, flags=re.M)
    return text.strip()


def parse_theory_part_blocks(
    markdown: str,
    *,
    rx_part: re.Pattern[str],
    rx_example: re.Pattern[str],
    rx_qs: re.Pattern[str],
    seed: ProjectSeed,
    style_rewrite: Any,
    anchors: list[str],
) -> tuple[list[dict[str, Any]], list[int], list[int]]:
    """Parse generated markdown into interim theory part dictionaries."""
    indices = [match.start() for match in rx_part.finditer(markdown)] + [len(markdown)]
    header_matches = list(rx_part.finditer(markdown))
    parts_data: list[dict[str, Any]] = []
    examples_to_generate: list[int] = []
    questions_to_generate: list[int] = []

    for idx, header in enumerate(header_matches):
        start = header.end()
        end = indices[idx + 1]
        body_block = markdown[start:end].strip()
        title = _heading_title(header)

        example_match = rx_example.search(body_block)
        example = example_match.group(1).strip() if example_match else ""

        questions_match = rx_qs.search(body_block)
        questions: list[str] = []
        if questions_match:
            questions_text = questions_match.group(1).strip()
            questions = [
                question.strip("- •\t ").rstrip()
                for question in questions_text.splitlines()
                if question.strip()
            ][:2]

        main_body = body_block.split("**Пример:**", 1)[0].strip()
        if "**Вопросы к практике:**" in main_body:
            main_body = main_body.split("**Вопросы к практике:**", 1)[0].strip()
        main_body = style_rewrite(main_body, seed.language)
        lo, hi = THRESHOLDS["theory_words_per_part"]
        main_body = _sanitize_theory_body_text(main_body, title, seed, anchors, lo, hi)

        parts_data.append(
            {
                "title": title,
                "body_block": body_block,
                "main_body": main_body,
                "example": example,
                "qs": questions,
            }
        )
        if not example:
            examples_to_generate.append(idx)
        if not questions:
            questions_to_generate.append(idx)

    return parts_data, examples_to_generate, questions_to_generate


def _heading_title(header: re.Match[str]) -> str:
    """Return the heading title from a canonical theory heading match."""
    for group_index in range((header.lastindex or 0), 0, -1):
        value = header.group(group_index)
        if value and not value.isdigit():
            return value.strip()
    return ""


def build_theory_example_prompt(part_data: dict[str, Any]) -> str:
    """Build prompt for a missing theory example."""
    return f"""Для части теории нужно сгенерировать краткий пример (1-3 предложения).

Название части: {part_data['title']}
Основной текст части:
{part_data['body_block'][:300]}...

Сгенерируй краткий пример из реальной жизни, иллюстрирующий концепцию из этой части.

Формат вывода (строго):
**Пример:** <1-3 предложения, короткий кейс>"""


def parse_theory_example_response(markdown: str) -> str:
    """Extract example body from an LLM response."""
    example_match = re.search(r"\*\*Пример:\*\*\s*(.+?)(?=\n\*\*|\Z)", markdown or "", re.S)
    if example_match:
        return example_match.group(1).strip()
    return "Пример из практики, иллюстрирующий применение концепции."


def parse_theory_questions_response(markdown: str) -> list[str]:
    """Extract bridge questions from an LLM response."""
    questions_match = re.search(r"\*\*Вопросы к практике:\*\*([\s\S]+?)(?=\n\*\*|\Z)", markdown or "", re.M)
    if not questions_match:
        return []
    questions_text = questions_match.group(1).strip()
    return [
        question.strip("- •\t ").rstrip()
        for question in questions_text.splitlines()
        if question.strip()
    ][:2]
