"""Postgres-харнесс для тестов каталога (SQLite удалён — каталог живёт только в PG).

Стратегия изоляции — **schema-once + truncate-per-test**: session-фикстура один раз строит
тестовую БД `catalog_test` (`alembic upgrade head`), а function-фикстура `catalog_conn`
чистит все `catalog.*` через `TRUNCATE ... RESTART IDENTITY CASCADE` перед каждым тестом.
Код каталога коммитит внутри себя, поэтому rollback-per-test не годится.

Требуется живой Postgres (локальный Docker `content_generator_postgres`). Если БД недоступна —
весь пакет `tests/catalog` скипается с внятным сообщением (не блокируем остальной suite).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import pytest

_REPO = Path(__file__).resolve().parents[2]
_TEST_DB_NAME = "catalog_test"


def _base_url() -> str | None:
    """Базовый URL Postgres: CATALOG_TEST_DATABASE_URL или DATABASE_URL (localhost→127.0.0.1)."""
    from dotenv import load_dotenv

    load_dotenv(_REPO / ".env")  # pytest не грузит .env сам; берём тот же URL, что и приложение
    url = os.getenv("CATALOG_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        return None
    # Контейнер слушает только IPv4; localhost на Windows резолвится в ::1 первым (см. memory).
    return url.replace("localhost", "127.0.0.1")


def _with_dbname(url: str, dbname: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", parts.query, parts.fragment))


def _test_url() -> str | None:
    base = _base_url()
    return _with_dbname(base, _TEST_DB_NAME) if base else None


def _ensure_test_database(base_url: str) -> None:
    """Создать БД `catalog_test`, если её нет (CREATE DATABASE вне транзакции, autocommit)."""
    import psycopg2  # локальный импорт: только когда PG реально нужен

    # Подключаемся к исходной (maintenance) БД, чтобы выдать CREATE DATABASE.
    maint = psycopg2.connect(base_url)
    try:
        maint.autocommit = True
        with maint.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (_TEST_DB_NAME,))
            if cur.fetchone() is None:
                cur.execute(f'CREATE DATABASE "{_TEST_DB_NAME}"')
    finally:
        maint.close()


def _alembic_upgrade(test_url: str) -> None:
    """`alembic upgrade head` против тестовой БД (env DATABASE_URL перекрывает .env)."""
    env = dict(os.environ)
    env["DATABASE_URL"] = test_url  # session.py: load_dotenv(override=False) → наш env выигрывает
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=str(_REPO),
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")


@pytest.fixture(scope="session")
def catalog_pg() -> Any:
    """Один раз поднять тестовую PG-БД каталога и включить PG-backend на всю сессию."""
    base = _base_url()
    if not base:
        pytest.skip("Каталог требует Postgres: не задан DATABASE_URL/CATALOG_TEST_DATABASE_URL")

    test_url = _test_url()
    assert test_url
    try:
        _ensure_test_database(base)
        _alembic_upgrade(test_url)
    except Exception as exc:  # недоступный Docker/PG → скипаем пакет, не роняем весь suite
        pytest.skip(f"Postgres недоступен для тестов каталога: {exc}")

    prev = {k: os.environ.get(k) for k in ("CATALOG_DB", "CATALOG_DATABASE_URL")}
    os.environ["CATALOG_DB"] = "postgres"
    os.environ["CATALOG_DATABASE_URL"] = test_url
    try:
        yield test_url
    finally:
        for key, value in prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _truncate_all(conn: Any) -> None:
    """Очистить все таблицы схемы catalog одним TRUNCATE (RESTART IDENTITY, CASCADE)."""
    cur = conn.cursor()
    cur.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'catalog' ORDER BY tablename"
    )
    tables = [row[0] for row in cur.fetchall()]
    if tables:
        joined = ", ".join(f"catalog.{t}" for t in tables)
        cur.execute(f"TRUNCATE {joined} RESTART IDENTITY CASCADE")
    conn.commit()


@pytest.fixture()
def catalog_conn(catalog_pg: str) -> Any:
    """Чистое PG-подключение к каталогу (sqlite3-совместимая обёртка PgConnection)."""
    from content_factory.catalog.db import open_catalog_connection

    conn = open_catalog_connection("unused-on-postgres", check_same_thread=False)
    _truncate_all(conn)
    try:
        yield conn
    finally:
        conn.close()
