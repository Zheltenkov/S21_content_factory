"""Pure text / markdown utilities for methodology checkpoints.

Stdlib-only (``re``) helpers extracted from ``checkpoint``: markdown section/outline/
subsection slicing, slug + unique-id generation, truncation, label detection, practice
task-block parsing, final-section extraction, and byte sizing. Dependency-free leaf
(no models, no policy) so the summary/matrix builders and the checkpoint policy can
share them without a cycle. All are internal helpers (no external consumer); ``checkpoint``
re-imports whichever it still calls.
"""

from __future__ import annotations

import re
from typing import Any


def _truncate_text(text: str, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return f"{value[:limit].rstrip()}..."


def _slug_text(value: str) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[^a-zа-яё0-9]+", "_", text, flags=re.I)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:80] or "section"


def _unique_section_id(base: str, used_ids: set[str]) -> str:
    candidate = base
    suffix = 2
    while candidate in used_ids:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used_ids.add(candidate)
    return candidate


def _markdown_section(markdown: str, heading_marker: str, limit: int = 1800) -> str:
    if not markdown:
        return ""
    marker = heading_marker.lower()
    lines = markdown.splitlines()
    start_index = None
    start_level = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        title = stripped.lstrip("#").strip().lower()
        if marker in title:
            start_index = index
            start_level = len(stripped) - len(stripped.lstrip("#"))
            break
    if start_index is None:
        return _truncate_text(markdown, limit)
    end_index = len(lines)
    for index in range(start_index + 1, len(lines)):
        stripped = lines[index].strip()
        if not stripped.startswith("#"):
            continue
        level = len(stripped) - len(stripped.lstrip("#"))
        if level <= start_level:
            end_index = index
            break
    return "\n".join(lines[start_index:end_index]).strip()


def _markdown_outline(markdown: str) -> list[dict[str, Any]]:
    outline: list[dict[str, Any]] = []
    for match in re.finditer(r"^(#{1,6})\s+(.+?)\s*$", str(markdown or ""), flags=re.MULTILINE):
        outline.append(
            {
                "level": len(match.group(1)),
                "title": match.group(2).strip(),
            }
        )
    return outline


def _markdown_subsections(section_markdown: str, *, min_level: int = 3) -> list[dict[str, str]]:
    """Split a chapter into addressable markdown subsections for review UI."""
    if not section_markdown:
        return []

    matches = [
        match
        for match in re.finditer(r"^(#{1,6})\s+(.+?)\s*$", section_markdown, flags=re.MULTILINE)
        if len(match.group(1)) >= min_level
    ]
    sections: list[dict[str, str]] = []
    used_ids: set[str] = set()
    for index, match in enumerate(matches):
        level = len(match.group(1))
        title = match.group(2).strip()
        end = len(section_markdown)
        for next_match in matches[index + 1 :]:
            if len(next_match.group(1)) <= level:
                end = next_match.start()
                break
        section_id = _unique_section_id(_slug_text(title), used_ids)
        sections.append(
            {
                "id": section_id,
                "title": title,
                "markdown": section_markdown[match.start() : end].strip(),
            }
        )
    return sections


def _has_markdown_label(text: str, label: str) -> bool:
    return bool(re.search(rf"\*\*{re.escape(label)}:?\*\*", text, flags=re.I))


def _practice_task_blocks(chapter_3: str) -> list[str]:
    raw = re.split(r"(?=^###\s+(?:Задание|Задача)\s+\d+\.)", chapter_3, flags=re.M)
    return [chunk.strip() for chunk in raw if chunk.strip().startswith("###")]


def _task_has_p2p_outcomes(task_block: str) -> bool:
    result_match = re.search(
        r"\*\*(?:Что должно получиться|Критерии проверки.*?):?\*\*\s*(.+?)(?=\n\*\*|\n###|\Z)",
        task_block,
        flags=re.S | re.I,
    )
    if not result_match:
        return False
    checklist = [
        line
        for line in result_match.group(1).splitlines()
        if re.match(r"^\s*[-*]\s*(?:\[[ xX]?\]\s*)?.{10,}", line)
    ]
    has_location = bool(re.search(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./{}:\-]+\.[A-Za-z0-9]+", task_block))
    has_artifact = bool(re.search(r"\b(файл|документ|таблиц|схем|артефакт|отчет|отчёт|README|Markdown)\b", task_block, re.I))
    return len(checklist) >= 2 and (has_location or has_artifact)


def _final_section(markdown: str) -> str:
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", markdown, flags=re.M))
    for index, match in enumerate(matches):
        title = match.group(1).strip().lower()
        if not re.search(r"(заключение|итог проекта|финал проекта|завершение проекта)", title, flags=re.I):
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        return markdown[match.start():end].strip()
    return ""


def _annotation_text(annotation: Any) -> str:
    if isinstance(annotation, dict):
        return str(annotation.get("text") or "")
    if hasattr(annotation, "text"):
        return str(annotation.text or "")
    return ""


def _data_size(data: Any) -> int:
    if data is None:
        return 0
    if isinstance(data, bytes):
        return len(data)
    return len(str(data).encode("utf-8"))
