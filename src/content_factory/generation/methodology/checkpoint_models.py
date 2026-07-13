"""Pydantic models for methodology human-approval checkpoints.

Leaf data contracts extracted from ``checkpoint``: the paused-artifact
``HumanApprovalCheckpoint`` and the strict requirement-matrix row
``RequirementMatrixItem``. Kept dependency-free (pydantic + stdlib) so both the
checkpoint policy and the requirement-matrix/summary helpers can import them without a
cycle. ``checkpoint`` re-imports both (``HumanApprovalCheckpoint`` is also re-exported
from the package ``__init__``).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class HumanApprovalCheckpoint(BaseModel):
    """A paused artifact that must be approved before the next flow node."""

    id: str
    stage: str
    node_id: str
    title: str
    summary: str
    resume_from_node: str
    allowed_targets: list[str] = Field(default_factory=list)
    artifact: dict[str, Any] = Field(default_factory=dict)
    artifact_hash: str = ""


class RequirementMatrixItem(BaseModel):
    """Strict UI contract for methodology requirement matrix rows."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=120)
    status: Literal["pass", "fail"]
    passed: bool
    evidence: str = Field(min_length=1, max_length=500)
