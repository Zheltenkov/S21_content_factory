"""Versioned evaluation contracts. A missing check is never a positive mark."""
from typing import Literal
from uuid import uuid4

from pydantic import ConfigDict, Field

from ..content.models import now
from ..core.contracts import STORED_SCHEMA, Contract

Mark = Literal[-1, 0, 1]
State = Literal["pending", "assessed", "not_applicable", "no_data", "unverified", "error"]


class Criterion(Contract):
    number: int = Field(ge=1, le=56)
    section: str
    title: str
    fulfilled: str
    partial: str
    failed: str
    method: Literal["model", "code", "execution"] = "model"
    requires_confirmation: str = ""
    blocker: bool = False


class Profile(Contract):
    version: str
    source: str
    source_sha256: str
    experimental: bool = True
    ready_threshold: float = 85
    revision_threshold: float = 70
    criteria: list[Criterion]


class Unit(Contract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    id: str
    title: str
    text: str
    kind: str
    part: str = ""


class FileRecord(Contract):
    path: str
    present: bool
    sha256: str = ""
    role: str = "issued"
    # Presence comes from ZIP membership; an empty template is still a supplied file.
    empty: bool = False
    required: bool | None = None
    tasks: list[int] = Field(default_factory=list)
    reference_units: list[str] = Field(default_factory=list)


class Context(Contract):
    units: list[Unit] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    curriculum_available: bool = False
    full_sources: bool = False


class Snapshot(Contract):
    digest: str
    content_digest: str
    title: str
    units: list[Unit]
    files: list[FileRecord]
    approved_files: list[str] | None = None
    outcomes: list[str]
    task_numbers: list[int]
    optional_tasks: list[int]
    format: str
    has_story: bool
    has_theory: bool
    curriculum_available: bool
    limitations: list[str] = Field(default_factory=list)


class Evidence(Contract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    unit: str = Field(min_length=1, max_length=300)
    quote: str = Field(min_length=8, max_length=1800)


class Verdict(Contract):
    criterion: int = Field(ge=1, le=56)
    state: State = "pending"
    mark: Mark | None = None
    reason: str = Field(default="", max_length=5000)
    evidence: list[Evidence] = Field(default_factory=list, max_length=12)
    suggestion: str = Field(default="", max_length=3000)
    method: str = ""
    confirmed_blocker: bool = False
    evidence_valid: bool = True
    dependency: str = ""
    reused: bool = False


class OutcomeMapping(Contract):
    outcome: int = Field(ge=1)
    status: Literal["confirmed", "gap", "uncertain"]
    tasks: list[int] = Field(default_factory=list)
    action: list[Evidence] = Field(default_factory=list)
    result: list[Evidence] = Field(default_factory=list)
    peer: list[Evidence] = Field(default_factory=list)
    reason: str = Field(max_length=3000)


class ModelVerdict(Contract):
    criterion: int = Field(ge=1, le=56)
    state: Literal["assessed", "no_data", "unverified"]
    mark: Mark | None = None
    reason: str = Field(min_length=1, max_length=5000)
    evidence: list[Evidence] = Field(default_factory=list, max_length=12)
    suggestion: str = Field(default="", max_length=3000)
    # Criterion 20 blocks only for an error in the answer used to grade a learner.
    checked_answer_error: bool = False
    # Only files whose exposure to learners is essential to this judgment, not every cited file.
    delivery_claims: list[str] = Field(default_factory=list, max_length=20)


class EvidenceRequest(Contract):
    unit: str = Field(min_length=1, max_length=300)
    mode: Literal["lines", "search", "csv_records"] = "lines"
    start: int = Field(default=1, ge=1)
    end: int = Field(default=20, ge=1)
    query: str = Field(default="", max_length=200)


class EvaluationBatch(Contract):
    results: list[ModelVerdict] = Field(max_length=8)
    outcomes: list[OutcomeMapping] = Field(default_factory=list, max_length=80)
    requests: list[EvidenceRequest] = Field(default_factory=list, max_length=4)


class Evaluation(Contract):
    schema_version: int = STORED_SCHEMA
    id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    owner_id: str
    created_at: str = Field(default_factory=now)
    completed_at: str | None = None
    state: Literal["running", "complete", "partial", "interrupted"] = "running"
    profile: Profile
    profile_digest: str
    engine_version: str
    prompt_digest: str
    snapshot: Snapshot
    model: str = ""
    verdicts: list[Verdict]
    outcomes: list[OutcomeMapping] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    history_warnings: list[str] = Field(default_factory=list)
    calls: int = 0
    failed_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_ms: int = 0
    use_model: bool = False
