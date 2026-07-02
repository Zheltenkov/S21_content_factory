"""Stable target registry for methodologist scoped revisions."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

TargetKind = Literal["field", "markdown_section", "material_file"]
TargetScope = Literal["local_section_only", "task_only", "materials_only"]


class SectionTarget(BaseModel):
    """A stable addressable target inside paused generation state."""

    id: str
    kind: TargetKind
    label: str
    stage: str
    selector: str
    scope: TargetScope
    start: int | None = None
    end: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SectionTargetRegistry(BaseModel):
    """Collection of stable methodologist revision targets."""

    targets: list[SectionTarget] = Field(default_factory=list)

    def find(self, value: str, *, kind: TargetKind | None = None, stage: str | None = None) -> SectionTarget | None:
        """Find a target by id, selector, label or material path."""
        normalized = _norm(value)
        if not normalized:
            return None
        for target in self.targets:
            if kind is not None and target.kind != kind:
                continue
            if stage is not None and target.stage != stage:
                continue
            candidates = [
                target.id,
                target.selector,
                target.label,
                str(target.metadata.get("path") or ""),
            ]
            for candidate in candidates:
                candidate_norm = _norm(candidate)
                if normalized == candidate_norm:
                    return target
        for target in self.targets:
            if kind is not None and target.kind != kind:
                continue
            if stage is not None and target.stage != stage:
                continue
            haystack = _norm(" ".join([target.id, target.selector, target.label, str(target.metadata.get("path") or "")]))
            if normalized in haystack or haystack in normalized:
                return target
        return None


def build_section_target_registry(context: dict[str, Any]) -> SectionTargetRegistry:
    """Build a registry from current markdown headings and material files."""
    markdown = str(context.get("markdown") or "")
    targets = _field_targets(context, markdown)
    targets.extend(_annotation_targets(markdown))
    targets.extend(_markdown_targets(markdown))
    targets.extend(_material_targets(context.get("dataset_files") or []))
    return SectionTargetRegistry(targets=targets)


def _field_targets(context: dict[str, Any], markdown: str) -> list[SectionTarget]:
    """Expose pre-markdown fields as scoped review targets."""
    targets: list[SectionTarget] = []
    title = str(context.get("title") or "").strip()
    if title:
        targets.append(
            SectionTarget(
                id="title",
                kind="field",
                label="Название проекта",
                stage="title",
                selector="title",
                scope="local_section_only",
                metadata={"field": "title"},
            )
        )

    annotation = context.get("annotation")
    annotation_text = ""
    if isinstance(annotation, dict):
        annotation_text = str(annotation.get("text") or "")
    elif hasattr(annotation, "text"):
        annotation_text = str(annotation.text or "")
    has_markdown_annotation = bool(markdown and re.search(r"^#\s+.+?$", markdown, flags=re.MULTILINE))
    if annotation_text.strip() and not has_markdown_annotation:
        targets.append(
            SectionTarget(
                id="annotation",
                kind="field",
                label="Аннотация",
                stage="annotation",
                selector="annotation",
                scope="local_section_only",
                metadata={"field": "annotation"},
            )
        )
    return targets


def _annotation_targets(markdown: str) -> list[SectionTarget]:
    if not markdown:
        return []
    title_match = re.search(r"^#\s+(.+?)\s*$", markdown, flags=re.MULTILINE)
    if not title_match:
        return []
    next_heading = re.search(r"^##\s+", markdown[title_match.end() :], flags=re.MULTILINE)
    end = len(markdown) if next_heading is None else title_match.end() + next_heading.start()
    start = title_match.end()
    if start >= end:
        return []
    return [
        SectionTarget(
            id="annotation",
            kind="markdown_section",
            label="Аннотация",
            stage="annotation",
            selector="annotation",
            scope="local_section_only",
            start=start,
            end=end,
            line_start=_line_number(markdown, start),
            line_end=_line_number(markdown, end),
            metadata={"heading_level": 1, "field": "annotation"},
        )
    ]


def _markdown_targets(markdown: str) -> list[SectionTarget]:
    if not markdown:
        return []

    matches = list(re.finditer(r"^(#{1,6})\s+(.+?)\s*$", markdown, flags=re.MULTILINE))
    targets: list[SectionTarget] = []
    used_ids: set[str] = set()
    chapter_id = ""
    chapter_stage = "final"

    for index, match in enumerate(matches):
        level = len(match.group(1))
        heading = match.group(2).strip()
        stage = _stage_for_heading(heading, chapter_stage if level > 2 else None)
        if level == 2:
            chapter_id = _chapter_id(heading) or _unique_id(f"section.{_slug(heading)}", used_ids)
            chapter_stage = stage
            target_id = chapter_id
        else:
            suffix = _subsection_suffix(heading) or _slug(heading)
            prefix = chapter_id or stage
            target_id = _unique_id(f"{prefix}.{suffix}", used_ids)

        if target_id in used_ids:
            target_id = _unique_id(target_id, used_ids)
        used_ids.add(target_id)

        end = len(markdown)
        for next_match in matches[index + 1 :]:
            if len(next_match.group(1)) <= level:
                end = next_match.start()
                break

        targets.append(
            SectionTarget(
                id=target_id,
                kind="markdown_section",
                label=heading,
                stage=stage,
                selector=heading,
                scope="local_section_only",
                start=match.start(),
                end=end,
                line_start=_line_number(markdown, match.start()),
                line_end=_line_number(markdown, end),
                metadata={"heading_level": level},
            )
        )
    return targets


def _material_targets(dataset_files: list[Any]) -> list[SectionTarget]:
    targets: list[SectionTarget] = []
    used_ids: set[str] = set()
    for index, item in enumerate(dataset_files, 1):
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").replace("\\", "/").strip()
        if not path:
            continue
        target_id = _unique_id(f"material.{_slug(path)}", used_ids)
        used_ids.add(target_id)
        size = _data_size(item.get("data"))
        targets.append(
            SectionTarget(
                id=target_id,
                kind="material_file",
                label=path,
                stage="dataset",
                selector=path,
                scope="materials_only",
                metadata={"path": path, "index": index, "bytes": size},
            )
        )
    return targets


def _stage_for_heading(heading: str, fallback: str | None = None) -> str:
    normalized = _norm(heading)
    if "аннотац" in normalized:
        return "annotation"
    if "глава 1" in normalized or "введение" in normalized or "инструкц" in normalized:
        return "skeleton"
    if "глава 2" in normalized or "теорет" in normalized or re.match(r"^2\.\d+\b", normalized):
        return "theory"
    if "глава 3" in normalized or "практи" in normalized or normalized.startswith("задание "):
        return "practice"
    if "бонус" in normalized:
        return "practice"
    return fallback or "final"


def _chapter_id(heading: str) -> str | None:
    match = re.search(r"глава\s+(\d+)", heading, flags=re.I)
    if not match:
        return None
    return f"chapter_{match.group(1)}"


def _subsection_suffix(heading: str) -> str | None:
    normalized = _norm(heading)
    canonical_part_match = re.search(r"\b2\.(\d+)\b", normalized)
    if canonical_part_match:
        return f"part_{canonical_part_match.group(1)}"
    task_match = re.search(r"\bзадание\s+(\d+)", normalized)
    if task_match:
        return f"task_{task_match.group(1)}"
    if "введение" in normalized:
        return "intro"
    if "инструкц" in normalized:
        return "instruction"
    return None


def _unique_id(base: str, used_ids: set[str]) -> str:
    candidate = base.strip(".") or "target"
    suffix = 2
    while candidate in used_ids:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


def _slug(value: str) -> str:
    text = value.replace("\\", "/").lower()
    text = re.sub(r"[^a-zа-яё0-9]+", "_", text, flags=re.I)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:80] or "target"


def _norm(value: str) -> str:
    normalized = str(value or "").replace("\\", "/").lower()
    normalized = re.sub(r"[^\wа-яё0-9/.-]+", " ", normalized, flags=re.I)
    return re.sub(r"\s+", " ", normalized).strip()


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def _data_size(data: Any) -> int:
    if data is None:
        return 0
    if isinstance(data, bytes):
        return len(data)
    return len(str(data).encode("utf-8"))
