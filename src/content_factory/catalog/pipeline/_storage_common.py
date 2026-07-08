"""Shared leaf helpers for the catalog storage layer.

DB introspection (table/column existence, Postgres detection), catalog-key
normalization, ISO timestamps, and JSON/dict coercion. Extracted from
``catalog/pipeline/storage.py``; ``storage`` re-exports them so the many
``storage._foo`` call sites (and the domain sub-modules) stay unchanged.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

from content_factory.catalog.db import existing_columns, is_postgres_connection, table_exists
from content_factory.catalog.pipeline.models import SkillCandidate

_REVIEW_QUEUE_ENTITY_TYPE_MAP = {
    "skill": "skill",
    "competency_block": "block",
    "curriculum_section": "block",
}


def _existing_cols(con: Any, table: str) -> set[str]:
    return existing_columns(con, table)


def _table_exists(con: Any, table: str) -> bool:
    return table_exists(con, table)


def _supports_superseded(con: Any) -> bool:
    if is_postgres_connection(con):
        # PG-схема (alembic 016) всегда допускает decision='superseded'.
        return True
    row = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='skill_suggestion'").fetchone()
    return bool(row and row[0] and "superseded" in row[0])


def _quoted_columns(columns: list[str]) -> str:
    return ", ".join(f'"{column}"' for column in columns)


def _review_queue_entity_type(candidate: SkillCandidate) -> str:
    return _REVIEW_QUEUE_ENTITY_TYPE_MAP.get(candidate.entity_type, "block")


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_catalog_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower().strip()
    normalized = re.sub(r"[^0-9a-zа-яё+ ]", " ", normalized)
    return re.sub(r"\s+", " ", normalized)


def _slug_catalog_key(value: str) -> str:
    normalized = _normalize_catalog_key(value)
    slug = "-".join(part for part in normalized.split() if part)
    return slug or "item"


def _as_dict(value: Any) -> dict[str, Any]:
    """Return value when it is a dict, else an empty dict (JSON payload guard)."""
    return value if isinstance(value, dict) else {}


def _json_list(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []

