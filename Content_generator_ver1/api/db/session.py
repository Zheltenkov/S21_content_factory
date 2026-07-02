"""Управление сессиями базы данных."""

import os
from collections.abc import Generator
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv
from fastapi import HTTPException, status
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()

# Получаем URL БД из переменных окружения
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError(
        "DATABASE_URL не установлен. Установите переменную окружения DATABASE_URL "
        "с URL подключения к PostgreSQL (например: postgresql://user:password@localhost:5432/dbname)"
    )

# Проверяем, что используется PostgreSQL
if not DATABASE_URL.startswith("postgresql://") and not DATABASE_URL.startswith("postgresql+psycopg2://"):
    raise ValueError(
        f"Поддерживается только PostgreSQL. DATABASE_URL должен начинаться с 'postgresql://'. "
        f"Получено: {DATABASE_URL[:20]}..."
    )

# Создаем движок
engine = create_engine(
    DATABASE_URL,
    echo=os.getenv("DB_ECHO", "false").lower() == "true"
)

# Создаем фабрику сессий
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

_database_available: bool | None = None
_database_error: str | None = None


def describe_database_target() -> str:
    """Return a password-safe DATABASE_URL summary for diagnostics."""
    try:
        parsed = urlsplit(DATABASE_URL)
        userinfo = f"{parsed.username}:***@" if parsed.username else ""
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        netloc = f"{userinfo}{host}{port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    except Exception:
        return "<invalid DATABASE_URL>"


def set_database_status(available: bool | None, error: Exception | str | None = None) -> None:
    """Update process-local database availability status."""
    global _database_available, _database_error
    _database_available = available
    _database_error = _format_database_error(error) if error else None


def is_database_available() -> bool | None:
    """Return last known database availability: True, False, or None if unknown."""
    return _database_available


def get_database_status() -> dict[str, str | bool | None]:
    """Return current DB status for health checks and diagnostics."""
    return {
        "available": _database_available,
        "error": _database_error,
        "target": describe_database_target(),
    }


def database_unavailable_detail() -> dict[str, str | bool | None]:
    """Build a client-safe API error payload for unavailable database."""
    return {
        "type": "DatabaseUnavailable",
        "message": "База данных недоступна. Проверьте DATABASE_URL и учетные данные PostgreSQL.",
        "target": describe_database_target(),
        "error": _database_error,
    }


def check_database_connection() -> None:
    """Validate database connectivity and update process-local status."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        set_database_status(True)
    except SQLAlchemyError as exc:
        set_database_status(False, exc)
        raise


def get_db_session() -> Generator[Session, None, None]:
    """
    Генератор сессий БД для использования в зависимостях FastAPI.
    
    Yields:
        Сессия БД
    """
    if _database_available is False:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=database_unavailable_detail(),
        )

    db = SessionLocal()
    try:
        yield db
    except SQLAlchemyError as exc:
        set_database_status(False, exc)
        raise
    finally:
        db.close()


def should_auto_create_tables() -> bool:
    """Return whether runtime metadata.create_all is allowed for this process."""
    explicit = os.getenv("DB_AUTO_CREATE_TABLES")
    if explicit is not None:
        return explicit.strip().lower() in {"1", "true", "yes", "on"}

    environment = os.getenv("APP_ENV", os.getenv("ENV", "")).strip().lower()
    if environment in {"dev", "development", "local", "test", "testing"}:
        return True

    return os.getenv("RELOAD", "false").strip().lower() == "true"


def init_db(auto_create: bool | None = None) -> None:
    """
    Validate database availability and optionally create tables in local/dev mode.

    Production schema changes must be applied through Alembic migrations. Runtime
    create_all is intentionally gated to prevent silent schema drift.
    """
    from .models import Base
    try:
        create_tables = should_auto_create_tables() if auto_create is None else auto_create
        if create_tables:
            Base.metadata.create_all(bind=engine)
        else:
            check_database_connection()
        set_database_status(True)
    except SQLAlchemyError as exc:
        set_database_status(False, exc)
        raise


def _format_database_error(error: Exception | str | None) -> str | None:
    """Keep DB errors compact enough for API/health responses."""
    if error is None:
        return None
    text_value = str(error).strip()
    if not text_value:
        return type(error).__name__ if isinstance(error, Exception) else None
    return text_value.splitlines()[0][:500]
