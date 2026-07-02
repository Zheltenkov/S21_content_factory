"""Scope contracts for README regeneration.

The UI sends human-readable instructions, but backend regeneration must still
enforce where edits are allowed. This module turns those instructions into a
small deterministic contract that can be reused by prompting, patch validation
and service-level guards.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


RegenerationChangeIntent = Literal["local_section_edit", "structural_document_edit"]


@dataclass(frozen=True)
class RegenerationEditScope:
    """A Markdown line range that is allowed to change during regeneration."""

    title: str
    start_line: int
    end_line: int
    change: str = ""
    keep: str = ""
    raw_block: str = ""
    source: str = "explicit"
    is_history: bool = False

    def as_line_range(self) -> tuple[int, int, str]:
        return (self.start_line, self.end_line, self.title)


_SCOPE_HEADER_RE = re.compile(
    r"(?m)^(?P<prefix>Сохран[её]нная правка|Правка)\s+\d+:\s*(?P<title>.+?)\s*$"
)
_LINE_RANGE_RE = re.compile(r"Диапазон строк:\s*(?P<start>\d+)\s*[-–—]\s*(?P<end>\d+)", re.IGNORECASE)
_STRUCTURAL_CHAPTER_ACTION_RE = re.compile(
    r"(?:добав(?:ь|ить)|созда(?:й|ть)|встав(?:ь|ить)|удал(?:и|ить)|убер(?:и|ать)|"
    r"переимену(?:й|йте|овать)|перенес(?:и|ти)|добавление|удаление)"
    r".{0,48}(?:глав|chapter)",
    re.IGNORECASE | re.DOTALL,
)
_STRUCTURAL_NEW_SECTION_RE = re.compile(
    r"(?:нов(?:ую|ая|ый|ое|ые|ого)\s+(?:глав|раздел|подраздел|секц)|"
    r"(?:добав(?:ь|ить)|созда(?:й|ть)|встав(?:ь|ить)).{0,36}нов(?:ую|ый|ое|ые)\s+"
    r"(?:раздел|подраздел|секц))",
    re.IGNORECASE | re.DOTALL,
)
_STRUCTURAL_TOC_ACTION_RE = re.compile(
    r"(?:обнов(?:и|ить)|пересобр(?:ать|и)|исправ(?:ь|ить)|синхронизиру(?:й|йте|овать)|"
    r"добав(?:ь|ить)|удал(?:и|ить)).{0,48}(?:содержание|оглавление|toc|table of contents|нумерац)",
    re.IGNORECASE | re.DOTALL,
)
_STRUCTURAL_WORD_RE = re.compile(
    r"(?:структурн(?:ая|ую|ые|ое)|структур(?:а|ы)|нумерац(?:ия|ию)|оглавление|содержание)",
    re.IGNORECASE,
)


def _markdown_line_count(markdown: str) -> int:
    return max(1, len((markdown or "").splitlines()) or 1)


def _clamp_line_range(start_line: int, end_line: int, markdown: str) -> tuple[int, int]:
    line_count = _markdown_line_count(markdown)
    start = max(1, min(start_line, line_count))
    end = max(start, min(end_line, line_count))
    return start, end


def _extract_field(block: str, label: str) -> str:
    pattern = re.compile(
        rf"{re.escape(label)}:\s*(.*?)(?=\n(?:Что исправить|Что оставить|Диапазон строк):|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(block or "")
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def _looks_like_title_scope(scope: RegenerationEditScope, markdown: str) -> bool:
    text = f"{scope.title} {scope.change}".lower()
    if not ("назван" in text and "проект" in text):
        return False
    lines = (markdown or "").splitlines()
    if scope.start_line < 1 or scope.start_line > len(lines):
        return False
    return lines[scope.start_line - 1].lstrip().startswith("# ")


def _normalize_scope(scope: RegenerationEditScope, markdown: str) -> RegenerationEditScope:
    start, end = _clamp_line_range(scope.start_line, scope.end_line, markdown)
    normalized = RegenerationEditScope(
        title=scope.title.strip() or "Часть README",
        start_line=start,
        end_line=end,
        change=scope.change.strip(),
        keep=scope.keep.strip(),
        raw_block=scope.raw_block.strip(),
        source=scope.source,
        is_history=scope.is_history,
    )
    if _looks_like_title_scope(normalized, markdown):
        return RegenerationEditScope(
            title=normalized.title,
            start_line=normalized.start_line,
            end_line=normalized.start_line,
            change=normalized.change,
            keep=normalized.keep,
            raw_block=normalized.raw_block,
            source=normalized.source,
            is_history=normalized.is_history,
        )
    return normalized


def _dedupe_scopes(scopes: list[RegenerationEditScope]) -> list[RegenerationEditScope]:
    seen: set[tuple[int, int, str, bool]] = set()
    result: list[RegenerationEditScope] = []
    for scope in sorted(scopes, key=lambda item: (item.start_line, item.end_line, item.title)):
        key = (scope.start_line, scope.end_line, scope.title.lower(), scope.is_history)
        if key in seen:
            continue
        seen.add(key)
        result.append(scope)
    return result


def detect_regeneration_change_intent(
    comments: str,
    markdown: str,
    scopes: list[RegenerationEditScope] | None = None,
) -> RegenerationChangeIntent:
    """
    Classify a regeneration request before prompting.

    Local edits are constrained to selected line ranges. Structural edits are a
    separate workflow because adding/removing/renaming chapters requires
    deterministic derived changes in the table of contents and outline.
    """
    text = re.sub(r"\s+", " ", comments or "").strip().casefold()
    if not text:
        return "local_section_edit"

    if (
        _STRUCTURAL_CHAPTER_ACTION_RE.search(text)
        or _STRUCTURAL_NEW_SECTION_RE.search(text)
        or _STRUCTURAL_TOC_ACTION_RE.search(text)
    ):
        return "structural_document_edit"

    if _STRUCTURAL_WORD_RE.search(text) and re.search(
        r"(?:перестро|измен(?:и|ить)|поменя(?:й|ть)|добав(?:ь|ить)|удал(?:и|ить)|обнов(?:и|ить))",
        text,
        re.IGNORECASE,
    ):
        return "structural_document_edit"

    for scope in scopes or []:
        scope_text = f"{scope.title} {scope.change}".casefold()
        if (
            _STRUCTURAL_CHAPTER_ACTION_RE.search(scope_text)
            or _STRUCTURAL_NEW_SECTION_RE.search(scope_text)
            or _STRUCTURAL_TOC_ACTION_RE.search(scope_text)
        ):
            return "structural_document_edit"

    return "local_section_edit"


def _parse_explicit_scopes(comments: str, markdown: str, *, include_history: bool) -> list[RegenerationEditScope]:
    matches = list(_SCOPE_HEADER_RE.finditer(comments or ""))
    scopes: list[RegenerationEditScope] = []

    for index, match in enumerate(matches):
        is_history = match.group("prefix").lower().startswith("сохран")
        if is_history and not include_history:
            continue
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(comments or "")
        block = (comments or "")[match.end() : next_start]
        range_match = _LINE_RANGE_RE.search(block)
        if not range_match:
            continue
        raw_block = f"{match.group(0)}{block}".strip()
        scope = RegenerationEditScope(
            title=match.group("title"),
            start_line=int(range_match.group("start")),
            end_line=int(range_match.group("end")),
            change=_extract_field(block, "Что исправить"),
            keep=_extract_field(block, "Что оставить"),
            raw_block=raw_block,
            source="explicit",
            is_history=is_history,
        )
        scopes.append(_normalize_scope(scope, markdown))

    return _dedupe_scopes(scopes)


def _heading_ranges(markdown: str) -> list[tuple[int, int, int, str]]:
    lines = (markdown or "").splitlines()
    headings: list[tuple[int, int, str]] = []
    for line_index, line in enumerate(lines, start=1):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            headings.append((line_index, len(match.group(1)), match.group(2).strip()))

    ranges: list[tuple[int, int, int, str]] = []
    for index, (start_line, level, title) in enumerate(headings):
        end_line = len(lines)
        for next_start, next_level, _next_title in headings[index + 1 :]:
            if next_level <= level:
                end_line = next_start - 1
                break
        ranges.append((start_line, end_line, level, title))
    return ranges


def _infer_scopes_from_comments(comments: str, markdown: str) -> list[RegenerationEditScope]:
    text = (comments or "").lower()
    scopes: list[RegenerationEditScope] = []

    if "назван" in text and "проект" in text:
        for start_line, _end_line, level, title in _heading_ranges(markdown):
            if level == 1:
                scopes.append(
                    RegenerationEditScope(
                        title="Название проекта",
                        start_line=start_line,
                        end_line=start_line,
                        change=comments.strip(),
                        raw_block=comments.strip(),
                        source="inferred",
                    )
                )
                break

    section_refs = set(re.findall(r"(?<!\d)(\d+(?:\.\d+)+)(?!\d)", text))
    if section_refs:
        for start_line, end_line, _level, title in _heading_ranges(markdown):
            normalized_title = title.lower()
            for ref in section_refs:
                if normalized_title.startswith(ref) or f" {ref}" in normalized_title:
                    scopes.append(
                        RegenerationEditScope(
                            title=title,
                            start_line=start_line,
                            end_line=end_line,
                            change=comments.strip(),
                            raw_block=comments.strip(),
                            source="inferred",
                        )
                    )
                    break

    return _dedupe_scopes([_normalize_scope(scope, markdown) for scope in scopes])


def parse_regeneration_edit_scopes(
    comments: str,
    markdown: str,
    *,
    include_history: bool = False,
) -> list[RegenerationEditScope]:
    """
    Parse active regeneration scopes from UI comments.

    Current edits are introduced as ``Правка N``. Previous applied edits are
    introduced as ``Сохранённая правка N`` and are intentionally excluded from
    the active edit set by default: they are already present in the input README
    and should be preserved, not reopened for arbitrary rewriting.
    """
    explicit_matches = list(_SCOPE_HEADER_RE.finditer(comments or ""))
    if explicit_matches:
        return _parse_explicit_scopes(comments, markdown, include_history=include_history)
    return _infer_scopes_from_comments(comments, markdown)


def scope_line_ranges(scopes: list[RegenerationEditScope]) -> list[tuple[int, int, str]]:
    return [scope.as_line_range() for scope in scopes]


def slice_markdown_by_scope(markdown: str, scope: RegenerationEditScope) -> str:
    lines = (markdown or "").splitlines()
    if not lines:
        return ""
    start, end = _clamp_line_range(scope.start_line, scope.end_line, markdown)
    return "\n".join(lines[start - 1 : end])


def replace_markdown_scope(markdown: str, scope: RegenerationEditScope, replacement: str) -> str:
    lines = (markdown or "").splitlines()
    if not lines:
        return replacement.strip("\n")
    start, end = _clamp_line_range(scope.start_line, scope.end_line, markdown)
    replacement_lines = (replacement or "").strip("\n").splitlines()
    next_lines = lines[: start - 1] + replacement_lines + lines[end:]
    suffix = "\n" if (markdown or "").endswith("\n") else ""
    return "\n".join(next_lines) + suffix


def render_scope_contract(scopes: list[RegenerationEditScope], markdown: str) -> str:
    """Render a compact prompt contract for allowed edit scopes."""
    if not scopes:
        return ""

    lines = [
        "РАЗРЕШЁННЫЕ ОБЛАСТИ ПРАВОК:",
        "Менять можно только текст внутри перечисленных диапазонов строк. Все остальные строки README должны остаться байт-в-байт без изменений.",
    ]
    for index, scope in enumerate(scopes, start=1):
        excerpt = re.sub(r"\s+", " ", slice_markdown_by_scope(markdown, scope)).strip()
        if len(excerpt) > 360:
            excerpt = f"{excerpt[:180]} … {excerpt[-140:]}"
        lines.append(
            f"{index}. {scope.title} — строки {scope.start_line}-{scope.end_line}. "
            f"Правка: {scope.change or 'см. комментарий'}. "
            f"Сохранить: {scope.keep or 'структуру, заголовок и смысл соседнего текста'}. "
            f"Фрагмент: {excerpt}"
        )
    lines.extend(
        [
            "Патч, который затрагивает текст вне этих диапазонов, считается ошибкой.",
            "Если точную правку нельзя выразить внутри этих диапазонов, верни пустой список changes вместо догадок.",
            "Сохранённые правки предыдущих перегенераций не являются разрешением переписывать их заново.",
        ]
    )
    return "\n".join(lines)


def render_structural_change_contract(scopes: list[RegenerationEditScope], markdown: str) -> str:
    """Render prompt guardrails for document-level structural regeneration."""
    headings = [
        (start, level, title)
        for start, _end, level, title in _heading_ranges(markdown)
        if level in {1, 2, 3}
    ]
    heading_lines = [
        f"- L{level} строка {start}: {title}"
        for start, level, title in headings[:80]
    ]

    lines = [
        "РЕЖИМ СТРУКТУРНОЙ ПРАВКИ:",
        "Запрос меняет структуру README, поэтому разрешены только явно производные изменения: новая/удалённая/переименованная глава, оглавление, якоря и нумерация.",
        "Обязательные главы нельзя удалять, переименовывать или перенумеровывать без прямого явного запроса:",
        "- ## Глава 1. Введение и инструкция",
        "- ## Глава 2. Теоретический блок",
        "- ## Глава 3. Практический блок",
        "Если пользователь просит добавить новую главу, добавь её как следующую главу после практического блока или перед заключением. Не сдвигай номера глав 1-3.",
        "Тела существующих глав и подразделов должны остаться без изменений, если они не являются явно выбранной целью правки.",
        "Оглавление будет дополнительно пересобрано детерминированно по фактическим заголовкам после перегенерации.",
    ]
    if scopes:
        lines.append("Явно выбранные части задают смысловой фокус, но не запрещают производные изменения оглавления и outline:")
        for index, scope in enumerate(scopes, start=1):
            lines.append(
                f"{index}. {scope.title} — строки {scope.start_line}-{scope.end_line}. "
                f"Правка: {scope.change or 'см. комментарий'}."
            )
    if heading_lines:
        lines.append("Текущий outline README:")
        lines.extend(heading_lines)
    return "\n".join(lines)
