"""Typed contracts for generation flow node execution."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .readme_document import ReadmeDocument


class GenerationContext(BaseModel):
    """Typed view over the mutable AgentFlow context.

    The flow runner still owns the runtime dict. This model gives node services
    typed access to the keys they consume while the AgentFlow context remains
    the mutable state carrier at orchestration boundaries.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    raw_input: dict[str, Any] = Field(default_factory=dict)
    track_files: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    issues: list[Any] = Field(default_factory=list)
    section_contexts: dict[str, Any] = Field(default_factory=dict)
    fallback_traces: list[dict[str, Any]] = Field(default_factory=list)

    target_language: str | None = None
    generate_bonus: bool = False

    seed: Any | None = None
    context_meta: Any | None = None
    context_analysis: Any | None = None
    context_bundle: Any | None = None
    similar_projects: list[Any] = Field(default_factory=list)
    task_plan: Any | None = None
    story_map_contract: Any | None = None
    practice_plan_contract: Any | None = None
    artifact_chain_plan: Any | None = None
    evidence_specs: list[Any] = Field(default_factory=list)

    title: str | None = None
    annotation: Any | None = None
    intro_section: Any | None = None
    blueprint: Any | None = None
    readme_document: Any | None = None
    markdown: str | None = None
    theory_parts: list[Any] = Field(default_factory=list)

    @classmethod
    def from_flow_context(cls, context: dict[str, Any]) -> "GenerationContext":
        """Create a typed view from the mutable flow context dict."""
        data = {key: value for key, value in context.items() if key != "state"}
        return cls(**data)

    def require(self, key: str) -> Any:
        """Return a required field with a clear error for broken node order."""
        value = getattr(self, key, None)
        if value is None:
            raise KeyError(f"GenerationContext is missing required field: {key}")
        return value


class TypedNodeOutput(BaseModel):
    """Base output contract for typed node services."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    issues: list[str] = Field(default_factory=list)
    status: Literal["success", "skipped", "error"] = "success"

    def updates(self) -> dict[str, Any]:
        """Return AgentFlow context updates."""
        raise NotImplementedError


class ContextNodeResult(TypedNodeOutput):
    """Typed output of the context node."""

    seed: Any
    target_language: str
    generate_bonus: bool
    context_meta: Any
    context_analysis: Any
    context_bundle: Any
    similar_projects: list[Any] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def updates(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "target_language": self.target_language,
            "generate_bonus": self.generate_bonus,
            "context_meta": self.context_meta,
            "context_analysis": self.context_analysis,
            "context_bundle": self.context_bundle,
            "similar_projects": self.similar_projects,
        }


class TitleAnnotationNodeResult(TypedNodeOutput):
    """Typed output of the title/annotation node."""

    title: str
    annotation: Any

    def updates(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "annotation": self.annotation,
        }


class TaskPlanningNodeResult(TypedNodeOutput):
    """Typed output of the task-planning node."""

    seed: Any
    task_plan: Any | None = None
    story_map_contract: Any | None = None
    practice_plan_contract: Any | None = None
    artifact_chain_plan: Any | None = None
    evidence_specs: list[Any] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    fallback_traces: list[dict[str, Any]] = Field(default_factory=list)

    def updates(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "task_plan": self.task_plan,
            "story_map_contract": self.story_map_contract,
            "practice_plan_contract": self.practice_plan_contract,
            "artifact_chain_plan": self.artifact_chain_plan,
            "evidence_specs": self.evidence_specs,
            "fallback_traces": self.fallback_traces,
        }


class QualityNodeResult(TypedNodeOutput):
    """Typed output of the global quality node."""

    markdown: str
    readme_document: ReadmeDocument | None = None
    fallback_traces: list[dict[str, Any]] = Field(default_factory=list)

    def updates(self) -> dict[str, Any]:
        return {
            "markdown": self.markdown,
            "readme_document": self.readme_document,
            "fallback_traces": self.fallback_traces,
        }


class EvaluationNodeResult(TypedNodeOutput):
    """Typed output of the final evaluation node."""

    rubric_json: dict[str, Any]
    serialized_issues: list[Any] = Field(default_factory=list)

    def updates(self) -> dict[str, Any]:
        return {
            "rubric_json": self.rubric_json,
        }


class TranslationNodeResult(TypedNodeOutput):
    """Typed output of the translation node."""

    markdown: str
    translated_markdown: str
    seed: Any
    target_language: str
    readme_document: ReadmeDocument | None = None
    fallback_traces: list[dict[str, Any]] = Field(default_factory=list)

    def updates(self) -> dict[str, Any]:
        updates = {
            "markdown": self.markdown,
            "translated_markdown": self.translated_markdown,
            "seed": self.seed,
            "readme_document": self.readme_document,
        }
        if self.fallback_traces:
            updates["fallback_traces"] = self.fallback_traces
        return updates


class PracticeNodeResult(TypedNodeOutput):
    """Typed output of the practice node."""

    markdown: str
    readme_document: ReadmeDocument | None = None
    practice_critic_issues: list[Any] = Field(default_factory=list)
    practice_tasks: list[Any] = Field(default_factory=list)
    bonus_tasks: list[Any] = Field(default_factory=list)
    blueprint: Any | None = None
    artifact_chain_plan: Any | None = None
    evidence_specs: list[Any] = Field(default_factory=list)
    dataset_files: list[Any] = Field(default_factory=list)
    section_contexts: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    serialized_issues: list[Any] = Field(default_factory=list)

    def updates(self) -> dict[str, Any]:
        return {
            "markdown": self.markdown,
            "readme_document": self.readme_document,
            "practice_critic_issues": self.practice_critic_issues,
            "practice_tasks": self.practice_tasks,
            "bonus_tasks": self.bonus_tasks,
            "blueprint": self.blueprint,
            "artifact_chain_plan": self.artifact_chain_plan,
            "evidence_specs": self.evidence_specs,
            "dataset_files": self.dataset_files,
            "section_contexts": self.section_contexts,
        }


class FinalizeNodeResult(TypedNodeOutput):
    """Typed output of the final assembly node."""

    result: Any
    project_spec: Any
    markdown: str
    readme_document: ReadmeDocument | None = None
    translated_markdown: str | None = None
    assets_binary: dict[str, Any] = Field(default_factory=dict)
    section_contexts: dict[str, Any] = Field(default_factory=dict)

    def updates(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "project_spec": self.project_spec,
            "markdown": self.markdown,
            "readme_document": self.readme_document,
            "translated_markdown": self.translated_markdown,
            "assets_binary": self.assets_binary,
            "section_contexts": self.section_contexts,
        }


class SkeletonNodeResult(TypedNodeOutput):
    """Typed output of the skeleton node."""

    markdown: str
    readme_document: ReadmeDocument | None = None
    title: str
    annotation: Any
    intro_section: Any
    blueprint: Any | None = None
    warnings: list[str] = Field(default_factory=list)
    serialized_issues: list[Any] = Field(default_factory=list)

    def updates(self) -> dict[str, Any]:
        return {
            "markdown": self.markdown,
            "readme_document": self.readme_document,
            "title": self.title,
            "annotation": self.annotation,
            "intro_section": self.intro_section,
            "blueprint": self.blueprint,
        }


class TheoryNodeResult(TypedNodeOutput):
    """Typed output of the theory node."""

    markdown: str
    readme_document: ReadmeDocument | None = None
    theory_parts: list[Any] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    serialized_issues: list[Any] = Field(default_factory=list)

    def updates(self) -> dict[str, Any]:
        return {
            "markdown": self.markdown,
            "readme_document": self.readme_document,
            "theory_parts": self.theory_parts,
        }
