"""Canonical user-facing validation message fragments."""

from __future__ import annotations


def theory_section_label(index: int, title: str = "", *, limit: int = 50) -> str:
    """Return the public label for a Chapter 2 section."""
    return _label(f"Раздел 2.{index}", title, limit=limit)


def practice_task_label(index: int, title: str = "", *, limit: int = 50) -> str:
    """Return the public label for a Chapter 3 task."""
    return _label(f"Задание {index}", title, limit=limit)


def _label(prefix: str, title: str, *, limit: int) -> str:
    clean_title = str(title or "").strip()
    if not clean_title:
        return prefix
    return f"{prefix} '{clean_title[:limit]}'"
