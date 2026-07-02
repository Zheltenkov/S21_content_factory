"""Shared didactics regex patterns for generation and validation."""

from __future__ import annotations

import re

# Theory headers
THEORY_PART_TITLE_PATTERN = r"^###\s+2\.\d+\."
THEORY_PART_SPLIT_PATTERN = r"(?=^###\s+2\.\d+\.)"
THEORY_PART_PARSE_PATTERN = r"^###\s+2\.(\d+)\.\s*(.+?)\s*$"

# Practice headers
PRACTICE_TASK_TITLE_PATTERN_STRICT = r"^###\s+Задани(?:е|я)\s+\d+\."
PRACTICE_TASK_PARSE_PATTERN = r"^###\s+Задани(?:е|я)\s+(\d+)\.\s*(.+?)\s*$"


def compile_theory_part_title() -> re.Pattern[str]:
    return re.compile(THEORY_PART_TITLE_PATTERN, re.M)


def compile_theory_part_split() -> re.Pattern[str]:
    return re.compile(THEORY_PART_SPLIT_PATTERN, re.M)


def compile_theory_part_parse() -> re.Pattern[str]:
    return re.compile(THEORY_PART_PARSE_PATTERN, re.M)


def compile_practice_task_title(strict: bool = True) -> re.Pattern[str]:
    return re.compile(PRACTICE_TASK_TITLE_PATTERN_STRICT, re.M)


def compile_practice_task_parse() -> re.Pattern[str]:
    return re.compile(PRACTICE_TASK_PARSE_PATTERN, re.M)
