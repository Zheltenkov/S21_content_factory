"""Абстракция подключения каталога (Postgres-native, после полного cutover).

Единая фабрика `open_catalog_connection` (только Postgres) + диалектные хелперы и
sqlite3-совместимая обёртка `PgConnection`. `CatalogConnection`/`CatalogRow` — тип-алиасы
для аннотаций каталожного кода (заменили `sqlite3.Connection`/`sqlite3.Row`).
"""

from typing import Any

from .connection import (
    catalog_database_url,
    open_catalog_connection,
    resolve_backend,
)
from .dialect import (
    adapt_write_sql,
    is_pragma,
    translate_current_timestamp,
    translate_group_concat,
    translate_like,
    translate_placeholders,
)
from .introspection import column_exists, existing_columns, table_exists
from .pg_compat import PgConnection, PgCursor, Row, is_postgres_connection

# Тип-алиасы для аннотаций (каталог на Postgres; строки — Row из PgCursor).
CatalogConnection = Any
CatalogRow = Row

__all__ = [
    "CatalogConnection",
    "CatalogRow",
    "PgConnection",
    "PgCursor",
    "Row",
    "adapt_write_sql",
    "catalog_database_url",
    "column_exists",
    "existing_columns",
    "is_postgres_connection",
    "is_pragma",
    "open_catalog_connection",
    "resolve_backend",
    "table_exists",
    "translate_current_timestamp",
    "translate_group_concat",
    "translate_like",
    "translate_placeholders",
]
