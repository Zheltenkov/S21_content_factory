"""Абстракция подключения каталога (SQLite→Postgres cutover, фундамент).

Единая фабрика `open_catalog_connection` + диалектные хелперы. По умолчанию SQLite (без
изменения поведения); Postgres — за флагом `CATALOG_DB=postgres`. Рервайринг call-sites и
флип поведения — следующие слайсы.
"""

from .connection import (
    catalog_database_url,
    open_catalog_connection,
    resolve_backend,
)
from .dialect import (
    adapt_write_sql,
    is_pragma,
    translate_group_concat,
    translate_like,
    translate_placeholders,
)
from .introspection import column_exists, existing_columns, table_exists
from .pg_compat import PgConnection, PgCursor, Row, is_postgres_connection

__all__ = [
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
    "translate_group_concat",
    "translate_like",
    "translate_placeholders",
]
