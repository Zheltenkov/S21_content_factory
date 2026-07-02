"""Typed phase results for generation nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .readme_document import ReadmeDocument
from .schemas import PracticeTask, TheoryPart


@dataclass
class ContextPhaseResult:
    """Typed contract for initial context assembly."""

    seed: Any
    context_meta: Any
    context_analysis: Any
    context_bundle: Any
    similar_projects: list[Any] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class TitleAnnotationPhaseResult:
    """Typed contract for separately reviewable title and annotation generation."""

    title: str
    annotation: Any


@dataclass
class StructurePhaseResult:
    """Typed contract for README structure assembly after title approval."""

    markdown: str
    preflight_result: Any
    intro_section: Any
    blueprint: Any


@dataclass
class SkeletonPhaseResult(StructurePhaseResult):
    """Typed contract for full skeleton generation including title and annotation."""

    title: str
    annotation: Any


@dataclass
class TheoryPhaseResult:
    """Typed contract for Phase 2 theory generation."""

    markdown: str
    readme_document: ReadmeDocument
    theory_parts: list[TheoryPart]
    issues: list[Any]
    warnings: list[str]


@dataclass
class PracticePhaseResult:
    """Typed contract for Phase 3 practice generation."""

    markdown: str
    readme_document: ReadmeDocument
    practice_tasks: list[PracticeTask]
    issues: list[Any]
    warnings: list[str]
    bonus_tasks: list[PracticeTask] = field(default_factory=list)
    artifact_chain_plan: Any | None = None
    evidence_specs: list[Any] = field(default_factory=list)
    dataset_files: list[dict[str, Any]] = field(default_factory=list)
    practice_critic_issues: list[Any] = field(default_factory=list)


@dataclass
class QualityPhaseResult:
    """Typed contract for Phase 4 quality processing."""

    markdown: str
    readme_document: ReadmeDocument


@dataclass
class EvaluationPhaseResult:
    """Typed contract for final evaluation."""

    rubric_json: dict[str, Any]
    issues: list[Any]
    readme_document: ReadmeDocument


@dataclass
class TranslationPhaseResult:
    """Typed contract for final translation."""

    markdown: str
    translated_markdown: str
    readme_document: ReadmeDocument
    translated_readme_document: ReadmeDocument | None = None
