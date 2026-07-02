"""Schema-first contracts for README regeneration.

This module keeps the deterministic regeneration pipeline separate from the
LLM agent: selected sections, user instructions, typed patch validation,
patch application and validation reporting are explicit contracts.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .utils.patch_format import Patch, PatchResult, apply_patches
from .utils.regeneration_scope import (
    RegenerationChangeIntent,
    RegenerationEditScope,
    detect_regeneration_change_intent,
    parse_regeneration_edit_scopes,
)


SCHEMA_VERSION = "regeneration.pipeline.v1"
_CHANGES_JSON_RE = re.compile(r'\{[\s\S]*"changes"[\s\S]*\}')


class RegenerationInstruction(BaseModel):
    """User instruction attached to one selected README section."""

    model_config = ConfigDict(extra="forbid")

    change: str = ""
    keep: str = ""
    raw_block: str = ""


class SelectedRegenerationSection(BaseModel):
    """A line-bounded README section that may be changed."""

    model_config = ConfigDict(extra="forbid")

    title: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    instruction: RegenerationInstruction = Field(default_factory=RegenerationInstruction)
    source: str = "explicit"
    is_history: bool = False

    @model_validator(mode="after")
    def _validate_line_range(self) -> "SelectedRegenerationSection":
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        return self

    @classmethod
    def from_scope(cls, scope: RegenerationEditScope) -> "SelectedRegenerationSection":
        return cls(
            title=scope.title,
            start_line=scope.start_line,
            end_line=scope.end_line,
            instruction=RegenerationInstruction(
                change=scope.change,
                keep=scope.keep,
                raw_block=scope.raw_block,
            ),
            source=scope.source,
            is_history=scope.is_history,
        )

    def to_scope(self) -> RegenerationEditScope:
        return RegenerationEditScope(
            title=self.title,
            start_line=self.start_line,
            end_line=self.end_line,
            change=self.instruction.change,
            keep=self.instruction.keep,
            raw_block=self.instruction.raw_block,
            source=self.source,
            is_history=self.is_history,
        )

    def as_line_range(self) -> tuple[int, int, str]:
        return (self.start_line, self.end_line, self.title)


class RegenerationPipelineInput(BaseModel):
    """Typed input contract for one regeneration command."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    original_md: str
    comments: str
    language: str = "ru"
    change_intent: RegenerationChangeIntent = "local_section_edit"
    selected_sections: list[SelectedRegenerationSection] = Field(default_factory=list)

    @property
    def is_scoped(self) -> bool:
        return bool(self.selected_sections) and self.change_intent == "local_section_edit"

    @property
    def is_structural(self) -> bool:
        return self.change_intent == "structural_document_edit"

    def scopes(self) -> list[RegenerationEditScope]:
        return [section.to_scope() for section in self.selected_sections]

    def allowed_line_ranges(self) -> list[tuple[int, int, str]]:
        return [section.as_line_range() for section in self.selected_sections]


class TypedRegenerationPatch(BaseModel):
    """One deterministic text replacement returned by the LLM."""

    model_config = ConfigDict(extra="forbid")

    location_hint: str = ""
    old_text: str = Field(min_length=1)
    new_text: str = ""

    def to_patch(self) -> Patch:
        return Patch(
            location_hint=self.location_hint,
            old_text=self.old_text,
            new_text=self.new_text,
        )


class TypedRegenerationPatchSet(BaseModel):
    """Strict schema expected from the LLM for patch-based regeneration."""

    model_config = ConfigDict(extra="forbid")

    changes: list[TypedRegenerationPatch] = Field(default_factory=list)

    def to_patches(self) -> list[Patch]:
        return [change.to_patch() for change in self.changes]


class RegenerationValidationIssue(BaseModel):
    """One item in the regeneration validation report."""

    model_config = ConfigDict(extra="forbid")

    severity: Literal["info", "warning", "error"]
    code: str
    message: str
    section_title: str | None = None
    patch_location: str | None = None


class RegenerationValidationReport(BaseModel):
    """Deterministic report for patch parsing, application and fallback use."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    change_intent: RegenerationChangeIntent = "local_section_edit"
    scoped: bool = False
    selected_sections: list[SelectedRegenerationSection] = Field(default_factory=list)
    requested_patch_count: int = 0
    applied_patch_count: int = 0
    failed_patch_count: int = 0
    apply_mode: Literal["typed_patch", "scoped_rewrite_fallback", "full_rewrite_fallback", "none"] = "none"
    deterministic_apply: bool = True
    changed: bool = False
    issues: list[RegenerationValidationIssue] = Field(default_factory=list)

    @classmethod
    def from_input(cls, pipeline_input: RegenerationPipelineInput) -> "RegenerationValidationReport":
        return cls(
            change_intent=pipeline_input.change_intent,
            scoped=pipeline_input.is_scoped,
            selected_sections=pipeline_input.selected_sections,
        )

    def add_issue(
        self,
        *,
        severity: Literal["info", "warning", "error"],
        code: str,
        message: str,
        section_title: str | None = None,
        patch_location: str | None = None,
    ) -> None:
        self.issues.append(
            RegenerationValidationIssue(
                severity=severity,
                code=code,
                message=message,
                section_title=section_title,
                patch_location=patch_location,
            )
        )

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def build_regeneration_pipeline_input(
    *,
    original_md: str,
    comments: str,
    language: str = "ru",
) -> RegenerationPipelineInput:
    """Build a typed regeneration command from UI comments and README markdown."""
    scopes = parse_regeneration_edit_scopes(comments, original_md)
    change_intent = detect_regeneration_change_intent(comments, original_md, scopes)
    return RegenerationPipelineInput(
        original_md=original_md,
        comments=comments,
        language=language,
        change_intent=change_intent,
        selected_sections=[SelectedRegenerationSection.from_scope(scope) for scope in scopes],
    )


def render_patch_response_schema() -> str:
    """Render the JSON schema that the LLM must satisfy."""
    return json.dumps(TypedRegenerationPatchSet.model_json_schema(), ensure_ascii=False, indent=2)


def parse_typed_patch_set(response: str) -> tuple[TypedRegenerationPatchSet | None, list[RegenerationValidationIssue]]:
    """Parse the LLM response through the strict patch schema."""
    json_match = _CHANGES_JSON_RE.search(response or "")
    if not json_match:
        return (
            None,
            [
                RegenerationValidationIssue(
                    severity="warning",
                    code="patch_json_not_found",
                    message="LLM response did not contain a valid changes JSON object.",
                )
            ],
        )

    try:
        payload = json.loads(json_match.group(0))
    except json.JSONDecodeError as exc:
        return (
            None,
            [
                RegenerationValidationIssue(
                    severity="warning",
                    code="patch_json_invalid",
                    message=f"LLM response contained changes JSON but it could not be decoded: {exc}",
                )
            ],
        )

    try:
        return TypedRegenerationPatchSet.model_validate(payload), []
    except ValidationError as exc:
        return (
            None,
            [
                RegenerationValidationIssue(
                    severity="error",
                    code="patch_schema_invalid",
                    message=f"Patch JSON does not match regeneration schema: {exc.errors()}",
                )
            ],
        )


def apply_typed_patch_set(
    *,
    markdown: str,
    patch_set: TypedRegenerationPatchSet,
    pipeline_input: RegenerationPipelineInput,
    report: RegenerationValidationReport,
) -> PatchResult:
    """Apply typed patches using deterministic text replacement only."""
    report.requested_patch_count = len(patch_set.changes)
    report.apply_mode = "typed_patch"

    patch_result = apply_patches(
        markdown,
        patch_set.to_patches(),
        allowed_line_ranges=pipeline_input.allowed_line_ranges() if pipeline_input.is_scoped else None,
    )
    report.applied_patch_count = len(patch_result.applied_patches)
    report.failed_patch_count = len(patch_result.failed_patches)
    report.changed = patch_result.result_md.strip() != markdown.strip()

    for error, patch in zip(patch_result.errors, patch_result.failed_patches, strict=False):
        report.add_issue(
            severity="warning",
            code="patch_not_applied",
            message=error,
            patch_location=patch.location_hint,
        )

    if not patch_result.applied_patches and patch_set.changes:
        report.add_issue(
            severity="warning",
            code="no_patches_applied",
            message="No typed patches were applied to the README.",
        )

    return patch_result
