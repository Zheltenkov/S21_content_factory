"""Explicit state machine for human methodology review."""

from __future__ import annotations

from enum import StrEnum


class MethodologyRuntimeState(StrEnum):
    """Durable states for methodology-aware generation."""

    RUNNING = "running"
    NEEDS_REVIEW = "needs_review"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"
    RESUMING = "resuming"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MethodologyRuntimeAction(StrEnum):
    """Events that move methodology runtime between states."""

    PAUSE_FOR_REVIEW = "pause_for_review"
    REQUEST_CHANGES = "request_changes"
    APPROVE_DIFF = "approve_diff"
    APPROVE_REVIEW = "approve_review"
    START_RESUME = "start_resume"
    COMPLETE = "complete"
    FAIL = "fail"
    CANCEL = "cancel"


class MethodologyStateTransitionError(ValueError):
    """Raised when a methodology state transition is not allowed."""


class MethodologyStateMachine:
    """Small deterministic state machine for methodology review lifecycle."""

    _TRANSITIONS: dict[
        MethodologyRuntimeState,
        dict[MethodologyRuntimeAction, MethodologyRuntimeState],
    ] = {
        MethodologyRuntimeState.RUNNING: {
            MethodologyRuntimeAction.PAUSE_FOR_REVIEW: MethodologyRuntimeState.NEEDS_REVIEW,
            MethodologyRuntimeAction.COMPLETE: MethodologyRuntimeState.COMPLETED,
            MethodologyRuntimeAction.FAIL: MethodologyRuntimeState.FAILED,
            MethodologyRuntimeAction.CANCEL: MethodologyRuntimeState.CANCELLED,
        },
        MethodologyRuntimeState.NEEDS_REVIEW: {
            MethodologyRuntimeAction.REQUEST_CHANGES: MethodologyRuntimeState.CHANGES_REQUESTED,
            MethodologyRuntimeAction.APPROVE_REVIEW: MethodologyRuntimeState.APPROVED,
            MethodologyRuntimeAction.START_RESUME: MethodologyRuntimeState.RESUMING,
            MethodologyRuntimeAction.FAIL: MethodologyRuntimeState.FAILED,
            MethodologyRuntimeAction.CANCEL: MethodologyRuntimeState.CANCELLED,
        },
        MethodologyRuntimeState.CHANGES_REQUESTED: {
            MethodologyRuntimeAction.REQUEST_CHANGES: MethodologyRuntimeState.CHANGES_REQUESTED,
            MethodologyRuntimeAction.APPROVE_DIFF: MethodologyRuntimeState.NEEDS_REVIEW,
            MethodologyRuntimeAction.FAIL: MethodologyRuntimeState.FAILED,
            MethodologyRuntimeAction.CANCEL: MethodologyRuntimeState.CANCELLED,
        },
        MethodologyRuntimeState.APPROVED: {
            MethodologyRuntimeAction.START_RESUME: MethodologyRuntimeState.RESUMING,
            MethodologyRuntimeAction.FAIL: MethodologyRuntimeState.FAILED,
            MethodologyRuntimeAction.CANCEL: MethodologyRuntimeState.CANCELLED,
        },
        MethodologyRuntimeState.RESUMING: {
            MethodologyRuntimeAction.PAUSE_FOR_REVIEW: MethodologyRuntimeState.NEEDS_REVIEW,
            MethodologyRuntimeAction.COMPLETE: MethodologyRuntimeState.COMPLETED,
            MethodologyRuntimeAction.FAIL: MethodologyRuntimeState.FAILED,
            MethodologyRuntimeAction.CANCEL: MethodologyRuntimeState.CANCELLED,
        },
        MethodologyRuntimeState.COMPLETED: {},
        MethodologyRuntimeState.FAILED: {},
        MethodologyRuntimeState.CANCELLED: {},
    }

    def transition(
        self,
        state: MethodologyRuntimeState | str,
        action: MethodologyRuntimeAction | str,
    ) -> MethodologyRuntimeState:
        """Return the next state or raise a deterministic transition error."""
        current = MethodologyRuntimeState(state)
        event = MethodologyRuntimeAction(action)
        next_state = self._TRANSITIONS.get(current, {}).get(event)
        if next_state is None:
            raise MethodologyStateTransitionError(
                f"Methodology transition is not allowed: {current.value} -> {event.value}"
            )
        return next_state

    def can_transition(
        self,
        state: MethodologyRuntimeState | str,
        action: MethodologyRuntimeAction | str,
    ) -> bool:
        """Return whether the transition is allowed."""
        try:
            self.transition(state, action)
        except (MethodologyStateTransitionError, ValueError):
            return False
        return True
