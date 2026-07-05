"""SQLAlchemy модели для базы данных логов."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from passlib.context import CryptContext
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Декларативная база SQLAlchemy 2.0 для всех моделей API."""


def utc_now_naive() -> datetime:
    """Return UTC time as a naive datetime for existing DateTime columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

# Контекст для хеширования паролей
# Используем Argon2 как основной (нет ограничения в 72 байта), bcrypt для совместимости со старыми паролями
pwd_context = CryptContext(schemes=["argon2", "bcrypt"], deprecated="auto")


class LogEntry(Base):
    """Модель записи лога в базе данных."""

    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(100))
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, nullable=False)
    level: Mapped[str] = mapped_column(String(10), nullable=False)  # DEBUG, INFO, WARNING, ERROR, CRITICAL
    message: Mapped[str] = mapped_column(Text, nullable=False)
    agent_name: Mapped[str | None] = mapped_column(String(100))
    phase: Mapped[str | None] = mapped_column(String(100))
    meta_data: Mapped[Any] = mapped_column(JSON, nullable=True)  # Дополнительные данные (ошибки, метрики, etc.) - переименовано из 'metadata' (зарезервированное имя в SQLAlchemy)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, nullable=False)

    # Индексы для быстрого поиска
    __table_args__ = (
        Index('idx_logs_request_id', 'request_id'),
        Index('idx_logs_user_id', 'user_id'),
        Index('idx_logs_timestamp', 'timestamp'),
        Index('idx_logs_level', 'level'),
    )

    def to_dict(self) -> dict[str, Any]:
        """Преобразует запись в словарь."""
        return {
            "id": self.id,
            "request_id": self.request_id,
            "user_id": self.user_id,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "level": self.level,
            "message": self.message,
            "agent_name": self.agent_name,
            "phase": self.phase,
            "metadata": self.meta_data,  # Возвращаем как 'metadata' для обратной совместимости API
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class User(Base):
    """Модель пользователя."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, nullable=False)
    last_login: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    sessions: Mapped[list["UserSession"]] = relationship(
        "UserSession", back_populates="user", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index('idx_users_active_email', 'is_active', 'email'),
    )

    @staticmethod
    def hash_password(password: str) -> str:
        """
        Хеширует пароль с использованием Argon2.

        Argon2 не имеет ограничения по длине пароля (в отличие от bcrypt с 72 байтами).
        Новые пароли будут хешироваться с Argon2.
        """
        return str(pwd_context.hash(password))

    def verify_password(self, password: str) -> bool:
        """
        Проверяет пароль.

        Поддерживает проверку как Argon2 (новые), так и bcrypt (старые) хешей.
        """
        return bool(pwd_context.verify(password, self.hashed_password))

    def needs_rehash(self) -> bool:
        """
        Проверяет, нужно ли перехешировать пароль (если это старый bcrypt хеш).

        Returns:
            True если пароль нужно перехешировать в Argon2
        """
        return self.hashed_password.startswith("$2b$") or self.hashed_password.startswith("$2a$")

    def to_dict(self) -> dict[str, Any]:
        """Преобразует в словарь (без пароля)."""
        return {
            "id": self.id,
            "email": self.email,
            "username": self.username,
            "role": self.role,
            "is_active": self.is_active,
            "is_email_verified": self.is_email_verified,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login": self.last_login.isoformat() if self.last_login else None,
        }


class PasswordResetToken(Base):
    """Модель токена для восстановления пароля."""

    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    token: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, nullable=False)

    user: Mapped["User"] = relationship("User")

    __table_args__ = (
        Index('ix_password_reset_tokens_user_id', 'user_id'),
        Index('ix_password_reset_tokens_expires_at', 'expires_at'),
    )


class UserSession(Base):
    """Модель сессии пользователя."""

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    session_token: Mapped[str] = mapped_column(String(255), nullable=False)
    token_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Хеш токена для безопасности
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, nullable=False)
    last_activity: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45))  # IPv6 может быть до 45 символов
    user_agent: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[str] = mapped_column(String(10), default="true", nullable=False)  # "true" или "false" для совместимости
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    user_id_fk: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    user: Mapped["User"] = relationship("User", back_populates="sessions")

    # Индексы для быстрого поиска
    __table_args__ = (
        Index('idx_sessions_user_id', 'user_id'),
        Index('idx_sessions_token', 'session_token', unique=True),
        Index('idx_sessions_token_hash', 'token_hash'),
        Index('idx_sessions_started_at', 'started_at'),
        Index('idx_sessions_active', 'is_active'),
        Index('idx_sessions_token_active', 'session_token', 'is_active'),
        Index('idx_sessions_user_activity', 'user_id', 'last_activity'),
        Index('idx_sessions_user_active', 'user_id_fk', 'is_active', 'last_activity'),
    )

    def to_dict(self) -> dict[str, Any]:
        """Преобразует запись в словарь."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.username,
            "session_token": self.session_token,
            "token_hash": self.token_hash,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_activity": self.last_activity.isoformat() if self.last_activity else None,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "is_active": self.is_active == "true",
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
        }


class RequestLog(Base):
    """Модель для логирования HTTP запросов."""

    __tablename__ = "request_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(100))
    method: Mapped[str] = mapped_column(String(10), nullable=False)  # GET, POST, PUT, DELETE и т.д.
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    request_body: Mapped[Any] = mapped_column(JSON, nullable=True)  # Тело запроса (с маскированием чувствительных данных)
    response_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Время ответа в миллисекундах
    ip_address: Mapped[str | None] = mapped_column(String(45))  # IPv6 может быть до 45 символов
    user_agent: Mapped[str | None] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, nullable=False)

    # Индексы для быстрого поиска
    __table_args__ = (
        Index('idx_request_logs_request_id', 'request_id'),
        Index('idx_request_logs_user_id', 'user_id'),
        Index('idx_request_logs_timestamp', 'timestamp'),
        Index('idx_request_logs_status', 'status_code'),
        Index('idx_request_logs_user_timestamp', 'user_id', 'timestamp'),
        Index('idx_request_logs_path', 'path'),
    )

    def to_dict(self) -> dict[str, Any]:
        """Преобразует запись в словарь."""
        return {
            "id": self.id,
            "request_id": self.request_id,
            "user_id": self.user_id,
            "method": self.method,
            "path": self.path,
            "status_code": self.status_code,
            "request_body": self.request_body,
            "response_time_ms": self.response_time_ms,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class GenerationResult(Base):
    """Основная таблица для хранения результатов генерации README."""

    __tablename__ = "generation_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    user_id: Mapped[str | None] = mapped_column(String(100))

    seed_data: Mapped[Any] = mapped_column(JSON, nullable=True)
    markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_stats: Mapped[Any] = mapped_column(JSON, nullable=True)
    task_plan: Mapped[Any] = mapped_column(JSON, nullable=True)
    issues: Mapped[Any] = mapped_column(JSON, nullable=True)
    practice_critic_issues: Mapped[Any] = mapped_column(JSON, nullable=True)
    agent_config_versions: Mapped[Any] = mapped_column(JSON, nullable=True)
    flow_trace: Mapped[Any] = mapped_column(JSON, nullable=True)

    regenerated_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    regeneration_comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    regeneration_changes: Mapped[Any] = mapped_column(JSON, nullable=True)
    original_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False
    )

    rubric: Mapped["RubricResult | None"] = relationship(
        "RubricResult", back_populates="generation", uselist=False, cascade="all, delete-orphan"
    )
    report: Mapped["ReportResult | None"] = relationship(
        "ReportResult", back_populates="generation", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index('idx_gen_results_user_id', 'user_id'),
        Index('idx_gen_results_created_at', 'created_at'),
        Index('idx_gen_results_user_created', 'user_id', 'created_at'),
    )

    def to_dict(self) -> dict[str, Any]:
        """Преобразует запись в словарь."""
        return {
            "id": self.id,
            "request_id": self.request_id,
            "user_id": self.user_id,
            "seed_data": self.seed_data,
            "markdown": self.markdown,
            "text_stats": self.text_stats,
            "task_plan": self.task_plan,
            "issues": self.issues,
            "practice_critic_issues": self.practice_critic_issues,
            "agent_config_versions": self.agent_config_versions,
            "flow_trace": self.flow_trace,
            "regenerated_markdown": self.regenerated_markdown,
            "regeneration_comments": self.regeneration_comments,
            "regeneration_changes": self.regeneration_changes,
            "original_markdown": self.original_markdown,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class PausedGenerationSession(Base):
    """Durable pause/resume state for methodology human-in-the-loop gates."""

    __tablename__ = "paused_generation_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="needs_review")

    project_seed: Mapped[Any] = mapped_column(JSON, nullable=True)
    track_paths: Mapped[Any] = mapped_column(JSON, nullable=True)
    context_payload: Mapped[Any] = mapped_column(JSON, nullable=True)
    steps_payload: Mapped[Any] = mapped_column(JSON, nullable=True)
    resume_from_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    methodology: Mapped[Any] = mapped_column(JSON, nullable=True)
    review_actions: Mapped[Any] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False
    )

    __table_args__ = (
        Index('idx_paused_gen_user_id', 'user_id'),
        Index('idx_paused_gen_status', 'status'),
        Index('idx_paused_gen_created_at', 'created_at'),
    )

    def to_dict(self) -> dict[str, Any]:
        """Преобразует запись в словарь без разворачивания context payload."""
        return {
            "id": self.id,
            "request_id": self.request_id,
            "user_id": self.user_id,
            "status": self.status,
            "project_seed": self.project_seed,
            "track_paths": self.track_paths,
            "resume_from_index": self.resume_from_index,
            "methodology": self.methodology,
            "review_actions": self.review_actions or [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class GenerationWorkflowState(Base):
    """Durable state snapshot for one generation workflow."""

    __tablename__ = "generation_workflow_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    user_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="created")
    current_node: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_completed_node: Mapped[str | None] = mapped_column(String(120), nullable=True)
    resume_from_node: Mapped[str | None] = mapped_column(String(120), nullable=True)
    progress_current: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_data: Mapped[Any] = mapped_column(JSON, nullable=True)
    commands: Mapped[Any] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False
    )

    __table_args__ = (
        Index("idx_generation_workflow_user_status", "user_id", "status"),
        Index("idx_generation_workflow_updated", "updated_at"),
    )

    def to_dict(self) -> dict[str, Any]:
        """Return a detached workflow snapshot."""
        return {
            "id": self.id,
            "request_id": self.request_id,
            "user_id": self.user_id,
            "status": self.status,
            "current_node": self.current_node,
            "last_completed_node": self.last_completed_node,
            "resume_from_node": self.resume_from_node,
            "progress_current": self.progress_current,
            "progress_total": self.progress_total,
            "error": self.error,
            "metadata": self.meta_data or {},
            "commands": self.commands or [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class GenerationWorkflowCheckpoint(Base):
    """Durable checkpoint emitted by a single workflow node."""

    __tablename__ = "generation_workflow_checkpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    checkpoint_index: Mapped[int] = mapped_column(Integer, nullable=False)
    node_id: Mapped[str] = mapped_column(String(120), nullable=False)
    node_name: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_artifact: Mapped[Any] = mapped_column(JSON, nullable=True)
    context_snapshot: Mapped[Any] = mapped_column(JSON, nullable=True)
    validation_result: Mapped[Any] = mapped_column(JSON, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[Decimal | None] = mapped_column(Numeric(18, 3), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, nullable=False)

    __table_args__ = (
        UniqueConstraint("request_id", "checkpoint_index", name="uq_workflow_checkpoint_request_index"),
        Index("idx_workflow_checkpoint_request_node", "request_id", "node_id"),
        Index("idx_workflow_checkpoint_status", "status"),
    )

    def to_dict(self) -> dict[str, Any]:
        """Return a detached checkpoint payload."""
        return {
            "id": self.id,
            "request_id": self.request_id,
            "user_id": self.user_id,
            "checkpoint_index": self.checkpoint_index,
            "node_id": self.node_id,
            "node_name": self.node_name,
            "status": self.status,
            "input_hash": self.input_hash,
            "output_artifact": self.output_artifact or {},
            "context_snapshot": self.context_snapshot or {},
            "validation_result": self.validation_result or {},
            "retry_count": self.retry_count,
            "duration_ms": float(self.duration_ms) if self.duration_ms is not None else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ToolRun(Base):
    """Unified run metadata for tools mounted into the generator service."""

    __tablename__ = "tool_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    tool_name: Mapped[str] = mapped_column(String(40), nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    input_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[Any] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False
    )

    __table_args__ = (
        Index("idx_tool_runs_tool_status", "tool_name", "status"),
        Index("idx_tool_runs_user_created", "user_id", "created_at"),
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialize run metadata for APIs and dashboard cards."""

        return {
            "id": self.id,
            "run_id": self.run_id,
            "tool_name": self.tool_name,
            "user_id": self.user_id,
            "status": self.status,
            "input_ref": self.input_ref,
            "output_ref": self.output_ref,
            "summary": self.summary,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class UserRun(Base):
    """Unified per-user activity feed for product dashboard rows."""

    __tablename__ = "user_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    score: Mapped[Any] = mapped_column(JSON, nullable=True)
    result_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    meta_data: Mapped[Any] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False
    )

    __table_args__ = (
        Index("idx_user_runs_user_updated", "user_id", "updated_at"),
        Index("idx_user_runs_user_status", "user_id", "status"),
        Index("idx_user_runs_kind_status", "kind", "status"),
    )

    def to_dict(self) -> dict[str, Any]:
        """Return a detached dashboard-safe representation."""
        return {
            "id": self.id,
            "request_id": self.request_id,
            "user_id": self.user_id,
            "kind": self.kind,
            "status": self.status,
            "title": self.title,
            "score": self.score,
            "result_url": self.result_url,
            "metadata": self.meta_data,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class LLMUsageLedger(Base):
    """Aggregated LLM spend and token usage by user, run and pipeline node."""

    __tablename__ = "llm_usage_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    run_id: Mapped[str] = mapped_column(String(100), nullable=False)
    node: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str | None] = mapped_column(String(60), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    calls_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, default=0)
    route_data: Mapped[Any] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "run_id", "node", name="uq_llm_usage_user_run_node"),
        Index("idx_llm_usage_role_provider", "role", "provider"),
    )

    def to_dict(self) -> dict[str, Any]:
        """Return a detached, JSON-safe usage snapshot."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "run_id": self.run_id,
            "node": self.node,
            "role": self.role,
            "provider": self.provider,
            "model": self.model,
            "calls_count": self.calls_count,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": float(self.cost_usd or 0),
            "route": self.route_data,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class RubricResult(Base):
    """Таблица для хранения полных rubric.json."""

    __tablename__ = "rubric_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    generation_result_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("generation_results.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    rubric_data: Mapped[Any] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False
    )

    generation: Mapped["GenerationResult"] = relationship("GenerationResult", back_populates="rubric")

    def to_dict(self) -> dict[str, Any]:
        """Преобразует запись в словарь."""
        return {
            "id": self.id,
            "generation_result_id": self.generation_result_id,
            "rubric_data": self.rubric_data,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ReportResult(Base):
    """Таблица для хранения сокращённых report.json."""

    __tablename__ = "report_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    generation_result_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("generation_results.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    report_data: Mapped[Any] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now_naive, onupdate=utc_now_naive, nullable=False
    )

    generation: Mapped["GenerationResult"] = relationship("GenerationResult", back_populates="report")

    def to_dict(self) -> dict[str, Any]:
        """Преобразует запись в словарь."""
        return {
            "id": self.id,
            "generation_result_id": self.generation_result_id,
            "report_data": self.report_data,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
