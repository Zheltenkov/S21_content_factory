"""Application service for durable generation workflow state."""

from __future__ import annotations

from typing import Any

from api.db.generation_workflow_db import (
    create_generation_workflow,
    get_generation_workflow,
    mark_interrupted_generation_workflows,
    record_generation_workflow_checkpoint,
    transition_generation_workflow,
)
from api.db.paused_generation_codec import hydrate_context
from content_gen.workflow.flow_runner import FlowExecutionStep, load_flow_definition
from content_gen.workflow_state import WorkflowCommand


_SECTION_TO_NODE = {
    "context": "context",
    "init": "context",
    "initialization": "context",
    "initial_context": "context",
    "context_phase": "context",
    "planning": "task_planning",
    "task_planning": "task_planning",
    "title": "title_annotation",
    "annotation": "title_annotation",
    "structure": "skeleton",
    "skeleton": "skeleton",
    "intro": "skeleton",
    "theory": "theory",
    "chapter2": "theory",
    "practice": "practice",
    "chapter3": "practice",
    "dataset": "practice",
    "quality": "global_quality",
    "evaluation": "evaluation",
    "final": "evaluation",
}


def _normalize_workflow_node_id(value: str | None) -> str | None:
    """Map legacy UI/checkpoint ids to the current AgentFlow node ids."""
    node_id = str(value or "").strip()
    if not node_id:
        return None
    return _SECTION_TO_NODE.get(node_id.lower(), node_id)


class GenerationWorkflowService:
    """Thin orchestration-facing facade over workflow state persistence."""

    def create(self, *, request_id: str, user_id: str, seed_metadata: dict[str, Any] | None = None) -> None:
        """Register a new generation workflow before the background task starts."""
        create_generation_workflow(request_id=request_id, user_id=user_id, metadata=seed_metadata or {})

    def mark_running(
        self,
        *,
        request_id: str,
        user_id: str,
        current_node: str | None = None,
        total_nodes: int | None = None,
    ) -> None:
        """Expose active execution as the durable progress source."""
        transition_generation_workflow(
            request_id=request_id,
            user_id=user_id,
            status="running",
            current_node=current_node,
            progress_total=total_nodes,
            error=None,
        )

    def mark_node_running(
        self,
        *,
        request_id: str,
        user_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Update current node and progress before a node starts."""
        transition_generation_workflow(
            request_id=request_id,
            user_id=user_id,
            status="running",
            current_node=_normalize_workflow_node_id(str(payload.get("node_id") or "") or None),
            progress_current=int(payload["checkpoint_index"]) - 1 if payload.get("checkpoint_index") else None,
            progress_total=int(payload["total_nodes"]) if payload.get("total_nodes") else None,
            metadata={"current_node_name": payload.get("node_name")},
            error=None,
        )

    def mark_resuming(self, *, request_id: str, user_id: str, comment: str | None = None) -> None:
        """Record a resume command before execution continues from a methodology pause."""
        self.submit_command(
            request_id=request_id,
            user_id=user_id,
            command="resume",
            payload={"comment": comment} if comment else {},
            status="resuming",
        )

    def mark_needs_review(
        self,
        *,
        request_id: str,
        user_id: str,
        resume_from_node: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record a true workflow interrupt for methodology review."""
        transition_generation_workflow(
            request_id=request_id,
            user_id=user_id,
            status="needs_review",
            current_node=None,
            resume_from_node=_normalize_workflow_node_id(resume_from_node),
            metadata=metadata or {},
        )

    def mark_completed(self, *, request_id: str, user_id: str) -> None:
        """Finalize workflow as completed."""
        transition_generation_workflow(
            request_id=request_id,
            user_id=user_id,
            status="completed",
            current_node=None,
            error=None,
        )

    def mark_failed(self, *, request_id: str, user_id: str, error: str) -> None:
        """Finalize workflow as failed."""
        transition_generation_workflow(
            request_id=request_id,
            user_id=user_id,
            status="failed",
            current_node=None,
            error=error,
        )

    def mark_cancelled(self, *, request_id: str, user_id: str) -> None:
        """Finalize workflow as cancelled by a command."""
        self.submit_command(request_id=request_id, user_id=user_id, command="cancel", status="cancelled")

    def mark_retry_node(
        self,
        *,
        request_id: str,
        user_id: str,
        node_id: str,
        reason: str | None = None,
    ) -> None:
        """Record a node-level retry command."""
        normalized_node_id = _normalize_workflow_node_id(node_id) or node_id
        self.submit_command(
            request_id=request_id,
            user_id=user_id,
            command="retry_node",
            node_id=normalized_node_id,
            payload={"reason": reason} if reason else {},
            status="resuming",
            resume_from_node=normalized_node_id,
        )

    def mark_regenerate_section(
        self,
        *,
        request_id: str,
        user_id: str,
        section: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Record a section regeneration command mapped onto a workflow node."""
        node_id = _normalize_workflow_node_id(section) or section
        command_payload = dict(payload or {})
        command_payload["section"] = section
        self.submit_command(
            request_id=request_id,
            user_id=user_id,
            command="regenerate_section",
            node_id=node_id,
            payload=command_payload,
            status="resuming",
            resume_from_node=node_id,
        )

    def record_methodology_assistant_command(
        self,
        *,
        request_id: str,
        user_id: str,
        command_payload: dict[str, Any],
        status: str = "needs_review",
    ) -> None:
        """Append a parsed methodologist chat command to durable workflow history."""
        command_name = str(command_payload.get("command") or "request_changes")
        node_id = str(
            command_payload.get("workflow_node_id")
            or command_payload.get("node_id")
            or command_payload.get("target_stage")
            or ""
        ) or None
        node_id = _normalize_workflow_node_id(node_id)
        self.submit_command(
            request_id=request_id,
            user_id=user_id,
            command=command_name,
            node_id=node_id,
            payload={
                "source": "methodology_assistant",
                "checkpoint_id": command_payload.get("checkpoint_id"),
                "checkpoint_stage": command_payload.get("checkpoint_stage"),
                "target_id": command_payload.get("target_id"),
                "target_stage": command_payload.get("target_stage"),
                "target_selector": command_payload.get("target_selector"),
                "assistant_command": command_payload,
            },
            status=status,
            resume_from_node=node_id if status == "resuming" else None,
        )

    def submit_command(
        self,
        *,
        request_id: str,
        user_id: str,
        command: str,
        node_id: str | None = None,
        payload: dict[str, Any] | None = None,
        status: str | None = None,
        resume_from_node: str | None = None,
    ) -> None:
        """Persist a workflow command as the single audit path for user actions."""
        normalized_node_id = _normalize_workflow_node_id(node_id)
        normalized_resume_from_node = _normalize_workflow_node_id(resume_from_node)
        workflow_command = WorkflowCommand(
            command=command,
            node_id=normalized_node_id,
            payload=payload or {},
            issued_by=user_id,
        )
        transition_generation_workflow(
            request_id=request_id,
            user_id=user_id,
            status=status or "resuming",
            current_node=None if command == "cancel" else None,
            resume_from_node=normalized_resume_from_node,
            command=workflow_command.model_dump(mode="json"),
        )

    def record_node_checkpoint(
        self,
        *,
        request_id: str,
        user_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Persist a node checkpoint emitted by AgentFlowRunner."""
        node_id = _normalize_workflow_node_id(str(payload.get("node_id") or "") or None) or ""
        checkpoint = record_generation_workflow_checkpoint(
            request_id=request_id,
            user_id=user_id,
            node_id=node_id,
            node_name=str(payload.get("node_name") or node_id),
            status=str(payload.get("status") or "success"),
            input_hash=str(payload.get("input_hash") or ""),
            output_artifact=payload.get("output_artifact") if isinstance(payload.get("output_artifact"), dict) else {},
            validation_result=payload.get("validation") if isinstance(payload.get("validation"), dict) else {},
            context_snapshot=(
                payload.get("context_snapshot")
                if isinstance(payload.get("context_snapshot"), dict)
                else {}
            ),
            retry_count=int(payload.get("retry_count") or 0),
            duration_ms=float(payload["duration_ms"]) if payload.get("duration_ms") is not None else None,
            checkpoint_index=int(payload["checkpoint_index"]) if payload.get("checkpoint_index") is not None else None,
        )
        node_status = str(payload.get("status") or "success")
        workflow_status = "node_completed"
        workflow_error = None
        if node_status == "paused":
            workflow_status = "needs_review"
        elif node_status == "cancelled":
            workflow_status = "cancelled"
        elif node_status == "error":
            workflow_status = "failed"
            validation = payload.get("validation") if isinstance(payload.get("validation"), dict) else {}
            issues = validation.get("issues") if isinstance(validation, dict) else None
            workflow_error = "; ".join(str(issue) for issue in issues or [] if str(issue)) or "Workflow node failed"
        transition_generation_workflow(
            request_id=request_id,
            user_id=user_id,
            status=workflow_status,
            current_node=None,
            last_completed_node=node_id or None,
            progress_current=checkpoint.get("checkpoint_index") if checkpoint else payload.get("checkpoint_index"),
            error=workflow_error,
            metadata={"last_checkpoint_status": payload.get("status")},
        )

    def get(self, request_id: str) -> dict[str, Any] | None:
        """Load durable workflow state for status API/UI."""
        return get_generation_workflow(request_id, include_checkpoints=True)

    def mark_interrupted_active_workflows(self) -> list[dict[str, Any]]:
        """Reconcile workflows that were active when the previous process died."""
        return mark_interrupted_generation_workflows()

    def build_recovery_session(
        self,
        *,
        request_id: str,
        command: str = "resume",
        node_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Build a restartable execution session from persisted workflow checkpoints."""
        workflow = self.get(request_id)
        if not workflow:
            return None
        checkpoints = list(workflow.get("checkpoints") or [])
        checkpoints.sort(key=lambda item: int(item.get("checkpoint_index") or 0))
        metadata = workflow.get("metadata") if isinstance(workflow.get("metadata"), dict) else {}
        resolved_node = self._resolve_command_node(command, node_id=node_id, payload=payload or {})

        if command in {"retry_node", "regenerate_section"} and resolved_node:
            return self._recovery_from_target_node(
                workflow=workflow,
                checkpoints=checkpoints,
                target_node=resolved_node,
                metadata=metadata,
                command=command,
                payload=payload or {},
            )
        return self._recovery_after_latest_checkpoint(
            workflow=workflow,
            checkpoints=checkpoints,
            metadata=metadata,
            command=command,
            payload=payload or {},
        )

    def _recovery_after_latest_checkpoint(
        self,
        *,
        workflow: dict[str, Any],
        checkpoints: list[dict[str, Any]],
        metadata: dict[str, Any],
        command: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        recoverable = [
            item
            for item in checkpoints
            if str(item.get("status") or "") in {"success", "skipped", "paused"}
            and isinstance(item.get("context_snapshot"), dict)
            and item.get("context_snapshot")
        ]
        if not recoverable:
            return self._initial_recovery_session(workflow, metadata, command=command, payload=payload)
        latest = recoverable[-1]
        start_index = int(latest.get("checkpoint_index") or 0)
        return {
            "request_id": workflow.get("request_id"),
            "user_id": workflow.get("user_id"),
            "command": command,
            "payload": payload,
            "context": hydrate_context(latest["context_snapshot"]),
            "previous_steps": self._steps_from_checkpoints(checkpoints[:start_index]),
            "start_index": start_index,
            "project_seed": metadata.get("project_seed_payload") or {},
            "track_paths": metadata.get("track_paths") or [],
        }

    def _recovery_from_target_node(
        self,
        *,
        workflow: dict[str, Any],
        checkpoints: list[dict[str, Any]],
        target_node: str,
        metadata: dict[str, Any],
        command: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        target_node = _normalize_workflow_node_id(target_node) or target_node
        plan = self._execution_plan()
        if target_node not in plan:
            return self._initial_recovery_session(workflow, metadata, command=command, payload=payload)
        target_start_index = plan.index(target_node)
        target_checkpoint = next(
            (
                item
                for item in checkpoints
                if _normalize_workflow_node_id(str(item.get("node_id") or "")) == target_node
            ),
            None,
        )
        if target_checkpoint is not None:
            target_start_index = max(0, int(target_checkpoint.get("checkpoint_index") or 1) - 1)
        previous = [
            item
            for item in checkpoints
            if int(item.get("checkpoint_index") or 0) <= target_start_index
            and str(item.get("status") or "") in {"success", "skipped"}
        ]
        context_source = previous[-1] if previous else None
        has_context_snapshot = (
            context_source
            and isinstance(context_source.get("context_snapshot"), dict)
            and context_source["context_snapshot"]
        )
        if has_context_snapshot:
            context = hydrate_context(context_source["context_snapshot"])
            previous_steps = self._steps_from_checkpoints(previous)
            return {
                "request_id": workflow.get("request_id"),
                "user_id": workflow.get("user_id"),
                "command": command,
                "payload": payload,
                "target_node": target_node,
                "context": context,
                "previous_steps": previous_steps,
                "start_index": target_start_index,
                "project_seed": metadata.get("project_seed_payload") or {},
                "track_paths": metadata.get("track_paths") or [],
            }
        return self._initial_recovery_session(
            workflow,
            metadata,
            command=command,
            payload=payload,
            start_index=0,
            target_node=target_node,
        )

    @staticmethod
    def _initial_recovery_session(
        workflow: dict[str, Any],
        metadata: dict[str, Any],
        *,
        command: str,
        payload: dict[str, Any],
        start_index: int = 0,
        target_node: str | None = None,
    ) -> dict[str, Any]:
        return {
            "request_id": workflow.get("request_id"),
            "user_id": workflow.get("user_id"),
            "command": command,
            "payload": payload,
            "target_node": target_node,
            "raw_input": metadata.get("project_seed_payload") or {},
            "track_paths": metadata.get("track_paths") or [],
            "previous_steps": [],
            "start_index": start_index,
            "project_seed": metadata.get("project_seed_payload") or {},
        }

    @staticmethod
    def _steps_from_checkpoints(checkpoints: list[dict[str, Any]]) -> list[FlowExecutionStep]:
        steps: list[FlowExecutionStep] = []
        for item in checkpoints:
            validation = item.get("validation_result") if isinstance(item.get("validation_result"), dict) else {}
            issues = validation.get("issues") if isinstance(validation, dict) else []
            steps.append(
                FlowExecutionStep(
                    node_id=_normalize_workflow_node_id(str(item.get("node_id") or "")) or "",
                    node_name=str(item.get("node_name") or item.get("node_id") or ""),
                    status=str(item.get("status") or "success"),
                    duration_ms=float(item.get("duration_ms") or 0.0),
                    issues=[str(issue) for issue in issues or []],
                )
            )
        return steps

    @staticmethod
    def _execution_plan() -> list[str]:
        flow = load_flow_definition("content_generation")
        return [node.id for node in flow.nodes]

    @staticmethod
    def _resolve_command_node(command: str, *, node_id: str | None, payload: dict[str, Any]) -> str | None:
        if node_id:
            return _normalize_workflow_node_id(node_id)
        if command == "regenerate_section":
            section = payload.get("section") or payload.get("target") or payload.get("target_id")
            return _normalize_workflow_node_id(str(section or "") or None)
        return None
