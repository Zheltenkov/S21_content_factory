import pytest

from api.services.generation_errors import GenerationServiceError
from api.services.generation_status_service import GenerationStatusService


class FakeWorkflowService:
    def __init__(self, payload=None):
        self.payload = payload
        self.cancelled = None

    def get(self, request_id):
        return self.payload

    def mark_cancelled(self, *, request_id, user_id):
        self.cancelled = {"request_id": request_id, "user_id": user_id}


async def _noop_log_writer(**kwargs):
    return None


def _service(*, status=None, workflow=None, paused_session=None, task_canceller=lambda request_id: True, owner="user-1"):
    statuses = {}
    if status is not None:
        statuses["req-1"] = status
    workflow_service = FakeWorkflowService(workflow)
    service = GenerationStatusService(
        status_getter=lambda request_id: statuses.get(request_id),
        status_setter=lambda request_id, value: statuses.__setitem__(request_id, value),
        result_getter=lambda request_id: None,
        error_getter=lambda request_id: None,
        task_canceller=task_canceller,
        owner_getter=lambda request_id: owner,
        methodology_getter=lambda request_id: None,
        methodology_setter=lambda request_id, payload: None,
        paused_loader=lambda request_id: paused_session,
        log_writer=_noop_log_writer,
        logger=type("Logger", (), {"warning": lambda *args, **kwargs: None, "debug": lambda *args, **kwargs: None, "info": lambda *args, **kwargs: None})(),
        workflow_service=workflow_service,
    )
    return service, workflow_service, statuses


@pytest.mark.asyncio
async def test_status_falls_back_to_durable_workflow_snapshot():
    workflow = {
        "request_id": "req-1",
        "status": "node_completed",
        "progress_current": 3,
        "progress_total": 9,
        "checkpoints": [{"node_id": "theory"}],
    }
    service, _, statuses = _service(workflow=workflow)

    response = await service.get_status("req-1")

    assert response.status == "in_progress"
    assert response.workflow == workflow
    assert statuses["req-1"] == "in_progress"
    assert response.workflow_profile
    assert response.workflow_profile["id"] == "standard"


@pytest.mark.asyncio
async def test_status_exposes_workflow_profile_from_durable_metadata():
    workflow = {
        "request_id": "req-1",
        "status": "needs_review",
        "metadata": {
            "workflow_profile": {
                "id": "methodology",
                "title": "Методологический режим",
                "description": "",
                "stages": ["context"],
                "gates": [{"after_stage": "context", "action": "approve_or_revise"}],
                "capabilities": {
                    "project_regeneration": False,
                    "section_regeneration": False,
                    "methodology_assistant": True,
                    "stage_review": True,
                    "final_readme_editing": True,
                    "checklist_editing": True,
                },
            }
        },
    }
    service, _, statuses = _service(workflow=workflow)

    response = await service.get_status("req-1")

    assert response.status == "needs_review"
    assert statuses["req-1"] == "needs_review"
    assert response.workflow_profile
    assert response.workflow_profile["id"] == "methodology"
    assert response.workflow_profile["capabilities"]["project_regeneration"] is False


@pytest.mark.asyncio
async def test_status_prefers_durable_needs_review_over_cached_in_progress():
    workflow = {
        "request_id": "req-1",
        "status": "needs_review",
        "metadata": {"workflow_profile_id": "methodology"},
        "checkpoints": [{"node_id": "context", "status": "paused"}],
    }
    paused_session = {"methodology": {"summary": {"latest_action": "pause"}}}
    service, _, statuses = _service(
        status="in_progress",
        workflow=workflow,
        paused_session=paused_session,
    )

    response = await service.get_status("req-1")

    assert response.status == "needs_review"
    assert statuses["req-1"] == "needs_review"
    assert response.workflow == workflow
    assert response.workflow_profile["id"] == "methodology"


@pytest.mark.asyncio
async def test_cancel_is_recorded_as_workflow_command():
    service, workflow_service, _ = _service(status="in_progress")

    response = await service.cancel("req-1", user_id="user-1")

    assert response["success"] is True
    assert workflow_service.cancelled == {"request_id": "req-1", "user_id": "user-1"}


@pytest.mark.asyncio
async def test_cancel_works_from_durable_workflow_after_process_restart():
    workflow = {"request_id": "req-1", "status": "running"}
    service, workflow_service, statuses = _service(workflow=workflow, task_canceller=lambda request_id: False)

    response = await service.cancel("req-1", user_id="user-1")

    assert response["success"] is True
    assert statuses["req-1"] == "cancelled"
    assert workflow_service.cancelled == {"request_id": "req-1", "user_id": "user-1"}


@pytest.mark.asyncio
async def test_status_exposes_interrupted_workflow_after_process_restart():
    workflow = {"request_id": "req-1", "status": "interrupted", "error": "server restarted"}
    service, _, statuses = _service(workflow=workflow)

    response = await service.get_status("req-1")

    assert response.status == "interrupted"
    assert response.error == "server restarted"
    assert response.workflow == workflow
    assert statuses["req-1"] == "interrupted"


@pytest.mark.asyncio
async def test_status_rejects_cross_user_access():
    service, _, _ = _service(status="in_progress", owner="user-1")

    with pytest.raises(GenerationServiceError) as exc_info:
        await service.get_status("req-1", user_id="user-2")

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_unknown_status_without_workflow_still_returns_404():
    service, _, _ = _service()

    with pytest.raises(GenerationServiceError):
        await service.get_status("req-1")
