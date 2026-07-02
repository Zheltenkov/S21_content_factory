"""Typed durable workflow state for generation runs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def _utc_now() -> datetime:
    """Return UTC time as a naive datetime for persisted workflow snapshots."""
    return datetime.now(UTC).replace(tzinfo=None)


GenerationWorkflowStatus = Literal[
    "created",
    "running",
    "node_completed",
    "needs_review",
    "resuming",
    "interrupted",
    "completed",
    "failed",
    "cancelled",
]
NodeCheckpointStatus = Literal["success", "skipped", "error", "paused", "cancelled"]
WorkflowCommandType = Literal[
    "cancel",
    "retry_node",
    "regenerate_section",
    "resume",
    "approve",
    "request_changes",
    "simplify_task",
    "add_example",
    "fix_failed_criteria",
]


class WorkflowValidationResult(BaseModel):
    """Validation summary stored with a node checkpoint."""

    model_config = ConfigDict(extra="forbid")

    status: str = "not_run"
    issues_count: int = 0
    issues: list[str] = Field(default_factory=list)


class GenerationNodeCheckpoint(BaseModel):
    """Durable artifact emitted after one workflow node attempt."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    node_id: str
    node_name: str
    checkpoint_index: int
    input_hash: str
    output_artifact: dict[str, Any] = Field(default_factory=dict)
    context_snapshot: dict[str, Any] = Field(default_factory=dict)
    status: NodeCheckpointStatus
    retry_count: int = 0
    validation: WorkflowValidationResult = Field(default_factory=WorkflowValidationResult)
    duration_ms: float | None = None
    created_at: datetime = Field(default_factory=_utc_now)


class WorkflowCommand(BaseModel):
    """User/system command that changes workflow execution state."""

    model_config = ConfigDict(extra="forbid")

    command: WorkflowCommandType
    node_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    issued_by: str | None = None
    issued_at: datetime = Field(default_factory=_utc_now)


class GenerationWorkflowSnapshot(BaseModel):
    """Serializable workflow state used by API/UI progress."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    user_id: str | None = None
    status: GenerationWorkflowStatus = "created"
    current_node: str | None = None
    last_completed_node: str | None = None
    resume_from_node: str | None = None
    progress_current: int = 0
    progress_total: int = 0
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    checkpoints: list[GenerationNodeCheckpoint] = Field(default_factory=list)
    commands: list[WorkflowCommand] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class GenerationStateMachine:
    """Small internal state machine for durable generation workflow snapshots."""

    _TERMINAL: set[str] = {"completed", "failed", "cancelled"}

    def __init__(self, snapshot: GenerationWorkflowSnapshot) -> None:
        self.snapshot = snapshot

    @classmethod
    def create(cls, request_id: str, user_id: str | None = None, **metadata: Any) -> "GenerationStateMachine":
        """Create a workflow in the initial durable state."""
        return cls(
            GenerationWorkflowSnapshot(
                request_id=request_id,
                user_id=user_id,
                status="created",
                metadata={key: value for key, value in metadata.items() if value is not None},
            )
        )

    def start(self, *, total_nodes: int | None = None, current_node: str | None = None) -> GenerationWorkflowSnapshot:
        """Move a created/resumed workflow into active execution."""
        self._ensure_not_terminal()
        self.snapshot.status = "running"
        self.snapshot.current_node = current_node or self.snapshot.current_node
        if total_nodes is not None:
            self.snapshot.progress_total = max(0, int(total_nodes))
        self._touch()
        return self.snapshot

    def node_started(self, node_id: str, *, progress_current: int | None = None) -> GenerationWorkflowSnapshot:
        """Record the node currently being executed."""
        self._ensure_not_terminal()
        self.snapshot.status = "running"
        self.snapshot.current_node = node_id
        if progress_current is not None:
            self.snapshot.progress_current = max(0, int(progress_current))
        self._touch()
        return self.snapshot

    def node_completed(self, checkpoint: GenerationNodeCheckpoint) -> GenerationWorkflowSnapshot:
        """Attach a node checkpoint and expose it as the latest completed unit."""
        self._ensure_not_terminal()
        self.snapshot.checkpoints.append(checkpoint)
        self.snapshot.status = "node_completed"
        self.snapshot.current_node = None
        self.snapshot.last_completed_node = checkpoint.node_id
        self.snapshot.progress_current = max(self.snapshot.progress_current, checkpoint.checkpoint_index)
        self._touch()
        return self.snapshot

    def pause(self, *, resume_from_node: str | None = None) -> GenerationWorkflowSnapshot:
        """Interrupt execution for methodology review."""
        self._ensure_not_terminal()
        self.snapshot.status = "needs_review"
        self.snapshot.resume_from_node = resume_from_node
        self.snapshot.current_node = None
        self._touch()
        return self.snapshot

    def resume(self, command: WorkflowCommand | None = None) -> GenerationWorkflowSnapshot:
        """Mark a paused workflow as resuming."""
        self._ensure_not_terminal()
        self.snapshot.status = "resuming"
        if command is not None:
            self.snapshot.commands.append(command)
        self._touch()
        return self.snapshot

    def submit_command(self, command: WorkflowCommand) -> GenerationWorkflowSnapshot:
        """Record a workflow command before an executor handles it."""
        self._ensure_not_terminal()
        self.snapshot.commands.append(command)
        if command.command == "cancel":
            self.snapshot.status = "cancelled"
            self.snapshot.current_node = None
        elif command.command == "resume":
            self.snapshot.status = "resuming"
        elif command.command in {"retry_node", "regenerate_section"}:
            self.snapshot.status = "resuming"
            self.snapshot.resume_from_node = command.node_id
        self._touch()
        return self.snapshot

    def complete(self) -> GenerationWorkflowSnapshot:
        """Finalize workflow as completed."""
        self.snapshot.status = "completed"
        self.snapshot.current_node = None
        self.snapshot.error = None
        if self.snapshot.progress_total:
            self.snapshot.progress_current = self.snapshot.progress_total
        self._touch()
        return self.snapshot

    def fail(self, error: str) -> GenerationWorkflowSnapshot:
        """Finalize workflow as failed with a UI-safe error message."""
        self.snapshot.status = "failed"
        self.snapshot.current_node = None
        self.snapshot.error = error
        self._touch()
        return self.snapshot

    def cancel(self, command: WorkflowCommand | None = None) -> GenerationWorkflowSnapshot:
        """Finalize workflow as cancelled by a node-level command."""
        self.snapshot.status = "cancelled"
        self.snapshot.current_node = None
        if command is not None:
            self.snapshot.commands.append(command)
        self._touch()
        return self.snapshot

    def _ensure_not_terminal(self) -> None:
        if self.snapshot.status in self._TERMINAL:
            raise ValueError(f"Workflow is already terminal: {self.snapshot.status}")

    def _touch(self) -> None:
        self.snapshot.updated_at = _utc_now()
