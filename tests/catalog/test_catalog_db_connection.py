"""Тесты фабрики подключения каталога (только Postgres; SQLite удалён)."""

import pytest

from content_factory.catalog.db.connection import (
    catalog_database_url,
    open_catalog_connection,
    resolve_backend,
)


def test_backend_is_always_postgres(monkeypatch) -> None:
    # SQLite-путь удалён — backend всегда postgres, даже если CATALOG_DB выставлен иначе.
    for value in ("", "sqlite", "postgres", "whatever"):
        monkeypatch.setenv("CATALOG_DB", value)
        assert resolve_backend() == "postgres"
    monkeypatch.delenv("CATALOG_DB", raising=False)
    assert resolve_backend() == "postgres"


def test_catalog_database_url_prefers_specific(monkeypatch) -> None:
    monkeypatch.setenv("CATALOG_DATABASE_URL", "postgresql://a/catalog")
    monkeypatch.setenv("DATABASE_URL", "postgresql://b/main")
    assert catalog_database_url() == "postgresql://a/catalog"


def test_catalog_database_url_falls_back_to_database_url(monkeypatch) -> None:
    monkeypatch.delenv("CATALOG_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://b/main")
    assert catalog_database_url() == "postgresql://b/main"


def test_postgres_backend_requires_url(monkeypatch) -> None:
    monkeypatch.delenv("CATALOG_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        open_catalog_connection()
