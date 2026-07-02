import pytest

from api.services.methodology_review_service import MethodologyReviewService


class FakeWorkflowService:
    def __init__(self) -> None:
        self.commands = []

    def record_methodology_assistant_command(self, **kwargs) -> None:
        self.commands.append(kwargs)

    def mark_resuming(self, **_kwargs) -> None:
        return None


async def _noop_log(**_kwargs):
    return None


@pytest.mark.asyncio
async def test_assistant_regenerate_section_runs_workflow_command() -> None:
    statuses = {"req-1": "needs_review"}
    registered_tasks = []
    workflow_commands = []
    fake_workflow = FakeWorkflowService()
    paused = {
        "user_id": "user-1",
        "context": {
            "markdown": "# README\n\n## Глава 2. Теоретический блок\n\nТекст.",
            "human_approval_checkpoint": {
                "id": "theory-review",
                "stage": "theory",
                "node_id": "theory",
                "artifact": {},
            },
        },
        "review_actions": [],
    }

    async def fake_workflow_background(**kwargs):
        workflow_commands.append(kwargs)

    service = MethodologyReviewService(
        status_getter=lambda request_id: statuses.get(request_id),
        status_setter=lambda request_id, status: statuses.__setitem__(request_id, status),
        error_store=lambda _request_id, _error: None,
        paused_loader=lambda _request_id: paused,
        approve_paused=lambda _request_id, **_kwargs: {**paused, "status": "approved"},
        reject_paused=lambda *_args, **_kwargs: True,
        record_change_request=lambda *_args, **_kwargs: paused,
        record_preview=lambda *_args, **_kwargs: paused,
        approve_diff=lambda *_args, **_kwargs: paused,
        task_registrar=lambda _request_id, task: registered_tasks.append(task),
        resume_background=lambda **_kwargs: None,
        workflow_command_background=fake_workflow_background,
        log_writer=_noop_log,
        workflow_service=fake_workflow,
    )

    response = await service.run_assistant_command(
        "req-1",
        user_id="user-1",
        message="Перегенерируй главу 2, теория слишком общая",
    )
    if registered_tasks:
        await registered_tasks[0]

    assert response["status"] == "in_progress"
    assert response["assistant_command"]["command"] == "regenerate_section"
    assert response["assistant_command"]["checkpoint_id"] == "theory-review"
    assert statuses["req-1"] == "in_progress"
    assert fake_workflow.commands[0]["command_payload"]["command"] == "regenerate_section"
    assert workflow_commands[0]["command"] == "regenerate_section"
    assert workflow_commands[0]["node_id"] == "theory"
    assert workflow_commands[0]["payload"]["source"] == "methodology_assistant"
