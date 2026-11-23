"""Durable translation metadata; the process dictionary is only a small compatibility cache.

SQLite lives beside translation artifacts on STORAGE_DIR. Transactions merge individual
updates, so progress, ownership and final links cannot overwrite one another across workers.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

import psutil
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class TranslationJobStoreError(RuntimeError):
    """Durable metadata could not be read or committed; never report a false success."""


class TranslationJobRecord(BaseModel):
    """Versioned private snapshot; executor identity is never exposed by the status API."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    version: Literal[1] = 1
    request_id: str = Field(min_length=1, max_length=100)
    user_id: str | None = None
    status: Literal["pending", "in_progress", "completed", "failed", "cancelled"]
    created_at: datetime
    updated_at: datetime
    phase: str | None = None
    original_markdown: str | None = None
    translated_markdown: str | None = None
    target_language: str | None = None
    error: str | None = None
    job_type: str | None = None
    translated_subtitles: str | None = None
    original_transcript: str | None = None
    subtitle_format: str | None = None
    progress: float | None = Field(default=None, ge=0, le=100)
    error_code: str | None = None
    result_links: dict[str, str] | None = None
    source_filename: str | None = None
    source_format: str | None = None
    executor_pid: int | None = None
    executor_started_at: float | None = None

    @field_validator("result_links")
    @classmethod
    def artifact_basenames(cls, links: dict[str, str] | None) -> dict[str, str] | None:
        """A durable link must not escape its request's artifact directory."""
        for name in (links or {}).values():
            if not name or name in {".", ".."} or "/" in name or "\\" in name or "\0" in name:
                raise ValueError("Artifact links must contain a filename, not a path")
        return links


ACTIVE_STATUSES = frozenset({"pending", "in_progress"})
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _executor_alive(record: TranslationJobRecord) -> bool:
    """Creation time distinguishes a restarted container/worker from a reused PID."""
    if record.executor_pid is None or record.executor_started_at is None:
        return False
    try:
        process = psutil.Process(record.executor_pid)
        return bool(process.is_running() and abs(float(process.create_time()) - record.executor_started_at) < 0.001)
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        # Lack of inspection permission is not proof that an active worker died.
        return True


class TranslationJobStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.path.touch(mode=0o600, exist_ok=True)
            connection = sqlite3.connect(str(self.path), timeout=10)
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("CREATE TABLE IF NOT EXISTS translation_jobs (request_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except (sqlite3.Error, OSError, ValidationError) as error:
            if connection is not None:
                connection.rollback()
            raise TranslationJobStoreError("Хранилище статусов перевода временно недоступно") from error
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _read(connection: sqlite3.Connection, request_id: str) -> TranslationJobRecord | None:
        row = connection.execute("SELECT payload FROM translation_jobs WHERE request_id=?", (request_id,)).fetchone()
        return TranslationJobRecord.model_validate_json(row[0]) if row is not None else None

    @staticmethod
    def _write(connection: sqlite3.Connection, record: TranslationJobRecord) -> None:
        connection.execute(
            "INSERT INTO translation_jobs(request_id,payload) VALUES(?,?) "
            "ON CONFLICT(request_id) DO UPDATE SET payload=excluded.payload",
            (record.request_id, record.model_dump_json()),
        )

    def update(self, request_id: str, changes: Mapping[str, object], *, phase_only: bool = False) -> TranslationJobRecord | None:
        """Commit a patch before publishing it; owner and terminal states are immutable."""
        with self._transaction() as connection:
            previous = self._read(connection, request_id)
            if phase_only and (previous is None or previous.status in TERMINAL_STATUSES):
                return previous
            if previous is not None:
                owner = changes.get("user_id")
                if owner is not None and previous.user_id is not None and owner != previous.user_id:
                    raise TranslationJobStoreError("Владелец задачи перевода не может быть изменён")
                if previous.status in TERMINAL_STATUSES and changes.get("status", previous.status) != previous.status:
                    raise TranslationJobStoreError("Завершённую задачу нельзя повторно запустить с тем же ID")
            now = _now()
            payload = previous.model_dump() if previous is not None else {"request_id": request_id, "created_at": now}
            payload.update(changes)
            payload["updated_at"] = now
            # Only the writer that creates the work owns execution. Status polling in
            # another worker never takes ownership of that work.
            if payload.get("status") in ACTIVE_STATUSES and payload.get("executor_pid") is None:
                payload["executor_pid"] = os.getpid()
                payload["executor_started_at"] = psutil.Process().create_time()
            record = TranslationJobRecord.model_validate(payload)
            self._write(connection, record)
            return record

    def get(self, request_id: str) -> TranslationJobRecord | None:
        """No age-based expiry: active work and completed links remain available."""
        if not request_id or len(request_id) > 100:
            return None
        with self._transaction() as connection:
            record = self._read(connection, request_id)
            if record is not None and record.status in ACTIVE_STATUSES and not _executor_alive(record):
                record = record.model_copy(update={
                    "status": "failed", "phase": "interrupted", "error_code": "TRANSLATION_INTERRUPTED",
                    "error": "Перевод прерван перезапуском приложения. Запустите его повторно. Готовые файлы сохранены.",
                    "updated_at": _now(),
                })
                self._write(connection, record)
            return record


@lru_cache(maxsize=16)
def _store_at(path: str) -> TranslationJobStore:
    return TranslationJobStore(Path(path))


def translation_job_store() -> TranslationJobStore:
    """Production STORAGE_DIR is the persistent /data volume; tests override only the DB."""
    storage = Path(os.getenv("STORAGE_DIR", os.path.join(tempfile.gettempdir(), "content_generator_translations")))
    path = os.getenv("TRANSLATION_JOB_DB_PATH") or str(storage / "translations" / ".state" / "jobs.sqlite3")
    return _store_at(str(Path(path).resolve()))
