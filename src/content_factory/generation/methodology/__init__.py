"""Methodology review layer for generation stages."""

from .change_request import (
    ChangeRequestConflict,
    MethodologistChangeRequest,
    has_hard_conflicts,
    validate_methodologist_change_request,
)
from .assistant import (
    MethodologyAssistantCommand,
    MethodologyAssistantCommandParser,
    MethodologyAssistantParseContext,
)
from .checkpoint import HumanApprovalCheckpoint, HumanApprovalCheckpointPolicy, build_requirement_matrix
from .decision import MethodologyGateDecision, MethodologyGateInterrupt, MethodologyGatePolicy
from .gate import MethodologyGate
from .models import StageRepairResult, StageReviewIssue, StageReviewResult
from .scoped_revision import ScopedResumePlan, ScopedRevisionExecutor, ScopedRevisionResult
from .state_machine import (
    MethodologyRuntimeAction,
    MethodologyRuntimeState,
    MethodologyStateMachine,
    MethodologyStateTransitionError,
)
from .target_registry import SectionTarget, SectionTargetRegistry, build_section_target_registry
from .trace import MethodologyTraceRecorder

__all__ = [
    "ChangeRequestConflict",
    "HumanApprovalCheckpoint",
    "HumanApprovalCheckpointPolicy",
    "MethodologyGate",
    "MethodologyGateDecision",
    "MethodologyGateInterrupt",
    "MethodologyGatePolicy",
    "MethodologistChangeRequest",
    "MethodologyAssistantCommand",
    "MethodologyAssistantCommandParser",
    "MethodologyAssistantParseContext",
    "MethodologyRuntimeAction",
    "MethodologyRuntimeState",
    "MethodologyStateMachine",
    "MethodologyStateTransitionError",
    "MethodologyTraceRecorder",
    "ScopedRevisionExecutor",
    "ScopedRevisionResult",
    "ScopedResumePlan",
    "SectionTarget",
    "SectionTargetRegistry",
    "StageRepairResult",
    "StageReviewIssue",
    "StageReviewResult",
    "build_section_target_registry",
    "build_requirement_matrix",
    "has_hard_conflicts",
    "validate_methodologist_change_request",
]
