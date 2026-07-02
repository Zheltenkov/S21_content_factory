"""Typed workflow profiles for generation modes.

The profile is the product contract between backend orchestration and UI:
it tells consumers which workflow capabilities are available without
duplicating mode-specific conditions across screens.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

WorkflowProfileId = Literal["standard", "methodology"]


class WorkflowCapabilities(BaseModel):
    """Feature flags exposed by a generation workflow profile."""

    model_config = ConfigDict(extra="forbid")

    project_regeneration: bool = True
    section_regeneration: bool = True
    methodology_assistant: bool = False
    stage_review: bool = False
    final_readme_editing: bool = True
    checklist_editing: bool = True


class WorkflowGate(BaseModel):
    """Human-in-the-loop gate after a workflow node."""

    model_config = ConfigDict(extra="forbid")

    after_stage: str
    action: Literal["approve_or_revise"] = "approve_or_revise"


class WorkflowProfile(BaseModel):
    """Serializable workflow mode contract."""

    model_config = ConfigDict(extra="forbid")

    id: WorkflowProfileId
    title: str
    description: str
    stages: list[str]
    gates: list[WorkflowGate] = Field(default_factory=list)
    capabilities: WorkflowCapabilities = Field(default_factory=WorkflowCapabilities)


GENERATION_STAGE_IDS: tuple[str, ...] = (
    "context",
    "task_planning",
    "title_annotation",
    "skeleton",
    "theory",
    "practice",
    "global_quality",
    "evaluation",
    "finalize",
)

METHODOLOGY_GATE_STAGE_IDS: tuple[str, ...] = (
    "context",
    "task_planning",
    "title_annotation",
    "skeleton",
    "theory",
    "practice",
    "global_quality",
    "evaluation",
)


STANDARD_WORKFLOW_PROFILE = WorkflowProfile(
    id="standard",
    title="Обычный режим",
    description="Автоматическая генерация результата без ручных контрольных точек между этапами.",
    stages=list(GENERATION_STAGE_IDS),
    capabilities=WorkflowCapabilities(
        project_regeneration=True,
        section_regeneration=True,
        methodology_assistant=False,
        stage_review=False,
        final_readme_editing=True,
        checklist_editing=True,
    ),
)

METHODOLOGY_WORKFLOW_PROFILE = WorkflowProfile(
    id="methodology",
    title="Методологический режим",
    description="Генерация с human-in-the-loop контрольными точками и командами методолога.",
    stages=list(GENERATION_STAGE_IDS),
    gates=[WorkflowGate(after_stage=stage_id) for stage_id in METHODOLOGY_GATE_STAGE_IDS],
    capabilities=WorkflowCapabilities(
        project_regeneration=True,
        section_regeneration=True,
        methodology_assistant=True,
        stage_review=True,
        final_readme_editing=True,
        checklist_editing=True,
    ),
)

WORKFLOW_PROFILES: dict[WorkflowProfileId, WorkflowProfile] = {
    "standard": STANDARD_WORKFLOW_PROFILE,
    "methodology": METHODOLOGY_WORKFLOW_PROFILE,
}


def coerce_bool(value: Any) -> bool:
    """Interpret API/form truthy values consistently."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled", "methodology"}
    return bool(value)


def get_workflow_profile(profile_id: str | None) -> WorkflowProfile:
    """Return a known profile, defaulting to the production-safe standard mode."""
    if profile_id in WORKFLOW_PROFILES:
        return WORKFLOW_PROFILES[profile_id]  # type: ignore[index]
    return STANDARD_WORKFLOW_PROFILE


def resolve_workflow_profile(
    seed_or_payload: Any | None = None,
    *,
    methodology_human_review: Any | None = None,
) -> WorkflowProfile:
    """Resolve workflow profile from a seed payload or explicit flag."""
    explicit_profile = _read_value(seed_or_payload, "workflow_profile_id") or _read_value(
        seed_or_payload, "workflow_profile"
    )
    if isinstance(explicit_profile, dict):
        explicit_profile = explicit_profile.get("id")
    if explicit_profile and str(explicit_profile) in WORKFLOW_PROFILES:
        return get_workflow_profile(str(explicit_profile))

    raw_review_flag = methodology_human_review
    if raw_review_flag is None:
        raw_review_flag = _read_value(seed_or_payload, "methodology_human_review")
    return METHODOLOGY_WORKFLOW_PROFILE if coerce_bool(raw_review_flag) else STANDARD_WORKFLOW_PROFILE


def workflow_profile_payload(profile: WorkflowProfile | str | None) -> dict[str, Any]:
    """Serialize a profile for API/UI payloads."""
    resolved = get_workflow_profile(profile) if isinstance(profile, str) or profile is None else profile
    return resolved.model_dump(mode="json")


def _read_value(payload: Any | None, key: str) -> Any:
    if payload is None:
        return None
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key, None)
