"""Pydantic схемы для API запросов и ответов."""

from typing import Any

from pydantic import BaseModel

from content_gen.models.schemas import ProjectSeed


class GenerateRequest(BaseModel):
    """Запрос на генерацию контента."""
    seed: ProjectSeed
    # track_files обрабатываются отдельно через UploadFile


class GenerateResponse(BaseModel):
    """Ответ с результатом генерации."""
    request_id: str
    result: dict[str, Any]  # OrchestratorResult.report_json
    warnings: list[str]


class GenerateStartResponse(BaseModel):
    """Ответ при запуске генерации (асинхронный режим)."""
    request_id: str
    status: str  # pending
    workflow_profile: dict[str, Any] | None = None


class GenerationStatusResponse(BaseModel):
    """Ответ с статусом генерации."""
    request_id: str
    status: str  # pending, in_progress, needs_review, interrupted, completed, failed
    error: str | None = None
    result: dict[str, Any] | None = None
    warnings: list[str] | None = None
    methodology: dict[str, Any] | None = None
    workflow: dict[str, Any] | None = None
    workflow_profile: dict[str, Any] | None = None
