import pytest

from content_gen.workflow_state import (
    GenerationNodeCheckpoint,
    GenerationStateMachine,
    WorkflowCommand,
    WorkflowValidationResult,
)


def test_generation_state_machine_records_checkpoint_and_completion():
    machine = GenerationStateMachine.create("req-1", "user-1", project_title="Test")
    machine.start(total_nodes=3)
    machine.node_started("context", progress_current=1)

    checkpoint = GenerationNodeCheckpoint(
        request_id="req-1",
        node_id="context",
        node_name="Context",
        checkpoint_index=1,
        input_hash="abc",
        output_artifact={"markdown": {"type": "str", "chars": 10}},
        context_snapshot={"seed": {"title_seed": "Test"}},
        status="success",
        validation=WorkflowValidationResult(status="passed"),
    )
    snapshot = machine.node_completed(checkpoint)

    assert snapshot.status == "node_completed"
    assert snapshot.last_completed_node == "context"
    assert snapshot.progress_current == 1
    assert snapshot.checkpoints[0].input_hash == "abc"
    assert snapshot.checkpoints[0].context_snapshot["seed"]["title_seed"] == "Test"

    machine.complete()
    assert machine.snapshot.status == "completed"
    assert machine.snapshot.progress_current == 3


def test_generation_state_machine_blocks_mutation_after_terminal_state():
    machine = GenerationStateMachine.create("req-2")
    machine.cancel(WorkflowCommand(command="cancel", issued_by="user-1"))

    with pytest.raises(ValueError):
        machine.node_started("theory")


def test_generation_state_machine_records_retry_node_command():
    machine = GenerationStateMachine.create("req-3")
    machine.start(total_nodes=10)

    snapshot = machine.submit_command(
        WorkflowCommand(
            command="retry_node",
            node_id="practice",
            issued_by="user-1",
            payload={"reason": "methodology_change"},
        )
    )

    assert snapshot.status == "resuming"
    assert snapshot.resume_from_node == "practice"
    assert snapshot.commands[-1].command == "retry_node"
