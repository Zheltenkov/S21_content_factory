"""Endpoint для генерации контента."""

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from content_factory.api.db.generation_results_db import list_recent_generation_results_for_user, save_generation_result
from content_factory.api.db.user_runs_db import (
    count_active_user_runs,
    list_recent_user_runs_for_user,
    mark_user_run_cancelled,
    reconcile_stale_active_user_runs,
    upsert_user_run,
)
from content_factory.api.db.logging_db import write_log_async
from content_factory.api.db.paused_generation_db import (
    load_paused_generation_session,
    mark_paused_generation_diff_approved,
    mark_paused_generation_approved,
    mark_paused_generation_completed,
    mark_paused_generation_rejected,
    record_paused_generation_change_request,
    record_paused_generation_preview,
    save_paused_generation_session,
)
from content_factory.api.dependencies import get_current_user
from content_factory.api.schemas import GenerateStartResponse, GenerationStatusResponse
from content_factory.api.services.generation_errors import GenerationServiceError
from content_factory.api.services.generation_resume_service import GenerationResumeService
from content_factory.api.services.generation_start_service import GenerationStartService
from content_factory.api.services.generation_status_service import GenerationStatusService
from content_factory.api.services.methodology_review_artifacts import (
    checkpoint_payload_hash as _checkpoint_payload_hash,
    context_preview_markdown as _context_preview_markdown,
    is_final_checkpoint_payload as _is_final_checkpoint_payload,
    markdown_outline as _markdown_outline,
    markdown_section as _markdown_section,
    markdown_subsections as _markdown_subsections,
    methodology_human_review_enabled as _methodology_human_review_enabled,
    refresh_checkpoint_artifact as _refresh_checkpoint_artifact,
)
from content_factory.api.services.methodology_review_service import MethodologyReviewService
from content_factory.api.services.methodology_review_state import (
    build_methodology_review_state as _build_methodology_review_state,
    change_action_ids as _change_action_ids,
    current_review_action_slice as _current_review_action_slice,
    latest_review_action as _latest_review_action,
    preview_hash as _preview_hash,
    revision_results_for_action_ids as _revision_results_for_action_ids,
)
from content_factory.api.utils.logger import get_logger
from content_factory.api.utils.result_cache import (
    cancel_generation_task,
    get_generation_error,
    get_generation_methodology,
    get_generation_owner,
    get_generation_status,
    get_active_generation_count,
    get_result,
    register_generation_task,
    set_generation_owner,
    set_generation_methodology,
    set_generation_status,
    store_generation_error,
    store_result,
    unregister_generation_task,
)

logger = get_logger("generation")
from content_factory.generation.exceptions import ContentGenerationError
from content_factory.platform.llm.factory import create_llm_client
from content_factory.generation.methodology import (
    MethodologistChangeRequest,
    ScopedRevisionExecutor,
)
from content_factory.generation.orchestrator import Orchestrator

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


class MethodologyReviewActionRequest(BaseModel):
    """Решение методолога для paused generation."""

    comment: str | None = None


class MethodologyAssistantCommandRequest(BaseModel):
    """Free-form methodology chat message that is parsed into a typed command."""

    message: str = Field(min_length=1, max_length=4000)
    selected_target_id: str | None = Field(default=None, max_length=300)


class WorkflowCommandRequest(BaseModel):
    """Durable workflow command for node-level recovery operations."""

    command: Literal["cancel", "resume", "retry_node", "regenerate_section"]
    node_id: str | None = Field(default=None, max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)


def _dashboard_title(seed_data: dict[str, Any] | None, report_json: dict[str, Any] | None) -> str:
    """Resolve a compact human-readable project title for dashboard rows."""
    seed = seed_data or {}
    report = report_json or {}
    candidates = [
        seed.get("platform_name"),
        seed.get("title_seed"),
        report.get("title"),
        report.get("project_title"),
        report.get("readme_title"),
    ]
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "README project"


def _dashboard_score(rubric: dict[str, Any] | None) -> dict[str, Any]:
    """Extract dashboard-friendly rubric score without exposing the full rubric."""
    if not isinstance(rubric, dict):
        return {"total": None, "max": None, "label": "—"}
    total = rubric.get("total")
    maximum = rubric.get("max_score") or rubric.get("max")
    if total is None and isinstance(rubric.get("items"), list):
        total = sum(1 for item in rubric["items"] if isinstance(item, dict) and item.get("score") == 1)
    if maximum is None and isinstance(rubric.get("items"), list):
        maximum = len(rubric["items"])
    label = f"{total}/{maximum}" if total is not None and maximum is not None else "—"
    return {"total": total, "max": maximum, "label": label}


def _dashboard_kind_label(kind: str | None) -> str:
    labels = {
        "generation": "ГЕНЕРАЦИЯ",
        "regeneration": "ПЕРЕГЕНЕРАЦИЯ",
        "checker": "ПРОВЕРКА",
        "translation": "ПЕРЕВОД",
        "video_translation": "ПЕРЕВОД",
        "readme_improvement": "УЛУЧШЕНИЕ",
    }
    return labels.get(str(kind or "").lower(), "ЗАПУСК")


def _dashboard_status_label(status: str | None) -> str:
    labels = {
        "completed": "ГОТОВО",
        "in_progress": "В РАБОТЕ",
        "pending": "ОЖИДАЕТ",
        "needs_review": "НА ПРОВЕРКЕ",
        "resuming": "ВОЗОБНОВЛЕНИЕ",
        "interrupted": "ПРЕРВАНО",
        "failed": "ОШИБКА",
        "cancelled": "ОСТАНОВЛЕНО",
    }
    return labels.get(str(status or "").lower(), str(status or "—").upper())


def _dashboard_open_url(kind: str | None) -> str:
    normalized = str(kind or "").lower()
    if normalized in {"checker", "readme_improvement"}:
        return "/app/auditor"
    if normalized in {"translation", "video_translation"}:
        return "/app/translate"
    return "/app/generate"


def _dashboard_run_kind(row: Any, report_json: dict[str, Any] | None) -> dict[str, str]:
    """Classify a saved result for dashboard chips."""
    if row.regenerated_markdown:
        return {"kind": "regeneration", "label": "ПЕРЕГЕНЕРАЦИЯ"}
    if isinstance(report_json, dict) and report_json.get("translated_markdown"):
        return {"kind": "translation", "label": "ПЕРЕВОД"}
    return {"kind": "generation", "label": "ГЕНЕРАЦИЯ"}


def _dashboard_user_run_item(row: dict[str, Any]) -> dict[str, Any]:
    """Serialize a generic user activity row into the public dashboard contract."""
    kind = str(row.get("kind") or "generation")
    status = str(row.get("status") or "completed")
    score = row.get("score") if isinstance(row.get("score"), dict) else None
    return {
        "request_id": row.get("request_id"),
        "title": row.get("title") or "README project",
        "kind": kind,
        "kind_label": _dashboard_kind_label(kind),
        "status": status,
        "status_label": _dashboard_status_label(status),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "score": score or {"total": None, "max": None, "label": "—"},
        "open_url": _dashboard_open_url(kind),
        "download_url": row.get("result_url"),
    }


def _dashboard_recent_item(row: Any) -> dict[str, Any]:
    """Serialize a GenerationResult into the public dashboard contract."""
    rubric = row.rubric.rubric_data if getattr(row, "rubric", None) else None
    report_json = row.report.report_data if getattr(row, "report", None) else None
    kind = _dashboard_run_kind(row, report_json if isinstance(report_json, dict) else None)
    return {
        "request_id": row.request_id,
        "title": _dashboard_title(row.seed_data, report_json if isinstance(report_json, dict) else None),
        "kind": kind["kind"],
        "kind_label": kind["label"],
        "status": "completed",
        "status_label": "ГОТОВО",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "score": _dashboard_score(rubric if isinstance(rubric, dict) else None),
        "open_url": _dashboard_open_url(kind["kind"]),
        "download_url": f"/api/v1/download/{row.request_id}",
    }


def _dashboard_sort_key(item: dict[str, Any]) -> str:
    return str(item.get("updated_at") or item.get("created_at") or "")


def _raise_generation_error(error: GenerationServiceError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.detail)


def _generation_resume_service() -> GenerationResumeService:
    return GenerationResumeService(
        status_getter=get_generation_status,
        status_setter=set_generation_status,
        methodology_getter=get_generation_methodology,
        methodology_setter=set_generation_methodology,
        error_store=store_generation_error,
        result_store=store_result,
        task_unregister=unregister_generation_task,
        result_saver=save_generation_result,
        paused_saver=save_paused_generation_session,
        paused_completed_marker=mark_paused_generation_completed,
        log_writer=write_log_async,
        llm_factory=lambda provider=None: create_llm_client(
            provider=provider,
            enable_cache=True,
            enable_batching=True,
        ),
        orchestrator_cls=Orchestrator,
        completed_saver=_save_completed_generation,
    )


def _generation_start_service() -> GenerationStartService:
    return GenerationStartService(
        status_setter=set_generation_status,
        error_store=store_generation_error,
        task_registrar=register_generation_task,
        background_runner=_run_generation_background,
        log_writer=write_log_async,
        logger=logger,
    )


def _generation_status_service() -> GenerationStatusService:
    return GenerationStatusService(
        status_getter=get_generation_status,
        status_setter=set_generation_status,
        result_getter=get_result,
        error_getter=get_generation_error,
        task_canceller=cancel_generation_task,
        owner_getter=get_generation_owner,
        methodology_getter=get_generation_methodology,
        methodology_setter=set_generation_methodology,
        paused_loader=load_paused_generation_session,
        log_writer=write_log_async,
        logger=logger,
    )


def _methodology_review_service() -> MethodologyReviewService:
    return MethodologyReviewService(
        status_getter=get_generation_status,
        status_setter=set_generation_status,
        error_store=store_generation_error,
        paused_loader=load_paused_generation_session,
        approve_paused=mark_paused_generation_approved,
        reject_paused=mark_paused_generation_rejected,
        record_change_request=record_paused_generation_change_request,
        record_preview=record_paused_generation_preview,
        approve_diff=mark_paused_generation_diff_approved,
        task_registrar=register_generation_task,
        resume_background=_resume_generation_background,
        log_writer=write_log_async,
        workflow_command_background=_workflow_command_background,
        llm_factory=lambda: create_llm_client(default_role="critic", enable_cache=True, enable_batching=True),
        revision_executor_cls=ScopedRevisionExecutor,
    )


async def _save_completed_generation(
    *,
    request_id: str,
    user_id: str,
    project_seed_payload: dict[str, Any],
    result: Any,
) -> bool:
    """Router-level adapter for completion persistence."""
    saved = await _generation_resume_service().save_completed_generation(
        request_id=request_id,
        user_id=user_id,
        project_seed_payload=project_seed_payload,
        result=result,
    )
    if saved:
        rubric = getattr(result, "rubric", None)
        await asyncio.to_thread(
            upsert_user_run,
            request_id=request_id,
            user_id=user_id,
            kind="generation",
            status="completed",
            title=_dashboard_title(project_seed_payload, None),
            score=_dashboard_score(rubric if isinstance(rubric, dict) else None),
            result_url=f"/api/v1/download/{request_id}",
        )
    return saved


async def _store_methodology_pause(
    *,
    request_id: str,
    user_id: str,
    project_seed_dict: dict[str, Any],
    track_paths: list[str],
    error: ContentGenerationError,
) -> bool:
    """Router-level adapter for persisted methodology pauses."""
    stored = await _generation_resume_service().store_methodology_pause(
        request_id=request_id,
        user_id=user_id,
        project_seed_dict=project_seed_dict,
        track_paths=track_paths,
        error=error,
    )
    if stored:
        await asyncio.to_thread(
            upsert_user_run,
            request_id=request_id,
            user_id=user_id,
            kind="generation",
            status="needs_review",
            title=_dashboard_title(project_seed_dict, None),
            result_url=f"/api/v1/download/{request_id}",
        )
    return stored


async def _run_generation_background(
    request_id: str,
    user_id: str,
    project_seed_dict: dict,
    track_paths: list[str],
    temp_dir: str | None = None
) -> None:
    """Router-level adapter for the background generation task."""
    await _generation_resume_service().run_generation_background(
        request_id=request_id,
        user_id=user_id,
        project_seed_dict=project_seed_dict,
        track_paths=track_paths,
        temp_dir=temp_dir,
    )


async def _resume_generation_background(
    request_id: str,
    user_id: str,
    paused_session: dict[str, Any],
    review_comment: str | None = None,
) -> None:
    """Router-level adapter for background resume."""
    await asyncio.to_thread(
        upsert_user_run,
        request_id=request_id,
        user_id=user_id,
        kind="generation",
        status="resuming",
        title="Генерация README",
        result_url=f"/api/v1/download/{request_id}",
    )
    await _generation_resume_service().resume_generation_background(
        request_id=request_id,
        user_id=user_id,
        paused_session=paused_session,
        review_comment=review_comment,
    )


async def _workflow_command_background(
    request_id: str,
    user_id: str,
    command: str,
    node_id: str | None,
    payload: dict[str, Any],
) -> None:
    """Router-level adapter for durable workflow commands."""
    await _generation_resume_service().run_workflow_command_background(
        request_id=request_id,
        user_id=user_id,
        command=command,
        node_id=node_id,
        payload=payload,
    )


@router.get("/dashboard/recent")
async def get_dashboard_recent_runs(
    limit: int = Query(6, ge=1, le=20),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Возвращает dashboard-сводку последних запусков только текущего пользователя."""
    user_id = user.get("id", "anonymous")
    await asyncio.to_thread(reconcile_stale_active_user_runs, user_id=user_id)
    user_run_rows = await asyncio.to_thread(list_recent_user_runs_for_user, user_id, limit)
    legacy_rows = await asyncio.to_thread(list_recent_generation_results_for_user, user_id, limit)

    items_by_request_id: dict[str, dict[str, Any]] = {}
    for row in user_run_rows:
        item = _dashboard_user_run_item(row)
        request_id = str(item.get("request_id") or "")
        if request_id:
            items_by_request_id[request_id] = item
    for row in legacy_rows:
        request_id = str(getattr(row, "request_id", "") or "")
        if request_id and request_id not in items_by_request_id:
            items_by_request_id[request_id] = _dashboard_recent_item(row)

    items = sorted(items_by_request_id.values(), key=_dashboard_sort_key, reverse=True)[:limit]
    active_tasks = max(
        await asyncio.to_thread(count_active_user_runs, user_id),
        get_active_generation_count(user_id),
    )
    return {
        "user": {
            "id": user_id,
            "username": user.get("username") or user_id,
            "email": user.get("email"),
            "role": user.get("role"),
        },
        "active_tasks": active_tasks,
        "last_run_at": (items[0]["updated_at"] or items[0]["created_at"]) if items else None,
        "items": items,
    }


@router.post("/generate", response_model=GenerateStartResponse)
@limiter.limit("10/minute")
async def generate(
    request: Request,
    track_files: list[UploadFile] | None = File(None),
    user: dict = Depends(get_current_user)
):
    """Запускает асинхронную генерацию контента учебного проекта."""
    user_id = user.get("id", "anonymous")
    try:
        response = await _generation_start_service().start_from_request(
            request=request,
            track_files=track_files,
            user_id=user_id,
        )
        set_generation_owner(response.request_id, user_id)
        await asyncio.to_thread(
            upsert_user_run,
            request_id=response.request_id,
            user_id=user_id,
            kind="generation",
            status=response.status or "in_progress",
            title="Генерация README",
            result_url=f"/api/v1/download/{response.request_id}",
        )
        return response
    except GenerationServiceError as error:
        _raise_generation_error(error)


@router.get("/generate/status/{request_id}", response_model=GenerationStatusResponse)
async def get_generation_status_endpoint(
    request_id: str,
    user: dict = Depends(get_current_user)
):
    """Получает статус генерации контента."""
    user_id = user.get("id", "anonymous")
    try:
        return await _generation_status_service().get_status(request_id, user_id=user_id)
    except GenerationServiceError as error:
        _raise_generation_error(error)


@router.post("/generate/review/{request_id}/approve", response_model=GenerateStartResponse)
async def approve_methodology_review(
    request_id: str,
    request: MethodologyReviewActionRequest,
    user: dict = Depends(get_current_user),
):
    """Approve a paused methodology gate and continue generation from the saved node."""
    user_id = user.get("id", "anonymous")
    try:
        payload = await _methodology_review_service().approve_review(
            request_id=request_id,
            user_id=user_id,
            comment=request.comment,
        )
        return GenerateStartResponse(**payload)
    except GenerationServiceError as error:
        _raise_generation_error(error)


@router.post("/generate/review/{request_id}/reject")
async def reject_methodology_review(
    request_id: str,
    request: MethodologyReviewActionRequest,
    user: dict = Depends(get_current_user),
):
    """Reject a paused methodology gate and stop the generation job."""
    user_id = user.get("id", "anonymous")
    try:
        return await _methodology_review_service().reject_review(
            request_id=request_id,
            user_id=user_id,
            comment=request.comment,
        )
    except GenerationServiceError as error:
        _raise_generation_error(error)


@router.get("/generate/review/{request_id}")
async def get_methodology_review_state(
    request_id: str,
    user: dict = Depends(get_current_user),
):
    """Return durable methodology review state for UI history and target selection."""
    user_id = user.get("id", "anonymous")
    try:
        return await _methodology_review_service().get_review_state(request_id, user_id=user_id)
    except GenerationServiceError as error:
        _raise_generation_error(error)


@router.post("/generate/review/{request_id}/preview-changes")
async def preview_methodology_changes(
    request_id: str,
    user: dict = Depends(get_current_user),
):
    """Run pending scoped revisions on a copied paused context and return diff previews."""
    user_id = user.get("id", "anonymous")
    try:
        return await _methodology_review_service().preview_changes(request_id, user_id=user_id)
    except GenerationServiceError as error:
        _raise_generation_error(error)


@router.post("/generate/review/{request_id}/approve-diff")
async def approve_methodology_diff(
    request_id: str,
    request: MethodologyReviewActionRequest,
    user: dict = Depends(get_current_user),
):
    """Approve the latest persisted preview before generation resume."""
    user_id = user.get("id", "anonymous")
    try:
        return await _methodology_review_service().approve_review_diff(
            request_id,
            user_id=user_id,
            comment=request.comment,
        )
    except GenerationServiceError as error:
        _raise_generation_error(error)


@router.post("/generate/review/{request_id}/request-changes")
async def request_methodology_changes(
    request_id: str,
    request: MethodologistChangeRequest,
    user: dict = Depends(get_current_user),
):
    """Record a scoped change request while keeping the generation paused."""
    user_id = user.get("id", "anonymous")
    try:
        return await _methodology_review_service().request_changes(
            request_id,
            user_id=user_id,
            change_request=request,
        )
    except GenerationServiceError as error:
        _raise_generation_error(error)


@router.post("/generate/review/{request_id}/assistant-command")
async def run_methodology_assistant_command(
    request_id: str,
    request: MethodologyAssistantCommandRequest,
    user: dict = Depends(get_current_user),
):
    """Parse a methodologist chat message and apply the resulting checkpoint command."""
    user_id = user.get("id", "anonymous")
    try:
        return await _methodology_review_service().run_assistant_command(
            request_id,
            user_id=user_id,
            message=request.message,
            selected_target_id=request.selected_target_id,
        )
    except GenerationServiceError as error:
        _raise_generation_error(error)


@router.post("/generate/cancel/{request_id}")
async def cancel_generation_endpoint(
    request_id: str,
    user: dict = Depends(get_current_user)
):
    """Останавливает активную генерацию контента."""
    user_id = user.get("id", "anonymous")
    try:
        response = await _generation_status_service().cancel(request_id, user_id=user_id)
        await asyncio.to_thread(
            upsert_user_run,
            request_id=request_id,
            user_id=user_id,
            kind="generation",
            status="cancelled",
            title="Генерация README",
            result_url=f"/api/v1/download/{request_id}",
        )
        return response
    except GenerationServiceError as error:
        cancelled_row = await asyncio.to_thread(
            mark_user_run_cancelled,
            request_id=request_id,
            user_id=user_id,
            reason=f"runtime_cancel_failed:{error.status_code}",
        )
        if cancelled_row and error.status_code in {404, 500}:
            return {
                "success": True,
                "message": "Запуск снят с активного статуса. Runtime-задача не найдена.",
            }
        _raise_generation_error(error)


@router.post("/generate/workflow/{request_id}/command", response_model=GenerateStartResponse)
async def submit_generation_workflow_command(
    request_id: str,
    request: WorkflowCommandRequest,
    user: dict = Depends(get_current_user),
):
    """Submit a durable workflow command: resume, retry_node or regenerate_section."""
    user_id = user.get("id", "anonymous")
    if request.command == "cancel":
        try:
            await _generation_status_service().cancel(request_id, user_id=user_id)
            return GenerateStartResponse(request_id=request_id, status="cancelled")
        except GenerationServiceError as error:
            _raise_generation_error(error)

    try:
        await _generation_status_service().get_status(request_id, user_id=user_id)
    except GenerationServiceError as error:
        _raise_generation_error(error)

    set_generation_status(request_id, "in_progress")
    task = asyncio.create_task(
        _workflow_command_background(
            request_id,
            user_id,
            request.command,
            request.node_id,
            request.payload,
        )
    )
    register_generation_task(request_id, task)
    await asyncio.to_thread(
        upsert_user_run,
        request_id=request_id,
        user_id=user_id,
        kind="generation",
        status="resuming",
        title="Генерация README",
        result_url=f"/api/v1/download/{request_id}",
    )
    return GenerateStartResponse(request_id=request_id, status="in_progress")
