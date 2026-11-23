"""Typed contracts for sections 4.1 and 4.2 of the internal DPO-PK template."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AssessmentProjectionTrace(BaseModel):
    """Source lineage for current controls and the final attestation passport."""

    current_module_indices: list[int] = Field(default_factory=list)
    current_components: list[str] = Field(default_factory=list)
    current_control_forms: list[str] = Field(default_factory=list)
    final_module_index: int = Field(default=0, ge=0)
    final_component: str = ""
    final_control_form: str = ""
    final_inferred: bool = False


class AssessmentTablesResult(BaseModel):
    """Both assessment tables are built together from one canonical projection."""

    current_header: list[str] = Field(default_factory=list)
    current_rows: list[list[str]] = Field(default_factory=list)
    final_header: list[str] = Field(default_factory=list)
    final_rows: list[list[str]] = Field(default_factory=list)
    trace: AssessmentProjectionTrace = Field(default_factory=AssessmentProjectionTrace)


class CurrentControlValidation(BaseModel):
    schema_violations: list[str] = Field(default_factory=list)
    component_violations: list[str] = Field(default_factory=list)
    methodology_violations: list[str] = Field(default_factory=list)
    value_violations: list[str] = Field(default_factory=list)


class FinalAttestationValidation(BaseModel):
    schema_violations: list[str] = Field(default_factory=list)
    label_violations: list[str] = Field(default_factory=list)
    methodology_violations: list[str] = Field(default_factory=list)
    value_violations: list[str] = Field(default_factory=list)


__all__ = [
    "AssessmentProjectionTrace",
    "AssessmentTablesResult",
    "CurrentControlValidation",
    "FinalAttestationValidation",
]
