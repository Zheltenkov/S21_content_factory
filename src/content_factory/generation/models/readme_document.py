"""Typed README document model used before final Markdown rendering."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .readme_blocks import (
    ReadmeBlock,
    ReadmeBlockKind,
    block_counts as count_readme_blocks,
    extract_readme_blocks,
    materialize_readme_blocks,
    render_readme_blocks,
)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", flags=re.MULTILINE)
_FENCE_RE = re.compile(r"^\s*(```+|~~~+)")
_ARTIFACT_PATH_RE = re.compile(r"`?([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.{}:-]+)+\.[A-Za-z0-9]+)`?")


class ReadmeSection(BaseModel):
    """One logical README section with optional nested children."""

    model_config = ConfigDict(extra="forbid")

    title: str
    level: int = Field(ge=1, le=6)
    body: str = ""
    blocks: list[ReadmeBlock] = Field(default_factory=list)
    children: list["ReadmeSection"] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        """Populate stable section metadata while preserving caller-provided fields."""
        if not self.blocks and (self.body or "").strip():
            self.blocks = materialize_readme_blocks(self.body)
        inferred = self._infer_metadata(self.title, self.level, self.body, self.blocks)
        metadata = dict(self.metadata or {})
        for key, value in inferred.items():
            metadata.setdefault(key, value)
        self.metadata = metadata

    def to_markdown(self) -> str:
        """Render this section and its children as Markdown."""
        blocks = [f"{'#' * self.level} {self.title}".rstrip()]
        body = self.body_markdown()
        if body:
            blocks.append(body)
        for child in self.children:
            rendered_child = child.to_markdown().strip()
            if rendered_child:
                blocks.append(rendered_child)
        return "\n\n".join(blocks).strip()

    def body_markdown(self) -> str:
        """Render the section body from typed blocks when they cover the body."""
        body = (self.body or "").strip()
        if not self.blocks:
            return body
        rendered = render_readme_blocks(self.blocks)
        if body and not _same_markdown(rendered, body):
            return body
        return rendered

    def flatten(self) -> list["ReadmeSection"]:
        """Return this section and all descendants in document order."""
        result = [self]
        for child in self.children:
            result.extend(child.flatten())
        return result

    def content_blocks(
        self,
        *,
        recursive: bool = True,
        path: list[str] | None = None,
        include_paragraphs: bool = False,
    ) -> list[ReadmeBlock]:
        """Return typed content blocks from this section."""
        section_path = [*(path or []), self.title]
        source_blocks = self.blocks or extract_readme_blocks(self.body, include_paragraphs=True)
        if not include_paragraphs:
            source_blocks = [block for block in source_blocks if block.kind is not ReadmeBlockKind.PARAGRAPH]
        blocks = [
            block.with_section(self.title, section_path)
            for block in source_blocks
        ]
        if recursive:
            for child in self.children:
                blocks.extend(
                    child.content_blocks(
                        recursive=True,
                        path=section_path,
                        include_paragraphs=include_paragraphs,
                    )
                )
        return blocks

    def block_counts(self, *, recursive: bool = True, include_paragraphs: bool = False) -> dict[str, int]:
        """Return typed block counts for this section."""
        return count_readme_blocks(
            self.content_blocks(recursive=recursive, include_paragraphs=include_paragraphs)
        )

    def has_label(self, label: str) -> bool:
        """Return whether this section body has a bold Markdown label."""
        return bool(self.label_block(label))

    def label_block(self, label: str) -> str:
        """Return text under a bold label such as ``**Что нужно сделать**``."""
        return _label_block(self.body_markdown(), label)

    def body_before_label(self, label: str) -> str:
        """Return body text before the first matching bold label."""
        return _body_before_label(self.body_markdown(), label)

    @classmethod
    def from_markdown(
        cls,
        markdown: str,
        *,
        fallback_title: str = "Section",
        fallback_level: int = 2,
    ) -> "ReadmeSection":
        """Parse a standalone Markdown section into a typed section tree."""
        text = (markdown or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            return cls(title=fallback_title, level=fallback_level)

        heading_matches = ReadmeDocument._heading_matches(text)
        if not heading_matches:
            return cls(title=fallback_title, level=fallback_level, body=text)

        document = ReadmeDocument.from_markdown(text, fallback_title=fallback_title)
        if len(heading_matches[0].group(1)) == 1:
            return cls(
                title=document.title,
                level=1,
                body=document.annotation,
                children=document.sections,
            )
        if len(document.sections) == 1:
            return document.sections[0]
        return cls(
            title=fallback_title,
            level=fallback_level,
            body=document.annotation,
            children=document.sections,
        )

    @staticmethod
    def _infer_metadata(
        title: str,
        level: int,
        body: str,
        blocks: list[ReadmeBlock] | None = None,
    ) -> dict[str, Any]:
        """Infer stable section metadata from heading, body, and structured blocks."""
        block_summary = count_readme_blocks(blocks or extract_readme_blocks(body or ""))
        metadata: dict[str, Any] = {
            "metadata_schema": "readme_section/v1",
            "slug": ReadmeDocument.slugify(title),
            "section_kind": "generic",
            "block_counts": block_summary,
        }
        title_text = (title or "").strip()
        title_lower = title_text.casefold()

        chapter_match = re.search(r"(?:глава|chapter)\s+(\d+)|(\d+)\s*[- ]?бөлүм", title_lower, flags=re.I)
        if chapter_match and level == 2:
            metadata["section_kind"] = "chapter"
            metadata["chapter_number"] = int(next(group for group in chapter_match.groups() if group))
        elif any(fragment in title_lower for fragment in ("содержание", "оглавление", "content", "table of contents", "мазмун")):
            metadata["section_kind"] = "toc"
        elif re.match(r"^(?:2\.\d+|часть\s+\d+)\b", title_lower, flags=re.I):
            metadata["section_kind"] = "theory_part"
            section_number = re.match(r"^(2\.\d+)", title_text)
            if section_number:
                metadata["section_number"] = section_number.group(1)
        elif task_match := re.match(r"^(?:задание|задача|task)\s+(\d+)", title_lower, flags=re.I):
            metadata["section_kind"] = "practice_task"
            metadata["task_number"] = int(task_match.group(1))
        elif "бонус" in title_lower or "bonus" in title_lower:
            metadata["section_kind"] = "bonus"
        elif re.match(r"^(?:заключение|итог проекта|финал проекта|завершение проекта)\b", title_lower, flags=re.I):
            metadata["section_kind"] = "final"

        artifact_paths = sorted(set(match.group(1) for match in _ARTIFACT_PATH_RE.finditer(body or "")))
        if artifact_paths:
            metadata["artifact_paths"] = artifact_paths
        return metadata


class ReadmeDocument(BaseModel):
    """Typed README representation; Markdown is only an output format."""

    model_config = ConfigDict(extra="forbid")

    title: str
    annotation: str = ""
    sections: list[ReadmeSection] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_markdown(self) -> str:
        """Render the document as Markdown with stable spacing between blocks."""
        blocks = [f"# {self.title}".strip()]
        annotation = self.annotation.strip()
        if annotation:
            blocks.append(annotation)
        for section in self.sections:
            rendered_section = section.to_markdown().strip()
            if rendered_section:
                blocks.append(rendered_section)
        return "\n\n".join(blocks).strip() + "\n"

    def outline(self) -> list[dict[str, Any]]:
        """Return a serializable heading outline."""
        headings = [{"level": 1, "title": self.title}]
        for section in self.sections:
            headings.extend({"level": item.level, "title": item.title} for item in section.flatten())
        return headings

    def section_by_title_fragment(self, fragment: str) -> ReadmeSection | None:
        """Find the first section whose title contains the supplied fragment."""
        normalized = fragment.casefold().strip()
        if not normalized:
            return None
        for section in self.sections:
            for item in section.flatten():
                if normalized in item.title.casefold():
                    return item
        return None

    def chapter_section(self, chapter_number: int, *, language: str = "ru") -> ReadmeSection | None:
        """Find a top-level chapter section by localized chapter number."""
        labels = self._chapter_title_labels(chapter_number, language=language)
        for section in self.sections:
            normalized_title = section.title.casefold()
            if section.level == 2 and any(label in normalized_title for label in labels):
                return section
        return None

    def with_replaced_section_by_title_fragment(
        self,
        fragment: str,
        replacement: ReadmeSection | str,
        *,
        fallback_level: int = 2,
    ) -> tuple["ReadmeDocument", bool]:
        """Return a copy with the first matching section replaced."""
        normalized = fragment.casefold().strip()
        if not normalized:
            return self.model_copy(deep=True), False

        replacement_section = self._coerce_section(
            replacement,
            fallback_title=fragment.strip() or "Section",
            fallback_level=fallback_level,
        )
        document = self.model_copy(deep=True)
        replaced = self._replace_first_section(document.sections, normalized, replacement_section)
        return document, replaced

    def with_replaced_chapter_body(
        self,
        chapter_number: int,
        body: str,
        *,
        language: str = "ru",
    ) -> tuple["ReadmeDocument", bool]:
        """Return a copy where one chapter keeps its title but receives a typed body tree."""
        chapter = self.chapter_section(chapter_number, language=language)
        if chapter is None:
            return self.model_copy(deep=True), False

        replacement_markdown = f"{'#' * chapter.level} {chapter.title}\n\n{(body or '').strip()}"
        replacement = ReadmeSection.from_markdown(
            replacement_markdown,
            fallback_title=chapter.title,
            fallback_level=chapter.level,
        )
        replacement.metadata = dict(chapter.metadata)
        return self.with_replaced_section_by_title_fragment(
            chapter.title,
            replacement,
            fallback_level=chapter.level,
        )

    def with_replaced_chapter_children(
        self,
        chapter_number: int,
        children: list[ReadmeSection],
        *,
        chapter_body: str = "",
        language: str = "ru",
    ) -> tuple["ReadmeDocument", bool]:
        """Return a copy where one chapter receives already-typed child sections."""
        labels = self._chapter_title_labels(chapter_number, language=language)
        document = self.model_copy(deep=True)
        for index, section in enumerate(document.sections):
            normalized_title = section.title.casefold()
            if section.level == 2 and any(label in normalized_title for label in labels):
                document.sections[index] = section.model_copy(
                    update={
                        "body": (chapter_body or "").strip(),
                        "blocks": materialize_readme_blocks(chapter_body or ""),
                        "children": [child.model_copy(deep=True) for child in children],
                    },
                    deep=True,
                )
                return document, True
        return document, False

    def with_upserted_section_by_title_fragment(
        self,
        fragment: str,
        replacement: ReadmeSection | str,
        *,
        fallback_level: int = 2,
    ) -> "ReadmeDocument":
        """Replace a matching section or append the section when it is absent."""
        document, replaced = self.with_replaced_section_by_title_fragment(
            fragment,
            replacement,
            fallback_level=fallback_level,
        )
        if replaced:
            return document
        document.sections.append(
            self._coerce_section(
                replacement,
                fallback_title=fragment.strip() or "Section",
                fallback_level=fallback_level,
            )
        )
        return document

    def without_section_by_title_fragment(self, fragment: str) -> tuple["ReadmeDocument", bool]:
        """Return a copy with the first matching section removed."""
        normalized = fragment.casefold().strip()
        if not normalized:
            return self.model_copy(deep=True), False
        document = self.model_copy(deep=True)
        removed = self._remove_first_section(document.sections, normalized)
        return document, removed

    def section_markdown_by_title_fragment(self, fragment: str) -> str:
        """Render the first section whose title contains fragment."""
        section = self.section_by_title_fragment(fragment)
        return section.to_markdown() if section else ""

    def markdown_subsections(self, *, min_level: int = 3) -> list[dict[str, str]]:
        """Return tab-ready rendered sections from the typed document tree."""
        sections: list[dict[str, str]] = []
        for section in self.sections:
            for item in section.flatten():
                if item.level >= min_level:
                    sections.append({"title": item.title, "markdown": item.to_markdown()})
        return sections

    def content_blocks(self, *, include_paragraphs: bool = False) -> list[ReadmeBlock]:
        """Return typed content blocks from the document."""
        blocks = [
            block.with_section(self.title, [self.title])
            for block in extract_readme_blocks(self.annotation, include_paragraphs=include_paragraphs)
        ]
        for section in self.sections:
            blocks.extend(section.content_blocks(path=[self.title], include_paragraphs=include_paragraphs))
        return blocks

    def block_counts(self, *, include_paragraphs: bool = False) -> dict[str, int]:
        """Return typed block counts for the whole document."""
        return count_readme_blocks(self.content_blocks(include_paragraphs=include_paragraphs))

    @classmethod
    def from_value(cls, value: Any, *, fallback_markdown: str = "", fallback_title: str = "README") -> "ReadmeDocument":
        """Hydrate a ReadmeDocument from model/dict/Markdown values."""
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            try:
                return cls.model_validate(value)
            except Exception:
                pass
        if isinstance(value, str) and value.strip():
            return cls.from_markdown(value, fallback_title=fallback_title)
        return cls.from_markdown(fallback_markdown, fallback_title=fallback_title)

    @classmethod
    def from_markdown(cls, markdown: str, *, fallback_title: str = "README") -> "ReadmeDocument":
        """Parse Markdown headings into a typed document without changing content."""
        text = (markdown or "").replace("\r\n", "\n").replace("\r", "\n")
        heading_matches = cls._heading_matches(text)
        if not heading_matches:
            return cls(title=fallback_title, annotation=text.strip())

        first = heading_matches[0]
        title = first.group(2).strip() if len(first.group(1)) == 1 else fallback_title
        annotation_start = first.end() if len(first.group(1)) == 1 else 0
        next_index = 1 if len(first.group(1)) == 1 else 0
        annotation_end = heading_matches[next_index].start() if next_index < len(heading_matches) else len(text)
        annotation = text[annotation_start:annotation_end].strip()

        root_sections: list[ReadmeSection] = []
        stack: list[ReadmeSection] = []
        for index, match in enumerate(heading_matches[next_index:], next_index):
            level = len(match.group(1))
            section_end = heading_matches[index + 1].start() if index + 1 < len(heading_matches) else len(text)
            section = ReadmeSection(
                title=match.group(2).strip(),
                level=level,
                body=text[match.end() : section_end].strip(),
            )
            while stack and stack[-1].level >= level:
                stack.pop()
            if stack:
                stack[-1].children.append(section)
            else:
                root_sections.append(section)
            stack.append(section)
        return cls(title=title, annotation=annotation, sections=root_sections)

    @staticmethod
    def _chapter_title_labels(chapter_number: int, *, language: str = "ru") -> list[str]:
        """Return accepted chapter title fragments for current and legacy locales."""
        number = str(chapter_number)
        labels = [
            f"глава {number}",
            f"chapter {number}",
            f"{number}-бөлүм",
            f"{number} бөлүм",
        ]
        language = (language or "ru").casefold().strip()
        if language in {"ky", "kg"}:
            labels.insert(0, f"{number}-бөлүм")
        elif language == "en":
            labels.insert(0, f"chapter {number}")
        else:
            labels.insert(0, f"глава {number}")
        return list(dict.fromkeys(label.casefold() for label in labels))

    @staticmethod
    def slugify(title: str) -> str:
        """Return a GitHub-style slug used by TOC and metadata consumers."""
        return re.sub(r"[^\w\- ]+", "", (title or ""), flags=re.U).strip().lower().replace(" ", "-")

    @staticmethod
    def _coerce_section(
        value: ReadmeSection | str,
        *,
        fallback_title: str,
        fallback_level: int,
    ) -> ReadmeSection:
        if isinstance(value, ReadmeSection):
            return value.model_copy(deep=True)
        return ReadmeSection.from_markdown(
            str(value or ""),
            fallback_title=fallback_title,
            fallback_level=fallback_level,
        )

    @classmethod
    def _replace_first_section(
        cls,
        sections: list[ReadmeSection],
        normalized_fragment: str,
        replacement: ReadmeSection,
    ) -> bool:
        for index, section in enumerate(sections):
            if normalized_fragment in section.title.casefold():
                sections[index] = replacement.model_copy(deep=True)
                return True
            if cls._replace_first_section(section.children, normalized_fragment, replacement):
                return True
        return False

    @classmethod
    def _remove_first_section(
        cls,
        sections: list[ReadmeSection],
        normalized_fragment: str,
    ) -> bool:
        for index, section in enumerate(list(sections)):
            if normalized_fragment in section.title.casefold():
                del sections[index]
                return True
            if cls._remove_first_section(section.children, normalized_fragment):
                return True
        return False

    @staticmethod
    def _heading_matches(text: str) -> list[re.Match[str]]:
        """Return Markdown heading matches outside fenced code blocks."""
        matches: list[re.Match[str]] = []
        in_fence: str | None = None
        position = 0
        for line in text.splitlines(keepends=True):
            line_without_break = line.rstrip("\n")
            fence_match = _FENCE_RE.match(line_without_break)
            if fence_match:
                fence_marker = fence_match.group(1)[:3]
                if in_fence == fence_marker:
                    in_fence = None
                elif in_fence is None:
                    in_fence = fence_marker
                position += len(line)
                continue
            if in_fence is None:
                heading_match = _HEADING_RE.match(text, position, position + len(line_without_break))
                if heading_match:
                    matches.append(heading_match)
            position += len(line)
        return matches


def _same_markdown(left: str, right: str) -> bool:
    """Compare Markdown bodies after whitespace normalization."""
    return re.sub(r"\s+", " ", (left or "").strip()) == re.sub(r"\s+", " ", (right or "").strip())


def _label_block(markdown: str, label: str) -> str:
    """Extract a body block after a bold Markdown label without relying on section regex."""
    target = _normalize_label(label)
    if not target:
        return ""

    capturing = False
    collected: list[str] = []
    for line in (markdown or "").splitlines():
        parsed = _parse_bold_label(line)
        if parsed is not None:
            current_label, remainder = parsed
            if capturing:
                break
            if _normalize_label(current_label) == target:
                capturing = True
                if remainder:
                    collected.append(remainder)
            continue
        if capturing:
            collected.append(line)
    return "\n".join(collected).strip()


def _body_before_label(markdown: str, label: str) -> str:
    """Return Markdown body before the first matching bold label."""
    target = _normalize_label(label)
    if not target:
        return (markdown or "").strip()

    collected: list[str] = []
    for line in (markdown or "").splitlines():
        parsed = _parse_bold_label(line)
        if parsed is not None and _normalize_label(parsed[0]) == target:
            break
        collected.append(line)
    return "\n".join(collected).strip()


def _parse_bold_label(line: str) -> tuple[str, str] | None:
    """Parse ``**Label:** optional text`` lines."""
    stripped = (line or "").strip()
    if not stripped.startswith("**"):
        return None
    close_index = stripped.find("**", 2)
    if close_index < 0:
        return None
    label = stripped[2:close_index].strip().rstrip(":").strip()
    if not label:
        return None
    return label, stripped[close_index + 2 :].strip()


def _normalize_label(label: str) -> str:
    return (label or "").strip().rstrip(":").casefold()
