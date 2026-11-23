"""Typed contracts for internal DPO-PK sections 5.1-5.3."""

from __future__ import annotations

from pydantic import BaseModel, Field


class OrganizationalConditionsTrace(BaseModel):
    """Source lineage and explicit human-only fields for organizational conditions."""

    program_topic: str = ""
    source_software_values: list[str] = Field(default_factory=list)
    detected_tools: list[str] = Field(default_factory=list)
    human_required_fields: list[str] = Field(default_factory=list)


class OrganizationalConditionsResult(BaseModel):
    methodological_header: list[str] = Field(default_factory=list)
    methodological_rows: list[list[str]] = Field(default_factory=list)
    material_header: list[str] = Field(default_factory=list)
    material_rows: list[list[str]] = Field(default_factory=list)
    staffing_header: list[str] = Field(default_factory=list)
    staffing_rows: list[list[str]] = Field(default_factory=list)
    trace: OrganizationalConditionsTrace = Field(default_factory=OrganizationalConditionsTrace)


class ConditionsTableValidation(BaseModel):
    schema_violations: list[str] = Field(default_factory=list)
    label_violations: list[str] = Field(default_factory=list)
    methodology_violations: list[str] = Field(default_factory=list)
    value_violations: list[str] = Field(default_factory=list)
    review_required: list[str] = Field(default_factory=list)


__all__ = [
    "ConditionsTableValidation",
    "OrganizationalConditionsResult",
    "OrganizationalConditionsTrace",
]
