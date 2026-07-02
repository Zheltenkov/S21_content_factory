"""Высокоуровневые операции записи/чтения результатов генерации из БД."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import joinedload

from api.utils.logger import get_logger
from api.utils.report_compressor import compress_report_json

from .models import GenerationResult, ReportResult, RubricResult
from .session import SessionLocal

logger = get_logger("db.generation_results")


def save_generation_result(
    request_id: str,
    user_id: str,
    seed_data: dict[str, Any] | None = None,
    markdown: str | None = None,
    rubric: dict[str, Any] | None = None,
    report_json: dict[str, Any] | None = None,
    text_stats: dict[str, Any] | None = None,
    task_plan: dict[str, Any] | None = None,
    issues: list | None = None,
    practice_critic_issues: list | None = None,
    agent_config_versions: dict[str, str] | None = None,
    flow_trace: list | None = None,
) -> GenerationResult:
    """Сохраняет результат генерации вместе с rubric и report."""
    db = SessionLocal()
    try:
        logger.debug("🗄️ Сохранение результата генерации в БД (%s)", request_id)
        gen_result = GenerationResult(
            request_id=request_id,
            user_id=user_id,
            seed_data=seed_data,
            markdown=markdown,
            text_stats=text_stats,
            task_plan=task_plan,
            issues=issues,
            practice_critic_issues=practice_critic_issues,
            agent_config_versions=agent_config_versions,
            flow_trace=flow_trace,
        )
        db.add(gen_result)
        db.flush()  # Получаем ID до добавления зависимых записей

        if rubric:
            db.add(
                RubricResult(
                    generation_result_id=gen_result.id,
                    rubric_data=rubric,
                )
            )

        if report_json:
            compressed_report = compress_report_json(report_json)
            db.add(
                ReportResult(
                    generation_result_id=gen_result.id,
                    report_data=compressed_report,
                )
            )

        db.commit()
        db.refresh(gen_result)
        logger.debug("✅ Результат сохранён в БД (%s)", request_id)
        return gen_result
    except Exception:
        db.rollback()
        logger.exception("❌ Ошибка сохранения результата генерации (%s)", request_id)
        raise
    finally:
        db.close()


def update_regeneration_result(
    request_id: str,
    regenerated_markdown: str | None = None,
    regenerated_rubric: dict[str, Any] | None = None,
    regeneration_comments: str | None = None,
    regeneration_changes: list | None = None,
    original_markdown: str | None = None,
) -> GenerationResult | None:
    """Обновляет запись генерации данными перегенерации."""
    db = SessionLocal()
    try:
        gen_result = (
            db.query(GenerationResult)
            .filter(GenerationResult.request_id == request_id)
            .first()
        )
        if not gen_result:
            logger.warning("⚠️ GenerationResult не найден (%s)", request_id)
            return None

        if regenerated_markdown is not None:
            gen_result.regenerated_markdown = regenerated_markdown
        if regeneration_comments is not None:
            gen_result.regeneration_comments = regeneration_comments
        if regeneration_changes is not None:
            gen_result.regeneration_changes = regeneration_changes
        if original_markdown is not None:
            gen_result.original_markdown = original_markdown

        gen_result.updated_at = datetime.utcnow()

        if regenerated_rubric and gen_result.rubric:
            gen_result.rubric.rubric_data = regenerated_rubric
            gen_result.rubric.updated_at = datetime.utcnow()

        db.commit()
        db.refresh(gen_result)
        logger.debug("♻️ Результат перегенерации сохранён (%s)", request_id)
        return gen_result
    except Exception:
        db.rollback()
        logger.exception("❌ Ошибка обновления перегенерации (%s)", request_id)
        raise
    finally:
        db.close()


def get_generation_result(request_id: str) -> GenerationResult | None:
    """Возвращает результат генерации по request_id со связанными сущностями."""
    db = SessionLocal()
    try:
        return (
            db.query(GenerationResult)
            .filter(GenerationResult.request_id == request_id)
            .first()
        )
    finally:
        db.close()


def get_rubric_by_request_id(request_id: str) -> dict[str, Any] | None:
    """Возвращает rubric.json по request_id (если сохранён)."""
    db = SessionLocal()
    try:
        result = (
            db.query(GenerationResult)
            .options(joinedload(GenerationResult.rubric))
            .filter(GenerationResult.request_id == request_id)
            .first()
        )
        if result and result.rubric:
            # Достаём данные до закрытия сессии, чтобы избежать DetachedInstanceError
            return dict(result.rubric.rubric_data) if isinstance(result.rubric.rubric_data, dict) else result.rubric.rubric_data
        return None
    finally:
        db.close()


def get_report_by_request_id(request_id: str) -> dict[str, Any] | None:
    """Возвращает сокращённый report.json по request_id (если сохранён)."""
    db = SessionLocal()
    try:
        result = (
            db.query(GenerationResult)
            .options(joinedload(GenerationResult.report))
            .filter(GenerationResult.request_id == request_id)
            .first()
        )
        if result and result.report:
            data = result.report.report_data
            # Гарантируем, что вернём отсоединяемый словарь, а не ленивую структуру
            return dict(data) if isinstance(data, dict) else data
        return None
    finally:
        db.close()


def list_recent_generation_results_for_user(user_id: str, limit: int = 8) -> list[GenerationResult]:
    """Возвращает последние сохраненные результаты генерации конкретного пользователя."""
    safe_limit = max(1, min(limit, 50))
    db = SessionLocal()
    try:
        rows = (
            db.query(GenerationResult)
            .options(joinedload(GenerationResult.rubric), joinedload(GenerationResult.report))
            .filter(GenerationResult.user_id == user_id)
            .order_by(GenerationResult.created_at.desc())
            .limit(safe_limit)
            .all()
        )
        for row in rows:
            # Force-load relationship payloads before the session is closed.
            _ = row.rubric.rubric_data if row.rubric else None
            _ = row.report.report_data if row.report else None
        return rows
    finally:
        db.close()
