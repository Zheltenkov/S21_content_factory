"""Application service for starting generation requests."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request, UploadFile

from api.schemas import GenerateStartResponse
from api.utils.data_masking import mask_dict
from api.utils.logging_context import set_request_id, set_user_id
from content_gen.exceptions import ValidationError
from content_gen.models.schemas import ProjectSeed
from content_gen.workflow_profiles import resolve_workflow_profile, workflow_profile_payload

from .generation_errors import GenerationServiceError
from .generation_workflow_service import GenerationWorkflowService

BackgroundRunner = Callable[[str, str, dict[str, Any], list[str], str | None], Awaitable[None]]


class GenerationStartService:
    """Parse generation seed, validate it and register a background generation task."""

    def __init__(
        self,
        *,
        status_setter: Callable[[str, str], Any],
        error_store: Callable[[str, str], Any],
        task_registrar: Callable[[str, asyncio.Task[Any]], Any],
        background_runner: BackgroundRunner,
        log_writer: Callable[..., Awaitable[Any]],
        logger: Any,
        request_id_factory: Callable[[], str] | None = None,
        workflow_service: GenerationWorkflowService | None = None,
    ) -> None:
        self._status_setter = status_setter
        self._error_store = error_store
        self._task_registrar = task_registrar
        self._background_runner = background_runner
        self._log_writer = log_writer
        self._logger = logger
        self._request_id_factory = request_id_factory or (lambda: str(uuid.uuid4()))
        self._workflow_service = workflow_service or GenerationWorkflowService()

    async def start_from_request(
        self,
        *,
        request: Request,
        track_files: list[UploadFile] | None,
        user_id: str,
    ) -> GenerateStartResponse:
        """Start a generation job from the HTTP request body."""
        request_id = self._request_id_factory()
        set_request_id(request_id)
        set_user_id(user_id)
        self._status_setter(request_id, "pending")
        self._logger.info("📝 Начало генерации контента для пользователя %s", user_id)

        track_files_list = await self._normalize_track_files(track_files)
        file_count = len(track_files_list)
        await self._log_writer(
            request_id=request_id,
            level="INFO",
            message="Начало генерации контента",
            user_id=user_id,
            phase="initialization",
            metadata={"file_count": file_count, "legacy_track_files_ignored": file_count > 0},
        )

        try:
            seed_data = await self._parse_seed_payload(request)
            await self._log_seed_metadata(request_id=request_id, user_id=user_id, seed_data=seed_data)
            seed_size = len(str(seed_data))
            self._logger.info("🔍 generate endpoint: язык из seed_data: %r", seed_data.get("language"))
            try:
                project_seed = ProjectSeed(**seed_data)
                self._logger.info(
                    "🔍 generate endpoint: язык из project_seed: %r (тип: %s)",
                    project_seed.language,
                    type(project_seed.language).__name__,
                )
            except Exception as exc:  # noqa: BLE001
                await self._log_writer(
                    request_id=request_id,
                    level="ERROR",
                    message=f"Ошибка валидации seed данных: {str(exc)}",
                    user_id=user_id,
                    phase="validation",
                    metadata={"error": str(exc), "seed_size": seed_size},
                )
                raise GenerationServiceError(400, f"Ошибка валидации данных: {str(exc)}") from exc

            project_seed_dict = project_seed.model_dump()
            workflow_profile = resolve_workflow_profile(project_seed_dict)
            workflow_profile_data = workflow_profile_payload(workflow_profile)
            self._workflow_service.create(
                request_id=request_id,
                user_id=user_id,
                seed_metadata=self._workflow_seed_metadata(
                    seed_data,
                    project_seed_payload=project_seed_dict,
                    track_paths=[],
                    workflow_profile=workflow_profile_data,
                ),
            )
            generation_task = asyncio.create_task(
                self._background_runner(request_id, user_id, project_seed_dict, [], None)
            )
            self._task_registrar(request_id, generation_task)
            return GenerateStartResponse(
                request_id=request_id,
                status="pending",
                workflow_profile=workflow_profile_data,
            )
        except ValidationError as exc:
            self._logger.error("❌ Ошибка валидации: %s", str(exc))
            self._status_setter(request_id, "failed")
            self._error_store(request_id, f"Ошибка валидации: {str(exc)}")
            self._workflow_service.mark_failed(
                request_id=request_id,
                user_id=user_id,
                error=f"Ошибка валидации: {str(exc)}",
            )
            await self._log_writer(
                request_id=request_id,
                level="ERROR",
                message=f"Ошибка валидации: {str(exc)}",
                user_id=user_id,
                phase="validation_error",
                metadata={"error_type": type(exc).__name__, "error_message": str(exc), "context": exc.context},
            )
            raise GenerationServiceError(400, f"Ошибка валидации: {str(exc)}") from exc
        except GenerationServiceError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._logger.error("💥 Неожиданная ошибка при подготовке: %s", str(exc), exc_info=True)
            self._status_setter(request_id, "failed")
            self._error_store(request_id, f"Ошибка при подготовке данных: {str(exc)}")
            self._workflow_service.mark_failed(
                request_id=request_id,
                user_id=user_id,
                error=f"Ошибка при подготовке данных: {str(exc)}",
            )
            await self._log_writer(
                request_id=request_id,
                level="ERROR",
                message=f"Неожиданная ошибка при подготовке: {str(exc)}",
                user_id=user_id,
                phase="unexpected_error",
                metadata={"error_type": type(exc).__name__, "error_message": str(exc)},
            )
            raise GenerationServiceError(500, "Внутренняя ошибка сервера") from exc

    async def _parse_seed_payload(self, request: Request) -> dict[str, Any]:
        content_type = request.headers.get("content-type", "")
        if "multipart/form-data" in content_type:
            form = await request.form()
            seed_str = form.get("seed")
            if not seed_str:
                raise GenerationServiceError(400, "Не указаны данные проекта (seed)")
            try:
                seed_data = json.loads(str(seed_str))
            except json.JSONDecodeError as exc:
                raise GenerationServiceError(400, "Ошибка парсинга JSON из FormData") from exc
        else:
            try:
                body = await request.json()
                seed_data = body.get("seed", {})
                if not seed_data:
                    raise GenerationServiceError(400, "Не указаны данные проекта (seed)")
            except GenerationServiceError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise GenerationServiceError(400, "Не указаны данные проекта (seed)") from exc

        if not isinstance(seed_data, dict):
            raise GenerationServiceError(400, "Seed должен быть JSON-объектом")
        seed_data.setdefault("language", "ru")
        return seed_data

    async def _log_seed_metadata(self, *, request_id: str, user_id: str, seed_data: dict[str, Any]) -> None:
        seed_metadata = mask_dict(
            {
                "language": seed_data.get("language"),
                "llm_provider": seed_data.get("llm_provider"),
                "track": seed_data.get("track"),
                "project_type": seed_data.get("project_type"),
                "learning_outcomes_count": len(seed_data.get("learning_outcomes", [])),
                "thematic_blocks_count": len(seed_data.get("thematic_blocks", [])),
                "methodology_human_review": seed_data.get("methodology_human_review", False),
            }
        )
        await self._log_writer(
            request_id=request_id,
            level="INFO",
            message="Данные проекта получены",
            user_id=user_id,
            phase="initialization",
            metadata={"seed_metadata": seed_metadata},
        )

    @staticmethod
    def _workflow_seed_metadata(
        seed_data: dict[str, Any],
        *,
        project_seed_payload: dict[str, Any] | None = None,
        track_paths: list[str] | None = None,
        workflow_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Keep only compact seed metadata in the workflow root row."""
        metadata = mask_dict(
            {
                "language": seed_data.get("language"),
                "llm_provider": seed_data.get("llm_provider"),
                "track": seed_data.get("track"),
                "project_type": seed_data.get("project_type"),
                "audience_level": seed_data.get("audience_level"),
                "project_title": seed_data.get("title_seed") or seed_data.get("platform_name"),
                "methodology_human_review": seed_data.get("methodology_human_review", False),
                "workflow_profile_id": (workflow_profile or {}).get("id"),
            }
        )
        metadata["project_seed_payload"] = project_seed_payload or dict(seed_data)
        metadata["track_paths"] = list(track_paths or [])
        metadata["workflow_profile"] = workflow_profile or workflow_profile_payload(
            resolve_workflow_profile(project_seed_payload or seed_data)
        )
        return metadata

    @staticmethod
    async def _normalize_track_files(track_files: list[UploadFile] | None) -> list[UploadFile]:
        if not track_files:
            return []
        if hasattr(track_files, "__aiter__"):
            return [item async for item in track_files]
        if isinstance(track_files, list):
            return track_files
        try:
            return list(track_files)
        except (TypeError, ValueError):
            return []
