"""Typed README helpers for rubric checkers."""

from __future__ import annotations

import re

from ...models.readme_blocks import ReadmeBlock, ReadmeBlockKind
from ...models.readme_document import ReadmeDocument, ReadmeSection
from .utils import TaskBlock


def _is_canonical_theory_part(section: ReadmeSection) -> bool:
    return section.level == 3 and section.title.strip().casefold().startswith("2.")


def _is_canonical_practice_task(section: ReadmeSection) -> bool:
    return section.level == 3 and section.title.strip().casefold().startswith("задание ")


def section_content(section: ReadmeSection | None) -> str:
    """Render a section body and children without the section's own heading."""
    if section is None:
        return ""
    blocks: list[str] = []
    body = section.body_markdown().strip()
    if body:
        blocks.append(body)
    blocks.extend(child.to_markdown().strip() for child in section.children if child.to_markdown().strip())
    return "\n\n".join(blocks).strip()


def chapter_content(document: ReadmeDocument, chapter_number: int, *, language: str = "ru") -> str:
    """Return one chapter content without its H2 heading."""
    return section_content(document.chapter_section(chapter_number, language=language))


def chapter_sections(document: ReadmeDocument, chapter_number: int, *, language: str = "ru") -> list[ReadmeSection]:
    """Return direct child sections for one typed chapter."""
    chapter = document.chapter_section(chapter_number, language=language)
    return list(chapter.children) if chapter else []


def theory_part_sections(document: ReadmeDocument, *, language: str = "ru") -> list[ReadmeSection]:
    """Return typed theory subsections from Chapter 2."""
    return [
        section
        for section in chapter_sections(document, 2, language=language)
        if _is_canonical_theory_part(section)
    ]


def practice_task_sections(document: ReadmeDocument, *, language: str = "ru") -> list[ReadmeSection]:
    """Return typed practice task sections from Chapter 3."""
    return [
        section
        for section in chapter_sections(document, 3, language=language)
        if _is_canonical_practice_task(section)
    ]


def section_blocks(section: ReadmeSection | None) -> list[ReadmeBlock]:
    """Return typed content blocks for one section."""
    return section.content_blocks() if section else []


def section_text_blocks(section: ReadmeSection | None, *, recursive: bool = True) -> list[ReadmeBlock]:
    """Return prose/checklist blocks that are safe for text analysis."""
    if section is None:
        return []
    return [
        block
        for block in section.content_blocks(recursive=recursive, include_paragraphs=True)
        if block.kind in {ReadmeBlockKind.PARAGRAPH, ReadmeBlockKind.CRITERIA}
    ]


def section_prose_markdown(section: ReadmeSection | None, *, recursive: bool = True) -> str:
    """Render only textual blocks from a section, excluding code, Mermaid, tables and formulas."""
    return "\n\n".join(block.to_markdown().strip() for block in section_text_blocks(section, recursive=recursive)).strip()


def section_prose_text(section: ReadmeSection | None, *, recursive: bool = True) -> str:
    """Return plain typed prose text from a section without re-rendering Markdown blocks."""
    text_blocks: list[str] = []
    for block in section_text_blocks(section, recursive=recursive):
        text = (block.content or block.source or "").strip()
        if text:
            text_blocks.append(text)
    return "\n\n".join(text_blocks).strip()


def section_label_block(section: ReadmeSection | None, label: str) -> str:
    """Extract a bold label block from typed prose without rendering Markdown."""
    text = section_prose_text(section, recursive=False)
    if not text:
        return ""
    match = re.search(
        rf"(?:^|\n)\s*\*\*{re.escape(label)}:?\*\*\s*(.+?)(?=\n\s*\*\*[^*\n]{{2,120}}:?\*\*|\Z)",
        text,
        flags=re.S | re.I,
    )
    return match.group(1).strip() if match else ""


def section_has_label(section: ReadmeSection | None, label: str) -> bool:
    """Return whether typed prose contains a bold label without block rendering."""
    return bool(section_label_block(section, label))


def section_content_size(section: ReadmeSection | None, *, recursive: bool = True) -> int:
    """Return typed source length for a section without rendering headings."""
    if section is None:
        return 0
    return sum(
        len((block.source or block.content or "").strip())
        for block in section.content_blocks(recursive=recursive, include_paragraphs=True)
    )


def chapter_prose_text(document: ReadmeDocument, chapter_number: int, *, language: str = "ru") -> str:
    """Return plain typed prose text for one chapter without section Markdown rendering."""
    return section_prose_text(document.chapter_section(chapter_number, language=language))


def document_prose_markdown(document: ReadmeDocument) -> str:
    """Render only text-oriented README blocks for style/narrative analysis."""
    blocks: list[str] = []
    if document.annotation.strip():
        blocks.append(document.annotation.strip())
    for section in document.sections:
        text = section_prose_markdown(section)
        if text:
            blocks.append(text)
    return "\n\n".join(blocks).strip()


def document_prose_text(document: ReadmeDocument) -> str:
    """Return plain text-oriented README blocks for typed style/narrative analysis."""
    blocks: list[str] = []
    if document.annotation.strip():
        blocks.append(document.annotation.strip())
    for section in document.sections:
        text = section_prose_text(section)
        if text:
            blocks.append(text)
    return "\n\n".join(blocks).strip()


def document_paragraphs(document: ReadmeDocument, *, min_length: int) -> list[str]:
    """Return paragraph-like typed blocks without reparsing rendered Markdown."""
    paragraphs: list[str] = []
    if len(document.annotation.strip()) >= min_length:
        paragraphs.append(document.annotation.strip())
    for section in document.sections:
        for block in section_text_blocks(section):
            text = (block.content or block.source or "").strip()
            if len(text) >= min_length:
                paragraphs.append(text)
    return paragraphs


def section_artifact_paths(section: ReadmeSection | None) -> list[str]:
    """Return artifact paths inferred by the typed README section model."""
    if section is None:
        return []
    raw_paths = section.metadata.get("artifact_paths") or []
    return [str(path) for path in raw_paths if str(path).strip()]


def section_criteria_items(section: ReadmeSection | None) -> list[str]:
    """Return checklist/criteria items from typed section blocks."""
    if section is None:
        return []
    items: list[str] = []
    for block in section.content_blocks(recursive=False, include_paragraphs=True):
        if block.kind is ReadmeBlockKind.CRITERIA:
            items.extend(str(item).strip() for item in block.items if str(item).strip())
    items.extend(_bullet_items(section_label_block(section, "Что должно получиться")))
    seen: set[str] = set()
    unique_items: list[str] = []
    for item in items:
        key = re.sub(r"\s+", " ", item).casefold()
        if key and key not in seen:
            seen.add(key)
            unique_items.append(item)
    return unique_items


def _bullet_items(markdown: str) -> list[str]:
    """Extract plain bullet/checklist items from one canonical label block."""
    items: list[str] = []
    for line in (markdown or "").splitlines():
        stripped = line.strip()
        match = re.match(r"^[-*]\s*(?:\[[ xX]?\]\s*)?(?P<item>.+?)\s*$", stripped)
        if match:
            items.append(match.group("item").strip())
    return items


def extract_colon_field(text: str, label: str) -> str:
    """Extract a colon-prefixed field from a canonical action block."""
    labels = ["Ситуация", "Исходные данные", "Цель", "Подход"]
    target = label.casefold()
    capturing = False
    collected: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        current_label = next(
            (item for item in labels if stripped.casefold().startswith(f"{item.casefold()}:")),
            "",
        )
        if current_label:
            if capturing:
                break
            if current_label.casefold() == target:
                capturing = True
                remainder = stripped.split(":", 1)[1].strip()
                if remainder:
                    collected.append(remainder)
            continue
        if capturing:
            collected.append(line)
    return "\n".join(collected).strip()


def task_block_from_section(section: ReadmeSection) -> TaskBlock:
    """Build a rubric task DTO from a typed README section."""
    action = section_label_block(section, "Что нужно сделать")
    return TaskBlock(
        title=section.title,
        body=section_prose_text(section),
        artifact_paths=section_artifact_paths(section),
        has_action_block=section_has_label(section, "Что нужно сделать"),
        has_expected_result_block=section_has_label(section, "Что должно получиться"),
        has_submission_block=section_has_label(section, "Формат сдачи"),
        situation=extract_colon_field(action, "Ситуация"),
        goal=extract_colon_field(action, "Цель"),
        approach=extract_colon_field(action, "Подход"),
        expected_result=section_label_block(section, "Что должно получиться"),
        criteria_items=section_criteria_items(section),
    )


def practice_brief_from_document(document: ReadmeDocument, *, language: str = "ru") -> str:
    """Build a short typed practice brief from task titles, goals, and expected results."""
    blocks: list[str] = []
    for section in practice_task_sections(document, language=language):
        task = task_block_from_section(section)
        blocks.append(
            "\n".join(
                part
                for part in [
                    f"Задание: {task.title}",
                    f"Цель: {task.goal}" if task.goal else "",
                    f"Результат: {task.expected_result}" if task.expected_result else "",
                ]
                if part
            )
        )
    return "\n\n".join(block for block in blocks if block.strip())


def intro_content_without_instruction(document: ReadmeDocument, *, language: str = "ru") -> str:
    """Return Chapter 1 introduction text without the static instruction subsection."""
    chapter = document.chapter_section(1, language=language)
    if chapter is None:
        return ""
    for child in chapter.children:
        if re.search(r"^(?:введение|intro|introduction)\b", child.title, flags=re.I):
            return section_prose_text(child)
    return section_prose_text(chapter)


def chapter_blocks(document: ReadmeDocument, chapter_number: int, *, language: str = "ru") -> list[ReadmeBlock]:
    """Return typed content blocks for one chapter."""
    return section_blocks(document.chapter_section(chapter_number, language=language))


def toc_section(document: ReadmeDocument) -> ReadmeSection | None:
    """Find the document TOC section by localized title fragments."""
    for section in document.sections:
        if section.metadata.get("section_kind") == "toc" and section.level == 2:
            return section
    for fragment in ("Содержание", "Оглавление", "Content", "Table of contents", "Мазмун"):
        section = document.section_by_title_fragment(fragment)
        if section is not None and section.level == 2:
            return section
    return None
