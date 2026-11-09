"""Evaluation checkpoints live beside content, outside its frozen storage contracts."""
import logging
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError

from ..content import store as content_store
from ..core.access import current_owner
from ..core.storage import Locks, read_owned, write_model
from .models import Evaluation

lock = Locks()
logger = logging.getLogger(__name__)


def folder(run_id: str) -> Path:
    return content_store.CONTENT_DIR / "evaluations" / str(UUID(run_id))


def save(report: Evaluation) -> None:
    write_model(folder(report.run_id) / f"{UUID(report.id)}.json", report)


def latest(run_id: str) -> Evaluation | None:
    reports, warnings = history(run_id)
    if not reports:
        return None
    # A damaged historical checkpoint cannot brick a new check. Preserve it for recovery.
    report = max(reports, key=lambda r: r.created_at)
    report.history_warnings = warnings
    return report


def history(run_id: str) -> tuple[list[Evaluation], list[str]]:
    reports: list[Evaluation] = []
    warnings: list[str] = []
    for path in folder(run_id).glob("*.json"):
        try:
            report = read_owned(path, Evaluation, current_owner.get(), "Проверка не найдена", "Повреждён сохранённый отчёт проверки")
            if report.run_id != run_id or path.stem != report.id:
                raise ValueError("Mismatched checkpoint identity")
            reports.append(report)
        except HTTPException as error:
            if error.status_code != 409:
                raise  # Ownership failures stay fail-closed, even for a malformed foreign report.
            warnings.append(f"Не удалось прочитать отчёт {path.name}; исходный файл сохранён. История может быть неполной.")
        except (OSError, ValueError):
            warnings.append(f"Не удалось прочитать отчёт {path.name}; исходный файл сохранён. История может быть неполной.")
    for warning in warnings:
        logger.warning("evaluation history run=%s: %s", run_id, warning)
    return reports, warnings


def recover() -> None:
    for path in (content_store.CONTENT_DIR / "evaluations").glob("*/*.json"):
        try:
            report = Evaluation.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValueError, OSError, ValidationError):
            logger.warning("Unreadable evaluation checkpoint: %s", path.name)
            continue
        if report.state == "running":
            report.state = "interrupted"
            report.notes.append("Сервер перезапущен. Завершённые проверки сохранены; повторный запуск продолжит работу.")
            save(report)
