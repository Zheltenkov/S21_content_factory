"""Typed state artifacts for the generation pipeline."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from content_gen.methodology.models import StageRepairResult, StageReviewResult

from .schemas import Annotation, IntroSection, ProjectSpec, TheoryPart

_RUNTIME_ONLY_CONTEXT_KEYS = frozenset({"observability_sink"})


class ProjectContextBundle(BaseModel):
    """Normalized curriculum-aware context passed through the pipeline."""

    context_source: str = "curriculum_only"
    thematic_block: str = ""
    current_project_order: int | None = None
    previous_projects_count: int = 0
    is_first_project: bool = False
    reference_enabled: bool = False
    context_summary: str = ""
    narrative_anchor: str = ""
    narrative_contract: dict[str, Any] = Field(default_factory=dict)
    aligned_skills: list[str] = Field(default_factory=list)
    new_skills: list[str] = Field(default_factory=list)
    continued_learning_outcomes: list[str] = Field(default_factory=list)
    new_learning_outcomes: list[str] = Field(default_factory=list)
    used_tools: list[str] = Field(default_factory=list)
    new_tools: list[str] = Field(default_factory=list)


class ProjectBlueprint(BaseModel):
    """Explicit document blueprint assembled before chapter generation."""

    language: str = "ru"
    has_bonus: bool = False
    section_order: list[str] = Field(default_factory=list)
    chapter_titles: dict[str, str] = Field(default_factory=dict)
    intro_subsections: list[str] = Field(default_factory=list)
    planned_tasks_count: int | None = None
    planned_task_complexity: str | None = None
    lo_task_map: dict[str, list[int]] = Field(default_factory=dict)
    theory_task_map: dict[str, list[int]] = Field(default_factory=dict)
    story_map_contract: dict[str, Any] = Field(default_factory=dict)
    practice_plan_contract: dict[str, Any] = Field(default_factory=dict)


class ProjectFlowState(BaseModel):
    """Explicit state store for the generation flow."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    raw_input: dict[str, Any] = Field(default_factory=dict)
    track_files: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    issues: list[Any] = Field(default_factory=list)
    assets_binary: dict[str, Any] = Field(default_factory=dict)

    target_language: str | None = None
    generate_bonus: bool = False

    seed: Any | None = None
    context_meta: Any | None = None
    context_analysis: Any | None = None
    context_bundle: ProjectContextBundle | None = None
    title: str | None = None
    annotation: Annotation | None = None
    intro_section: IntroSection | None = None
    blueprint: ProjectBlueprint | None = None
    theory_parts: list[TheoryPart] = Field(default_factory=list)
    similar_projects: list[Any] = Field(default_factory=list)
    task_plan: Any | None = None
    story_map_contract: Any | None = None
    practice_plan_contract: Any | None = None
    artifact_chain_plan: Any | None = None
    evidence_specs: list[Any] = Field(default_factory=list)
    dataset_files: list[Any] = Field(default_factory=list)
    section_contexts: dict[str, Any] = Field(default_factory=dict)

    markdown: str | None = None
    translated_markdown: str | None = None
    rubric_json: dict[str, Any] | None = None
    practice_critic_issues: list[Any] | None = None
    practice_tasks: list[Any] = Field(default_factory=list)
    bonus_tasks: list[Any] = Field(default_factory=list)
    methodology_reviews: list[StageReviewResult] = Field(default_factory=list)
    methodology_repairs: list[StageRepairResult] = Field(default_factory=list)
    methodology_gate_decisions: list[Any] = Field(default_factory=list)

    result: Any | None = None
    project_spec: ProjectSpec | None = None
    flow_trace: list[dict[str, Any]] = Field(default_factory=list)
    stopped_at: str | None = None
    stopped_reason: str | None = None

    @classmethod
    def from_initial_input(cls, raw_input: dict[str, Any], track_files: list[str] | None = None) -> "ProjectFlowState":
        """Construct initial pipeline state."""
        return cls(
            raw_input=raw_input,
            track_files=track_files or [],
            target_language=str(raw_input.get("language", "ru")).lower().strip(),
        )

    def to_context(self) -> dict[str, Any]:
        """Expose a legacy-compatible dict context plus explicit typed state."""
        context = self.model_dump(exclude_none=True, mode="python")
        context["state"] = self
        return context

    def apply_updates(self, updates: dict[str, Any] | None) -> None:
        """Apply handler updates to typed state."""
        if not updates:
            return
        for key, value in updates.items():
            if key == "state":
                continue
            setattr(self, key, value)

    def sync_from_context(self, context: dict[str, Any]) -> None:
        """Synchronize typed state from the mutable execution context."""
        for key, value in context.items():
            if key == "state" or key in _RUNTIME_ONLY_CONTEXT_KEYS:
                continue
            setattr(self, key, value)
