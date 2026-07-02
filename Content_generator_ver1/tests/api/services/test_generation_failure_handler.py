import pytest

from api.services.generation_failure_handler import GenerationFailureHandler
from content_gen.exceptions import ContentGenerationError


async def _noop_log_writer(**kwargs):
    return None


class _WorkflowService:
    def __init__(self) -> None:
        self.failed = None

    def mark_failed(self, *, request_id, user_id, error):
        self.failed = {"request_id": request_id, "user_id": user_id, "error": error}


class _FailingPausePersister:
    async def store_methodology_pause(self, **kwargs):
        raise RuntimeError("db is unavailable")


@pytest.mark.asyncio
async def test_methodology_pause_persistence_failure_does_not_leave_run_active() -> None:
    statuses = {}
    errors = {}
    workflow_service = _WorkflowService()
    handler = GenerationFailureHandler(
        status_setter=lambda request_id, status: statuses.__setitem__(request_id, status),
        error_store=lambda request_id, message: errors.__setitem__(request_id, message),
        log_writer=_noop_log_writer,
        pause_persister=_FailingPausePersister(),
        workflow_service=workflow_service,
    )
    error = ContentGenerationError(
        "Контрольная точка",
        context={"error_type": "HumanApprovalCheckpoint", "phase": "context"},
    )

    await handler.handle_content_generation_error(
        request_id="req-1",
        user_id="user-1",
        project_seed_dict={"methodology_human_review": True},
        track_paths=[],
        error=error,
    )

    assert statuses["req-1"] == "failed"
    assert "не удалось сохранить" in errors["req-1"]
    assert workflow_service.failed["request_id"] == "req-1"
