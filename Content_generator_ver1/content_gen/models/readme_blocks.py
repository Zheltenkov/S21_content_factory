"""Typed README content blocks extracted from section bodies."""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ReadmeBlockKind(str, Enum):
    """Supported structured block kinds inside README sections."""

    PARAGRAPH = "paragraph"
    MERMAID = "mermaid"
    TABLE = "table"
    FORMULA = "formula"
    CODE = "code"
    CRITERIA = "criteria"


class ReadmeBlock(BaseModel):
    """Structured block extracted from Markdown without changing the source."""

    model_config = ConfigDict(extra="forbid")

    kind: ReadmeBlockKind
    source: str
    content: str = ""
    caption: str = ""
    language: str = ""
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    items: list[str] = Field(default_factory=list)
    display: bool = True
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    section_title: str = ""
    section_path: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_markdown(self) -> str:
        """Return the renderable Markdown source for the block."""
        return self.source

    def with_section(self, title: str, path: list[str]) -> "ReadmeBlock":
        """Attach section location metadata to the extracted block."""
        return self.model_copy(
            update={
                "section_title": title,
                "section_path": list(path),
            }
        )


class MarkdownParagraph(ReadmeBlock):
    """Plain Markdown paragraph/list block."""

    kind: ReadmeBlockKind = ReadmeBlockKind.PARAGRAPH


class MermaidBlock(ReadmeBlock):
    """Mermaid diagram block with optional nearby caption."""

    kind: ReadmeBlockKind = ReadmeBlockKind.MERMAID
    language: str = "mermaid"


class TableBlock(ReadmeBlock):
    """Markdown table block with parsed headers and rows."""

    kind: ReadmeBlockKind = ReadmeBlockKind.TABLE


class FormulaBlock(ReadmeBlock):
    """Display formula block."""

    kind: ReadmeBlockKind = ReadmeBlockKind.FORMULA


class CodeBlock(ReadmeBlock):
    """Generic fenced code block."""

    kind: ReadmeBlockKind = ReadmeBlockKind.CODE


class CriteriaBlock(ReadmeBlock):
    """Checklist or criteria list block."""

    kind: ReadmeBlockKind = ReadmeBlockKind.CRITERIA


_CODE_RE = re.compile(
    r"(?P<source>(?P<fence>`{3,}|~{3,})(?P<language>[^\n]*)\n(?P<content>[\s\S]*?)\n(?P=fence))",
    flags=re.IGNORECASE,
)
_FORMULA_RE = re.compile(r"(?P<source>\$\$\s*\n?(?P<content>[\s\S]*?)\n?\$\$)")
_TABLE_SEPARATOR_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)
_TABLE_ROW_RE = re.compile(r"^\s*\|.+\|\s*$")
_FENCE_RE = re.compile(r"^\s*(```+|~~~+)")
_PARAGRAPH_RE = re.compile(r"\S[\s\S]*?(?=\n{2,}|\Z)")
_CHECKBOX_RE = re.compile(r"^\s*[-*]\s+\[[ xX]\]\s*(?P<item>.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(?P<item>.+?)\s*$")
_CAPTION_RE = re.compile(
    r"^\s*(?:_(?P<italic>[^_\n]{3,160})_|\*(?P<star>[^*\n]{3,160})\*|(?P<label>(?:Рисунок|Диаграмма|Таблица|Formula|Figure|Table)\s*[:.]\s*[^\n]{3,160}))\s*$",
    flags=re.IGNORECASE,
)


def materialize_readme_blocks(markdown: str) -> list[ReadmeBlock]:
    """Split Markdown into ordered typed blocks while preserving renderable source."""
    text = (markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    occupied: list[tuple[int, int]] = []
    non_text_blocks: list[ReadmeBlock] = []

    for match in _CODE_RE.finditer(text):
        language = (match.group("language") or "").strip()
        block_cls = MermaidBlock if language.casefold().startswith("mermaid") else CodeBlock
        block = block_cls(
            source=match.group("source"),
            content=match.group("content").strip(),
            caption=_caption_near(text, match.start(), match.end()),
            language=language,
            start=match.start(),
            end=match.end(),
        )
        non_text_blocks.append(block)
        occupied.append((match.start(), match.end()))

    for match in _FORMULA_RE.finditer(text):
        if _overlaps(match.start(), match.end(), occupied):
            continue
        block = FormulaBlock(
            source=match.group("source"),
            content=match.group("content").strip(),
            caption=_caption_near(text, match.start(), match.end()),
            start=match.start(),
            end=match.end(),
        )
        non_text_blocks.append(block)
        occupied.append((match.start(), match.end()))

    for start, end in _table_spans(text):
        if _overlaps(start, end, occupied):
            continue
        source = text[start:end].strip("\n")
        headers, rows = _parse_table_source(source)
        block = TableBlock(
            source=source,
            content=source,
            caption=_caption_near(text, start, end),
            headers=headers,
            rows=rows,
            start=start,
            end=end,
            metadata={"rows": max(0, len(source.splitlines()) - 2)},
        )
        non_text_blocks.append(block)
        occupied.append((start, end))

    blocks: list[ReadmeBlock] = []
    cursor = 0
    for block in sorted(non_text_blocks, key=lambda item: item.start):
        if cursor < block.start:
            blocks.extend(_text_blocks(text[cursor:block.start], offset=cursor))
        blocks.append(block)
        cursor = block.end
    if cursor < len(text):
        blocks.extend(_text_blocks(text[cursor:], offset=cursor))

    return [block for block in blocks if block.source.strip()]


def render_readme_blocks(blocks: list[ReadmeBlock]) -> str:
    """Render typed blocks to Markdown with stable blank lines between block boundaries."""
    return "\n\n".join(block.to_markdown().strip() for block in blocks if block.to_markdown().strip()).strip()


def extract_readme_blocks(markdown: str, *, include_paragraphs: bool = False) -> list[ReadmeBlock]:
    """Extract typed README blocks from Markdown.

    By default this returns non-paragraph blocks so existing validators keep
    focusing on visual/checkable artifacts. Pass ``include_paragraphs=True`` to
    materialize the full section body.
    """
    blocks = materialize_readme_blocks(markdown)
    if include_paragraphs:
        return blocks
    return [block for block in blocks if block.kind is not ReadmeBlockKind.PARAGRAPH]


def block_counts(blocks: list[ReadmeBlock], *, include_empty: bool = False) -> dict[str, int]:
    """Return stable block counts keyed by block kind value."""
    counts = {kind.value: 0 for kind in ReadmeBlockKind} if include_empty else {}
    for block in blocks:
        counts[block.kind.value] = counts.get(block.kind.value, 0) + 1
    return counts


def _overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < span_end and end > span_start for span_start, span_end in spans)


def _table_spans(text: str) -> list[tuple[int, int]]:
    """Find Markdown table spans outside fenced code blocks."""
    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    position = 0
    for line in lines:
        offsets.append(position)
        position += len(line)

    spans: list[tuple[int, int]] = []
    in_fence: str | None = None
    index = 0
    while index < len(lines):
        stripped_line = lines[index].rstrip("\n")
        fence_match = _FENCE_RE.match(stripped_line)
        if fence_match:
            fence_marker = fence_match.group(1)[:3]
            in_fence = None if in_fence == fence_marker else fence_marker if in_fence is None else in_fence
            index += 1
            continue

        if (
            in_fence is None
            and index + 1 < len(lines)
            and _TABLE_ROW_RE.match(stripped_line)
            and _TABLE_SEPARATOR_RE.match(lines[index + 1].rstrip("\n"))
        ):
            start = offsets[index]
            end_index = index + 2
            while end_index < len(lines) and _TABLE_ROW_RE.match(lines[end_index].rstrip("\n")):
                end_index += 1
            end = offsets[end_index] if end_index < len(offsets) else len(text)
            spans.append((start, end))
            index = end_index
            continue

        index += 1
    return spans


def _text_blocks(fragment: str, *, offset: int) -> list[ReadmeBlock]:
    """Materialize prose/list fragments into paragraph or criteria blocks."""
    blocks: list[ReadmeBlock] = []
    for match in _PARAGRAPH_RE.finditer(fragment):
        source = match.group(0).strip("\n")
        if not source.strip():
            continue
        start = offset + match.start()
        end = offset + match.end()
        items = _criteria_items(source)
        if items:
            blocks.append(
                CriteriaBlock(
                    source=source,
                    content=source,
                    items=items,
                    start=start,
                    end=end,
                    metadata={"items": len(items)},
                )
            )
        else:
            blocks.append(
                MarkdownParagraph(
                    source=source,
                    content=source.strip(),
                    start=start,
                    end=end,
                )
            )
    return blocks


def _criteria_items(source: str) -> list[str]:
    """Extract criteria/checklist items from one textual block."""
    lines = [line.rstrip() for line in source.splitlines() if line.strip()]
    if not lines:
        return []

    checkbox_items = [
        match.group("item").strip()
        for line in lines
        if (match := _CHECKBOX_RE.match(line))
    ]
    if checkbox_items:
        return checkbox_items

    first_line = lines[0].casefold()
    if "критери" not in first_line and "criteria" not in first_line and "чек-лист" not in first_line:
        return []

    return [
        match.group("item").strip()
        for line in lines[1:]
        if (match := _BULLET_RE.match(line))
    ]


def _parse_table_source(source: str) -> tuple[list[str], list[list[str]]]:
    """Parse a Markdown table into headers and row cells."""
    lines = [line.strip() for line in source.splitlines() if line.strip()]
    if len(lines) < 2:
        return [], []
    headers = _split_table_row(lines[0])
    rows = [_split_table_row(line) for line in lines[2:]]
    return headers, rows


def _split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _caption_near(text: str, start: int, end: int) -> str:
    """Return a short caption immediately before or after a block."""
    after = _first_non_empty_line(text[end:])
    if after and (match := _CAPTION_RE.match(after)):
        return _caption_match_text(match)

    before = _last_non_empty_line(text[:start])
    if before and (match := _CAPTION_RE.match(before)):
        return _caption_match_text(match)
    return ""


def _first_non_empty_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _last_non_empty_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return ""


def _caption_match_text(match: re.Match[str]) -> str:
    return next((group.strip() for group in match.groups() if group), "")
