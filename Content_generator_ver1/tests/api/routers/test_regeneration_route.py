import pytest
from fastapi import HTTPException

from api.routers import regeneration
from api.routers.regeneration import RegenerateRequest
from api.services.regeneration_service import RegenerationResultView, RegenerationValidationError


class _FakeSuccessService:
    def __init__(self) -> None:
        self.command = None

    async def regenerate(self, command):
        self.command = command
        return RegenerationResultView(
            request_id=command.request_id,
            regenerated_md="# README",
            changes=["updated"],
            rubric={"items": []},
            text_stats={"chars": 8, "words": 2, "tokens": 2},
            learning_outcomes=["Понимает контекст проекта."],
            skills=["Редактирование README"],
            seed_source="request.project_seed",
            learning_context_source="seed",
            validation_report={"schema_version": "regeneration.pipeline.v1"},
        )


class _FakeValidationService:
    async def regenerate(self, _command):
        raise RegenerationValidationError("latex broken", status_code=422)


@pytest.mark.asyncio
async def test_regenerate_route_delegates_to_service(monkeypatch) -> None:
    fake_service = _FakeSuccessService()
    monkeypatch.setattr(regeneration, "_regeneration_service", fake_service)
    monkeypatch.setattr(regeneration, "get_generation_owner", lambda request_id: "user_1")

    response = await regeneration.regenerate(
        RegenerateRequest(
            original_request_id="source_1",
            original_md="# Old",
            comments="Исправь",
            language="ru",
            project_seed={"title_seed": "Проект", "project_description": "Описание."},
        ),
        user={"id": "user_1"},
    )

    assert response.regenerated_md == "# README"
    assert response.seed_source == "request.project_seed"
    assert response.validation_report == {"schema_version": "regeneration.pipeline.v1"}
    assert fake_service.command is not None
    assert fake_service.command.original_request_id == "source_1"
    assert fake_service.command.project_seed == {"title_seed": "Проект", "project_description": "Описание."}


@pytest.mark.asyncio
async def test_regenerate_route_rejects_foreign_original_request(monkeypatch) -> None:
    monkeypatch.setattr(regeneration, "get_generation_owner", lambda request_id: "user_2")

    with pytest.raises(HTTPException) as exc_info:
        await regeneration.regenerate(
            RegenerateRequest(
                original_request_id="source_1",
                original_md="# Old",
                comments="Исправь",
                language="ru",
            ),
            user={"id": "user_1"},
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_regenerate_route_preserves_validation_status(monkeypatch) -> None:
    monkeypatch.setattr(regeneration, "_regeneration_service", _FakeValidationService())

    with pytest.raises(HTTPException) as exc_info:
        await regeneration.regenerate(
            RegenerateRequest(
                original_md="# Old",
                comments="Исправь",
                language="ru",
            ),
            user={"id": "user_1"},
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "latex broken"
