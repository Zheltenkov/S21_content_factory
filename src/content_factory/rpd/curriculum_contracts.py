"""Typed contracts for the public curriculum-table projection."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

WorkloadKind = Literal["lectures", "practical", "independent", "control"]
WORKLOAD_KINDS: tuple[WorkloadKind, ...] = (
    "lectures",
    "practical",
    "independent",
    "control",
)


class CurriculumTitleDraft(BaseModel):
    """One editorial title proposal keyed by a stable one-based module index."""

    module_index: int = Field(ge=1)
    title: str


class CurriculumTitleResponse(BaseModel):
    modules: list[CurriculumTitleDraft] = Field(default_factory=list)


class CurriculumWorkloadProjection(BaseModel):
    """Module-level provenance for the four workload cells shown to a methodologist."""

    module_index: int = Field(ge=1)
    total_hours: str = "0"
    allocated_hours: dict[WorkloadKind, str] = Field(default_factory=dict)
    exact_allocated_hours: dict[WorkloadKind, str] = Field(default_factory=dict)
    inferred_hours: str = "0"
    provenance: Literal["source_exact", "profile_default", "mixed"] = "source_exact"
    review_required: bool = False


class CurriculumProjectionTrace(BaseModel):
    """Audit trail for semantic title cleanup and deterministic workload allocation."""

    status: Literal["not_run", "deterministic", "llm"] = "not_run"
    source_titles: list[str] = Field(default_factory=list)
    display_titles: list[str] = Field(default_factory=list)
    title_issues: list[str] = Field(default_factory=list)
    workload_kinds: tuple[WorkloadKind, ...] = WORKLOAD_KINDS
    workload_projection: list[CurriculumWorkloadProjection] = Field(default_factory=list)
    inferred_workload_rows: int = 0
    unknown_workload_formats: list[str] = Field(default_factory=list)


class CurriculumTableResult(BaseModel):
    header: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    trace: CurriculumProjectionTrace = Field(default_factory=CurriculumProjectionTrace)


class CurriculumTableValidation(BaseModel):
    schema_violations: list[str] = Field(default_factory=list)
    title_violations: list[str] = Field(default_factory=list)
    workload_violations: list[str] = Field(default_factory=list)


__all__ = [
    "WORKLOAD_KINDS",
    "CurriculumProjectionTrace",
    "CurriculumTableResult",
    "CurriculumTableValidation",
    "CurriculumTitleDraft",
    "CurriculumTitleResponse",
    "CurriculumWorkloadProjection",
    "WorkloadKind",
]
