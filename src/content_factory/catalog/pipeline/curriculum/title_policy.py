"""Deterministic ProjectTitlePolicy (contract epic, slice 5).

The review found 13/20 titles over 72 chars, many just echoing the stage title with a
skill appended. Fix: a deterministic policy that (a) validates a title against explicit
rules and (b) produces a compliant fallback from the project's own skills — never the
stage title. The LLM may still formulate a nicer title; the policy only guarantees
compliance and blocks publish (not draft) on violations.

Rules: 3-8 meaningful words, <= 72 chars, never equal to / derived from the stage title,
no "часть N" / long enumerations, no mid-word truncation. Applied as a post-grouping pass
that regenerates only *violating* titles, preserving good template/capstone titles.

Pure leaf: depends only on the domain dataclasses + stdlib.
"""

from __future__ import annotations

import re

from .domain import CurriculumBlock, ProjectBlueprint

TITLE_MAX_CHARS = 72
TITLE_MAX_WORDS = 8
TITLE_MIN_WORDS = 3

_PART_N_RE = re.compile(r"\bчаст[ьи]\s+\d+", re.IGNORECASE)
_PROJECT_PREFIX_RE = re.compile(r"^\s*(практический\s+проект|проект)\s*:\s*", re.IGNORECASE)
_STAGE_PREFIX_RE = re.compile(r"^\s*(блок|этап|модуль)\s+\d+\s*[.:]\s*", re.IGNORECASE)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").casefold().replace("ё", "е")).strip(" .,-:;")


def _stage_theme(stage_title: str) -> str:
    return _norm(_STAGE_PREFIX_RE.sub("", str(stage_title or "")))


def title_violations(title: str, *, stage_title: str = "") -> tuple[str, ...]:
    """Explicit title-policy violations (empty tuple == compliant)."""
    text = str(title or "").strip()
    reasons: list[str] = []
    words = text.split()
    if len(text) > TITLE_MAX_CHARS:
        reasons.append("too_long")
    if len(words) > TITLE_MAX_WORDS:
        reasons.append("too_many_words")
    if len(words) < TITLE_MIN_WORDS:
        reasons.append("too_few_words")
    if _PART_N_RE.search(text):
        reasons.append("part_enumeration")
    if text.endswith(("…", "...")):
        reasons.append("mid_word_truncation")
    theme = _stage_theme(stage_title)
    if theme and _norm(text) == theme:
        reasons.append("echoes_stage")
    return tuple(reasons)


def _compact(text: str, *, max_words: int, max_chars: int) -> str:
    cleaned = _PROJECT_PREFIX_RE.sub("", str(text or ""))
    cleaned = re.sub(r"\s*\([^)]*[A-Za-z][^)]*\)", "", cleaned)  # drop latin glossary notes
    cleaned = re.sub(r"\s+", " ", cleaned.replace("—", "-")).strip(" .,-:;")
    words = cleaned.split()
    if len(words) > max_words:
        cleaned = " ".join(words[:max_words])
    if len(cleaned) > max_chars:
        clipped = cleaned[:max_chars].rstrip()
        boundary = clipped.rfind(" ")
        if boundary >= max_chars // 2:
            clipped = clipped[:boundary]
        cleaned = clipped.rstrip(" .,-:;")
    return cleaned.strip(" .,-:;")


def build_project_title(project: ProjectBlueprint, *, stage_title: str = "") -> str:
    """Deterministic compliant title from the project's own skills (never the stage title)."""
    primary = project.primary_occurrences
    anchor_nodes = [occurrence.node for occurrence in primary] or project.unique_nodes
    if not anchor_nodes:
        return "Проект"
    anchor = _compact(anchor_nodes[0].name, max_words=TITLE_MAX_WORDS, max_chars=TITLE_MAX_CHARS)
    # Never echo the stage theme; if the compacted anchor collapses onto it, add a second skill.
    if _stage_theme(stage_title) and _norm(anchor) == _stage_theme(stage_title) and len(anchor_nodes) > 1:
        extra = _compact(anchor_nodes[1].name, max_words=4, max_chars=32)
        anchor = _compact(f"{anchor}: {extra}", max_words=TITLE_MAX_WORDS, max_chars=TITLE_MAX_CHARS)
    return anchor or "Проект"


def apply_title_policy(blocks: list[CurriculumBlock]) -> None:
    """Regenerate only violating titles in place; keep compliant template/capstone titles."""
    for block in blocks:
        for project in block.projects:
            if title_violations(project.title, stage_title=block.title):
                project.title = build_project_title(project, stage_title=block.title)
