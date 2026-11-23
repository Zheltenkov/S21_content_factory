"""Typed contracts shared by competency agents, orchestration, quality and evaluation."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ProgramType(StrEnum):
    DPO_PK = "dpo_pk"
    DPO_RETRAINING = "dpo_retraining"
    ADDITIONAL_GENERAL = "additional_general"
    UNIVERSITY = "university"


CompetencyFamily = Literal["ПК", "ОК", "УК", "ОПК"]
CompetencySourceType = Literal[
    "professional_standard",
    "qualification_requirement",
    "professional_activity_analysis",
    "general_education_outcomes",
    "university_standard",
]


class ProgramCompetencyPolicy(BaseModel):
    """Deterministic routing policy selected by the concrete program/template profile."""

    program_type: ProgramType
    family: CompetencyFamily
    professional_standard_applicable: bool
    max_competencies: int = 8


class CompetencyAssessmentEvidence(BaseModel):
    """One assessment option derived from an actual UP row, not invented by the model."""

    assessment_id: str
    title: str
    method: str


class SourceCompetencyItem(BaseModel):
    """One auditable source skill and the curriculum evidence attached to it."""

    source_id: str
    name: str
    modules: list[str] = Field(default_factory=list)
    outcomes: list[str] = Field(default_factory=list)
    assessments: list[CompetencyAssessmentEvidence] = Field(default_factory=list)


class CompetencyEvidence(BaseModel):
    """Allowed evidence reference; LLM output may only point to one of these identifiers."""

    evidence_id: str
    source: Literal["up", "professional_standard"]
    text: str


class ProfessionalStandardSelection(BaseModel):
    """Explicit HITL approval; matcher suggestions alone never become normative evidence."""

    approved_codes: list[str] = Field(default_factory=list)
    approved_by: str = ""
    approved_at: str = ""

    @field_validator("approved_codes", mode="before")
    @classmethod
    def normalize_codes(cls, value: object) -> list[str]:
        if not isinstance(value, (list, tuple)):
            return []
        return list(dict.fromkeys(" ".join(str(item or "").split()) for item in value if str(item or "").strip()))


class ProfessionalStandardMatch(BaseModel):
    """One model-ranked catalog candidate, still requiring methodologist approval."""

    standard_code: str
    relevant_function_codes: list[str] = Field(default_factory=list)
    reason: str
    confidence: float = Field(ge=0, le=1)


class ProfessionalStandardMatchResponse(BaseModel):
    matches: list[ProfessionalStandardMatch] = Field(default_factory=list, max_length=5)


class CompetencyDraft(BaseModel):
    """Semantic draft without a local code or free-form normative source claims."""

    title: str
    baseline: str = ""
    qualitative_change: str = ""
    after_training: str
    assessment: str = ""
    assessment_refs: list[str] = Field(default_factory=list)
    covered_source_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class CompetencyDesignResponse(BaseModel):
    competencies: list[CompetencyDraft] = Field(default_factory=list, max_length=8)


class CompetencyCritique(BaseModel):
    """Critic result complements deterministic validation but cannot override it."""

    valid: bool
    issues: list[str] = Field(default_factory=list)
    uncovered_source_ids: list[str] = Field(default_factory=list)
    unsupported_evidence_refs: list[str] = Field(default_factory=list)
    duplicate_pairs: list[str] = Field(default_factory=list)


class CompetencyPipelineTrace(BaseModel):
    """Persisted lineage and observability for one competency generation run."""

    program_type: ProgramType = ProgramType.DPO_PK
    family: CompetencyFamily = "ПК"
    status: Literal["not_run", "fallback", "validated", "repaired"] = "not_run"
    source_item_ids: list[str] = Field(default_factory=list)
    covered_source_ids: list[str] = Field(default_factory=list)
    catalog_sha256: str = ""
    catalog_load_error: str = ""
    candidate_standard_codes: list[str] = Field(default_factory=list)
    matches: list[ProfessionalStandardMatch] = Field(default_factory=list)
    approved_standard_codes: list[str] = Field(default_factory=list)
    validation_issues: list[str] = Field(default_factory=list)
    fallback_reason: str = ""

    @property
    def source_coverage_rate(self) -> float | None:
        if not self.source_item_ids:
            return None
        covered = set(self.covered_source_ids) & set(self.source_item_ids)
        return len(covered) / len(set(self.source_item_ids))


class CompetencyTableResult(BaseModel):
    header: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    trace: CompetencyPipelineTrace = Field(default_factory=CompetencyPipelineTrace)


class CompetencyTableValidation(BaseModel):
    schema_violations: list[str] = Field(default_factory=list)
    missing_codes: list[str] = Field(default_factory=list)
    invalid_codes: list[str] = Field(default_factory=list)
    duplicate_codes: list[str] = Field(default_factory=list)
    missing_descriptions: list[str] = Field(default_factory=list)
    missing_semantic_fields: list[str] = Field(default_factory=list)
    basis_review_required: list[str] = Field(default_factory=list)


def policy_for_program_type(value: str) -> ProgramCompetencyPolicy:
    """Map template-owned program type labels to stable competency families."""
    normalized = value.casefold().replace("ё", "е")
    if "переподготов" in normalized:
        return ProgramCompetencyPolicy(
            program_type=ProgramType.DPO_RETRAINING,
            family="ПК",
            professional_standard_applicable=True,
        )
    if "общеразв" in normalized or "общеобраз" in normalized:
        return ProgramCompetencyPolicy(
            program_type=ProgramType.ADDITIONAL_GENERAL,
            family="ОК",
            professional_standard_applicable=False,
        )
    if "высш" in normalized or "вуз" in normalized or "универс" in normalized:
        return ProgramCompetencyPolicy(
            program_type=ProgramType.UNIVERSITY,
            family="ПК",
            professional_standard_applicable=True,
        )
    return ProgramCompetencyPolicy(
        program_type=ProgramType.DPO_PK,
        family="ПК",
        professional_standard_applicable=True,
    )


__all__ = [
    "CompetencyAssessmentEvidence",
    "CompetencyCritique",
    "CompetencyDesignResponse",
    "CompetencyDraft",
    "CompetencyEvidence",
    "CompetencyFamily",
    "CompetencyPipelineTrace",
    "CompetencySourceType",
    "CompetencyTableResult",
    "CompetencyTableValidation",
    "ProfessionalStandardMatch",
    "ProfessionalStandardMatchResponse",
    "ProfessionalStandardSelection",
    "ProgramCompetencyPolicy",
    "ProgramType",
    "SourceCompetencyItem",
    "policy_for_program_type",
]
