"""Endpoints для улучшения README после проверки."""

import io
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.db.logging_db import write_log_async
from api.dependencies import get_current_user
from api.services.readme_improvement_service import (
    ExtractForImprovementCommand,
    GenerateImprovedCommand,
    ReadmeImprovementNotFoundError,
    ReadmeImprovementService,
)
from api.utils.improvement_cache import get_improvement_owner
from api.utils.logger import get_logger
from api.utils.logging_context import set_request_id, set_user_id
from api.utils.result_cache import get_generation_owner, set_generation_status
from content_gen.llm.factory import create_llm_client
from content_gen.models.schemas import ProjectSeed

router = APIRouter()
logger = get_logger("readme_improvement")


def _ensure_improvement_owner(request_id: str, user: dict) -> None:
    """Запрещает доступ к чужим improvement/diff request_id."""
    current_user_id = user.get("id")
    owner_id = get_improvement_owner(request_id) or get_generation_owner(request_id)
    if owner_id and current_user_id and owner_id != current_user_id:
        raise HTTPException(status_code=403, detail="Нет доступа к запуску другого пользователя")
    if not owner_id:
        raise HTTPException(status_code=403, detail="Владелец запуска не определен")


def _build_improvement_service() -> ReadmeImprovementService:
    """Build service with route-level dependencies for tests/monkeypatching."""
    return ReadmeImprovementService(
        llm_factory=lambda: create_llm_client(default_role="planner", enable_cache=True, enable_batching=True),
    )


class ExtractForImprovementRequest(BaseModel):
    """Запрос на извлечение данных для улучшения README."""
    readme_text: str = Field(..., description="Текст исходного README")
    learning_outcomes: list[str] | None = Field(
        default=None,
        description="Образовательные результаты (опционально, для контекста)"
    )
    curriculum_project: dict[str, Any] | None = Field(
        default=None,
        description="Если передан УП и выбран проект: данные проекта из УП (block + project). Тогда извлечение из README не вызывается, входные данные берутся из УП."
    )
    curriculum_context: dict[str, Any] | None = Field(
        default=None,
        description="Контекст УП для генерации (previous_projects, next_projects, sjm_context и т.д.). Передаётся вместе с curriculum_project."
    )


class ExtractForImprovementResponse(BaseModel):
    """Ответ с извлеченными данными для редактирования."""
    request_id: str
    status: str
    partial_seed: dict[str, Any]
    classification: dict[str, Any]
    metadata: dict[str, Any]


class GenerateImprovedRequest(BaseModel):
    """Запрос на генерацию улучшенного README."""
    request_id: str = Field(..., description="ID запроса извлечения")
    seed: ProjectSeed = Field(..., description="Отредактированный ProjectSeed")


class GenerateImprovedResponse(BaseModel):
    """Ответ на запрос генерации улучшенного README."""
    request_id: str
    status: str
    generation_request_id: str | None = None


@router.post("/readme/improve/extract", response_model=ExtractForImprovementResponse)
async def extract_data_for_improvement(
    request: ExtractForImprovementRequest,
    user: dict = Depends(get_current_user)
):
    """
    Извлекает данные из README для последующего улучшения.
    
    Сохраняет исходный README и извлеченные данные в кэш.
    """
    request_id = str(uuid.uuid4())
    user_id = user.get("id", "anonymous")

    set_request_id(request_id)
    set_user_id(user_id)

    try:
        result = await _build_improvement_service().extract_for_improvement(
            ExtractForImprovementCommand(
                request_id=request_id,
                user_id=user_id,
                readme_text=request.readme_text,
                learning_outcomes=request.learning_outcomes,
                curriculum_project=request.curriculum_project,
                curriculum_context=request.curriculum_context,
            )
        )
        return ExtractForImprovementResponse(
            request_id=result.request_id,
            status=result.status,
            partial_seed=result.partial_seed,
            classification=result.classification,
            metadata=result.metadata,
        )

    except Exception as e:
        logger.error(f"❌ Ошибка при извлечении данных: {e}", exc_info=True)
        await write_log_async(
            request_id=request_id,
            level="ERROR",
            message=f"Ошибка при извлечении данных: {e}",
            user_id=user_id,
            phase="improvement_extract_error",
            metadata={"error": str(e)}
        )
        raise HTTPException(status_code=500, detail="Ошибка при извлечении данных")


@router.post("/readme/improve/generate", response_model=GenerateImprovedResponse)
async def generate_improved_readme(
    request: GenerateImprovedRequest,
    user: dict = Depends(get_current_user)
):
    """
    Генерирует улучшенный README на основе извлеченных и отредактированных данных.
    
    Использует существующий пайплайн генерации контента.
    """
    user_id = user.get("id", "anonymous")
    generation_request_id = str(uuid.uuid4())

    set_request_id(generation_request_id)
    set_user_id(user_id)
    _ensure_improvement_owner(request.request_id, user)

    try:
        result = await _build_improvement_service().generate_improved_readme(
            GenerateImprovedCommand(
                extract_request_id=request.request_id,
                generation_request_id=generation_request_id,
                user_id=user_id,
                seed=request.seed,
            )
        )
        return GenerateImprovedResponse(
            request_id=result.request_id,
            status=result.status,
            generation_request_id=result.generation_request_id,
        )
    except ReadmeImprovementNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.error(f"❌ Ошибка при запуске генерации: {e}", exc_info=True)
        set_generation_status(generation_request_id, "failed")
        await write_log_async(
            request_id=generation_request_id,
            level="ERROR",
            message=f"Ошибка при запуске генерации: {e}",
            user_id=user_id,
            phase="improvement_generate_error",
            metadata={"error": str(e)}
        )
        raise HTTPException(status_code=500, detail="Ошибка при запуске генерации")


@router.get("/readme/improve/diff/{request_id}")
async def get_readme_diff(
    request_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Получает diff между исходным и улучшенным README.
    
    Args:
        request_id: ID запроса извлечения (extract request_id)
    """
    user_id = user.get("id", "anonymous")
    _ensure_improvement_owner(request_id, user)
    try:
        return _build_improvement_service().get_diff(request_id)
    except ReadmeImprovementNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/readme/improve/status/{generation_request_id}")
async def get_generation_status_endpoint(
    generation_request_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Получает статус генерации улучшенного README.
    
    Использует существующий механизм проверки статуса генерации.
    """
    _ensure_improvement_owner(generation_request_id, user)
    try:
        return _build_improvement_service().get_generation_status(generation_request_id)
    except ReadmeImprovementNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/readme/improve/download/{generation_request_id}")
async def download_improved_readme_archive(
    generation_request_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Скачивает ZIP архив с улучшенным README и дочерними файлами.
    
    Args:
        generation_request_id: ID запроса генерации улучшенного README
        user: Данные пользователя
        
    Returns:
        ZIP архив с улучшенным README (regen_<имя>.md) и дочерними файлами
    """
    user_id = user.get("id", "anonymous")
    set_user_id(user_id)
    set_request_id(generation_request_id)
    _ensure_improvement_owner(generation_request_id, user)

    try:
        archive = _build_improvement_service().build_download_archive(generation_request_id)
    except ReadmeImprovementNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    return StreamingResponse(
        io.BytesIO(archive.data),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{archive.filename}"'}
    )
