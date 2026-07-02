"""Didactics manifest package."""

from .composer import compose_didactics_context, get_didactics_trace
from .loader import DidacticsManifest, build_didactics_context, load_didactics_manifest
from .patterns import (
    PRACTICE_TASK_PARSE_PATTERN,
    PRACTICE_TASK_TITLE_PATTERN_STRICT,
    THEORY_PART_PARSE_PATTERN,
    THEORY_PART_SPLIT_PATTERN,
    THEORY_PART_TITLE_PATTERN,
    compile_practice_task_parse,
    compile_practice_task_title,
    compile_theory_part_parse,
    compile_theory_part_split,
    compile_theory_part_title,
)

__all__ = [
    "DidacticsManifest",
    "load_didactics_manifest",
    "build_didactics_context",
    "compose_didactics_context",
    "get_didactics_trace",
    "THEORY_PART_TITLE_PATTERN",
    "THEORY_PART_SPLIT_PATTERN",
    "THEORY_PART_PARSE_PATTERN",
    "PRACTICE_TASK_TITLE_PATTERN_STRICT",
    "PRACTICE_TASK_PARSE_PATTERN",
    "compile_theory_part_title",
    "compile_theory_part_split",
    "compile_theory_part_parse",
    "compile_practice_task_title",
    "compile_practice_task_parse",
]
