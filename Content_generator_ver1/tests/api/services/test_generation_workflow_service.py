from api.db.paused_generation_codec import serialize_context
from api.services import generation_workflow_service
from api.services.generation_workflow_service import GenerationWorkflowService


def test_record_methodology_assistant_command_writes_workflow_history(monkeypatch):
    calls = []
    monkeypatch.setattr(
        generation_workflow_service,
        "transition_generation_workflow",
        lambda **kwargs: calls.append(kwargs) or {"ok": True},
    )
    service = GenerationWorkflowService()

    service.record_methodology_assistant_command(
        request_id="req-1",
        user_id="user-1",
        command_payload={
            "command": "simplify_task",
            "checkpoint_id": "practice-review",
            "checkpoint_stage": "practice",
            "workflow_node_id": "practice",
            "target_id": "practice.task.1",
            "target_stage": "practice",
            "target_selector": "practice.task.1",
        },
    )

    assert calls
    assert calls[0]["status"] == "needs_review"
    assert calls[0]["command"]["command"] == "simplify_task"
    assert calls[0]["command"]["node_id"] == "practice"
    assert calls[0]["command"]["issued_at"]
    assert calls[0]["command"]["payload"]["source"] == "methodology_assistant"
    assert calls[0]["command"]["payload"]["checkpoint_id"] == "practice-review"


def test_build_recovery_session_resumes_after_latest_checkpoint(monkeypatch):
    service = GenerationWorkflowService()
    workflow = {
        "request_id": "req-1",
        "user_id": "user-1",
        "metadata": {"project_seed_payload": {"language": "ru"}, "track_paths": []},
        "checkpoints": [
            {
                "checkpoint_index": 1,
                "node_id": "context",
                "node_name": "Context",
                "status": "success",
                "duration_ms": 10,
                "validation_result": {"issues": []},
                "context_snapshot": serialize_context({"seed": "s", "raw_input": {"language": "ru"}}),
            },
            {
                "checkpoint_index": 2,
                "node_id": "task_planning",
                "node_name": "Planner",
                "status": "success",
                "duration_ms": 20,
                "validation_result": {"issues": []},
                "context_snapshot": serialize_context({"seed": "s", "task_plan": "p"}),
            },
        ],
    }
    monkeypatch.setattr(service, "get", lambda request_id: workflow)

    session = service.build_recovery_session(request_id="req-1")

    assert session["start_index"] == 2
    assert session["context"]["task_plan"] == "p"
    assert [step.node_id for step in session["previous_steps"]] == ["context", "task_planning"]


def test_build_recovery_session_retry_node_uses_previous_checkpoint(monkeypatch):
    service = GenerationWorkflowService()
    workflow = {
        "request_id": "req-2",
        "user_id": "user-1",
        "metadata": {"project_seed_payload": {"language": "ru"}, "track_paths": []},
        "checkpoints": [
            {
                "checkpoint_index": 4,
                "node_id": "skeleton",
                "node_name": "Skeleton",
                "status": "success",
                "duration_ms": 10,
                "validation_result": {"issues": []},
                "context_snapshot": serialize_context({"markdown": "# Skeleton"}),
            },
            {
                "checkpoint_index": 5,
                "node_id": "theory",
                "node_name": "Theory",
                "status": "error",
                "duration_ms": 20,
                "validation_result": {"issues": ["bad theory"]},
                "context_snapshot": serialize_context({"markdown": "# Broken theory"}),
            },
        ],
    }
    monkeypatch.setattr(service, "get", lambda request_id: workflow)

    session = service.build_recovery_session(
        request_id="req-2",
        command="retry_node",
        node_id="theory",
        payload={"reason": "repair"},
    )

    assert session["target_node"] == "theory"
    assert session["start_index"] == 4
    assert session["context"]["markdown"] == "# Skeleton"
    assert [step.node_id for step in session["previous_steps"]] == ["skeleton"]


def test_build_recovery_session_without_checkpoints_uses_initial_seed(monkeypatch):
    service = GenerationWorkflowService()
    workflow = {
        "request_id": "req-3",
        "user_id": "user-1",
        "metadata": {
            "project_seed_payload": {"language": "ru", "title_seed": "Проект"},
            "track_paths": ["track.md"],
        },
        "checkpoints": [],
    }
    monkeypatch.setattr(service, "get", lambda request_id: workflow)

    session = service.build_recovery_session(request_id="req-3")

    assert session["raw_input"]["title_seed"] == "Проект"
    assert session["track_paths"] == ["track.md"]
    assert session["start_index"] == 0


def test_build_recovery_session_normalizes_legacy_init_checkpoint(monkeypatch):
    service = GenerationWorkflowService()
    workflow = {
        "request_id": "req-4",
        "user_id": "user-1",
        "metadata": {"project_seed_payload": {"language": "ru"}, "track_paths": []},
        "checkpoints": [
            {
                "checkpoint_index": 1,
                "node_id": "init",
                "node_name": "Legacy Init",
                "status": "success",
                "duration_ms": 10,
                "validation_result": {"issues": []},
                "context_snapshot": serialize_context({"seed": "s", "raw_input": {"language": "ru"}}),
            },
        ],
    }
    monkeypatch.setattr(service, "get", lambda request_id: workflow)

    session = service.build_recovery_session(request_id="req-4")

    assert session["start_index"] == 1
    assert session["context"]["seed"] == "s"
    assert [step.node_id for step in session["previous_steps"]] == ["context"]


def test_build_recovery_session_retry_node_normalizes_legacy_init(monkeypatch):
    service = GenerationWorkflowService()
    workflow = {
        "request_id": "req-5",
        "user_id": "user-1",
        "metadata": {
            "project_seed_payload": {"language": "ru", "title_seed": "Legacy"},
            "track_paths": [],
        },
        "checkpoints": [
            {
                "checkpoint_index": 1,
                "node_id": "init",
                "node_name": "Legacy Init",
                "status": "success",
                "duration_ms": 10,
                "validation_result": {"issues": []},
                "context_snapshot": serialize_context({"seed": "s", "raw_input": {"language": "ru"}}),
            },
        ],
    }
    monkeypatch.setattr(service, "get", lambda request_id: workflow)

    session = service.build_recovery_session(
        request_id="req-5",
        command="retry_node",
        node_id="init",
        payload={"reason": "legacy checkpoint"},
    )

    assert session["target_node"] == "context"
    assert session["start_index"] == 0
    assert session["raw_input"]["title_seed"] == "Legacy"
