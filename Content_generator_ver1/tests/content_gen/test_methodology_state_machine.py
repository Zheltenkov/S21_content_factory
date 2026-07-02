import pytest

from content_gen.methodology.state_machine import (
    MethodologyRuntimeAction,
    MethodologyRuntimeState,
    MethodologyStateMachine,
    MethodologyStateTransitionError,
)


def test_methodology_state_machine_models_review_resume_flow() -> None:
    machine = MethodologyStateMachine()

    state = machine.transition(MethodologyRuntimeState.RUNNING, MethodologyRuntimeAction.PAUSE_FOR_REVIEW)
    state = machine.transition(state, MethodologyRuntimeAction.REQUEST_CHANGES)
    state = machine.transition(state, MethodologyRuntimeAction.APPROVE_DIFF)
    state = machine.transition(state, MethodologyRuntimeAction.APPROVE_REVIEW)
    state = machine.transition(state, MethodologyRuntimeAction.START_RESUME)
    state = machine.transition(state, MethodologyRuntimeAction.COMPLETE)

    assert state == MethodologyRuntimeState.COMPLETED


def test_methodology_state_machine_rejects_completed_resume() -> None:
    machine = MethodologyStateMachine()

    with pytest.raises(MethodologyStateTransitionError):
        machine.transition(MethodologyRuntimeState.COMPLETED, MethodologyRuntimeAction.START_RESUME)
