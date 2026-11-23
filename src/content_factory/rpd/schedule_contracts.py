"""Typed contracts for the calendar curriculum schedule in section 3.2."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ScheduleRowProjection(BaseModel):
    """One module-to-period projection retained for audit and quality checks."""

    module_index: int = Field(ge=1)
    source_title: str = ""
    component: str = ""
    hours: str = "0"
    start_week: int = Field(ge=1)
    end_week: int = Field(ge=1)
    organization_form: str = ""
    control_form: str = ""
    is_final: bool = False
    final_status: Literal["not_final", "source_explicit", "profile_default"] = "not_final"


class ScheduleProjectionTrace(BaseModel):
    """Provenance for deterministic periods and assessment-form wording."""

    status: Literal["not_run", "passport_duration", "sequential_default"] = "not_run"
    duration_source: str = ""
    duration_weeks: int = 0
    final_module_index: int = 0
    final_inferred: bool = False
    review_required: bool = False
    rows: list[ScheduleRowProjection] = Field(default_factory=list)


class ScheduleTableResult(BaseModel):
    header: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    trace: ScheduleProjectionTrace = Field(default_factory=ScheduleProjectionTrace)


class ScheduleTableValidation(BaseModel):
    schema_violations: list[str] = Field(default_factory=list)
    component_violations: list[str] = Field(default_factory=list)
    period_violations: list[str] = Field(default_factory=list)
    hour_violations: list[str] = Field(default_factory=list)
    methodology_violations: list[str] = Field(default_factory=list)


__all__ = [
    "ScheduleProjectionTrace",
    "ScheduleRowProjection",
    "ScheduleTableResult",
    "ScheduleTableValidation",
]
