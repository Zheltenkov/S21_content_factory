"""Shared leaf helpers for the catalog viewer.

Pure/low-level utilities (JSON guards, DB fetch, schema introspection, request/form
parsing, datetime + parse/format helpers) extracted from ``viewer/app.py`` during its
decomposition. No dependency on domain logic — safe to import from every catalog module.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from email.parser import BytesParser
from email.policy import default as email_policy
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs

from content_factory.catalog.db import CatalogConnection, open_catalog_connection
from content_factory.catalog.db import existing_columns as _db_existing_columns
from content_factory.catalog.db import table_exists as _db_table_exists


def _as_dict(value: Any) -> dict[str, Any]:
    """Return value when it is a dict, else an empty dict (JSON payload guard)."""
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    """Return value when it is a list, else an empty list (JSON payload guard)."""
    return value if isinstance(value, list) else []


def normalize_search_text(value: object | None) -> str:
    if value is None:
        return ""
    return " ".join(str(value).casefold().replace("ё", "е").split())


def normalize_catalog_key(value: object | None) -> str:
    if value is None:
        return ""
    text = str(value).casefold().replace("ё", "е")
    normalized = "".join(char if char.isalnum() or char in {"+", " "} else " " for char in text)
    return " ".join(normalized.split())


def fetch_one(conn: CatalogConnection, query: str, params: tuple = ()) -> dict[str, Any] | None:
    row = conn.execute(query, params).fetchone()
    return dict(row) if row else None


def fetch_all(conn: CatalogConnection, query: str, params: tuple = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(query, params)]


def table_exists(conn: Any, table_name: str) -> bool:
    return _db_table_exists(conn, table_name)


def column_exists(conn: Any, table_name: str, column_name: str) -> bool:
    return column_name in _db_existing_columns(conn, table_name)


def table_columns(conn: Any, table_name: str) -> set[str]:
    return _db_existing_columns(conn, table_name)


@dataclass
class UploadedFile:
    filename: str
    content_type: str
    data: bytes


def _read_request_body(environ: dict[str, Any]) -> bytes:
    content_length = int(environ.get("CONTENT_LENGTH") or 0)
    if content_length <= 0:
        return b""
    return cast(bytes, environ["wsgi.input"].read(content_length))


def parse_multipart_form_data(raw_body: bytes, content_type: str) -> tuple[dict[str, str], dict[str, UploadedFile]]:
    header_blob = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
    message = BytesParser(policy=email_policy).parsebytes(header_blob + raw_body)
    form_data: dict[str, str] = {}
    files: dict[str, UploadedFile] = {}

    for part in message.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        field_name = part.get_param("name", header="content-disposition")
        if not isinstance(field_name, str):
            continue

        raw_payload = part.get_payload(decode=True)
        payload = raw_payload if isinstance(raw_payload, bytes) else b""
        filename = part.get_filename()
        if filename:
            if payload:
                files[field_name] = UploadedFile(
                    filename=filename,
                    content_type=part.get_content_type(),
                    data=payload,
                )
            continue

        charset = part.get_content_charset() or "utf-8"
        form_data[field_name] = payload.decode(charset, errors="replace")

    return form_data, files


def parse_post_form_and_files(environ: dict[str, Any]) -> tuple[dict[str, str], dict[str, UploadedFile]]:
    raw_body = _read_request_body(environ)
    if not raw_body:
        return {}, {}

    content_type = environ.get("CONTENT_TYPE", "")
    if content_type.casefold().startswith("multipart/form-data"):
        return parse_multipart_form_data(raw_body, content_type)

    parsed = parse_qs(raw_body.decode("utf-8"), keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}, {}


def parse_post_data(environ: dict[str, Any]) -> dict[str, str]:
    form_data, _files = parse_post_form_and_files(environ)
    return form_data


def parse_path_int(path: str, prefix: str, suffix: str = "") -> int | None:
    if not path.startswith(prefix) or (suffix and not path.endswith(suffix)):
        return None
    end = -len(suffix) if suffix else None
    try:
        return int(path[len(prefix) : end])
    except ValueError:
        return None


def clean_profile_name(value: str | None) -> str:
    cleaned = str(value or "").strip()
    cleaned = re.sub(r"[_\s-]*warning$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"_{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or str(value or "").strip()


def clean_profile_slug(value: str | None) -> str:
    cleaned = str(value or "").strip()
    cleaned = re.sub(r"[_-]*warning$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"_{2,}", "-", cleaned)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned or str(value or "").strip()


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def parse_iso_datetime(value: object | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def format_local_datetime(value: object | None) -> str:
    parsed = parse_iso_datetime(value)
    if not parsed:
        return str(value or "")
    return parsed.strftime("%d.%m.%Y %H:%M")


def parse_optional_float(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = value.strip().replace(",", ".")
    if not cleaned:
        return None
    return float(cleaned)


def parse_optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return int(cleaned)


def parse_brief_id(source_ref: str | None) -> int | None:
    if not source_ref or not source_ref.startswith("brief:"):
        return None
    tail = source_ref.split(":", 2)[1]
    try:
        return int(tail)
    except ValueError:
        return None


def extract_quoted_name(details: str | None) -> str | None:
    if not details or details.lstrip().startswith("{"):
        return None
    start = details.find("«")
    end = details.find("»", start + 1) if start >= 0 else -1
    if start < 0 or end < 0:
        return None
    return details[start + 1:end].strip() or None


def format_percent(value: object | None) -> str | None:
    if value is None:
        return None
    try:
        number = float(cast("str | float", value))
    except (TypeError, ValueError):
        return None
    if number <= 1:
        number *= 100
    return f"{number:.0f}%"


def slugify(value: str) -> str:
    lowered = value.casefold().replace("ё", "е")
    lowered = "-".join(part for part in "".join(ch if ch.isalnum() else "-" for ch in lowered).split("-") if part)
    return lowered or "item"


def format_catalog_similarity(score: float | int | None) -> tuple[str | None, str | None]:
    """Return UI-ready catalog similarity and novelty scores on a 0..100 scale."""
    if score is None:
        return None, None
    bounded_score = max(0.0, min(100.0, float(score)))
    return f"{bounded_score:.2f}", f"{100.0 - bounded_score:.2f}"


def load_summary(summary_path: Path) -> dict[str, Any]:
    if not summary_path.exists():
        return {}
    try:
        loaded = json.loads(summary_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def refresh_summary_counts(summary: dict[str, Any], db_path: Path) -> dict[str, Any]:
    refreshed = dict(summary or {})
    counts = dict(refreshed.get("counts") or {})
    try:
        conn = open_catalog_connection(db_path)
        counts.update(
            {
                "profiles": int(conn.execute("SELECT COUNT(*) FROM profile").fetchone()[0]) if table_exists(conn, "profile") else counts.get("profiles", 0),
                "competencies": (
                    int(conn.execute("SELECT COUNT(*) FROM competency").fetchone()[0])
                    if table_exists(conn, "competency")
                    else (
                        int(conn.execute("SELECT COUNT(*) FROM profile_competency").fetchone()[0])
                        if table_exists(conn, "profile_competency")
                        else counts.get("competencies", 0)
                    )
                ),
                "skills": int(conn.execute("SELECT COUNT(*) FROM skill WHERE status = 'active'").fetchone()[0]) if table_exists(conn, "skill") else counts.get("skills", 0),
                "indicator_rows": int(conn.execute("SELECT COUNT(*) FROM indicator_row").fetchone()[0]) if table_exists(conn, "indicator_row") else counts.get("indicator_rows", 0),
                "open_reviews": int(conn.execute("SELECT COUNT(*) FROM review_queue WHERE status = 'open'").fetchone()[0]) if table_exists(conn, "review_queue") else counts.get("open_reviews", 0),
            }
        )
        conn.close()
    except Exception:
        # Summary counts — best-effort; degrade gracefully on either backend.
        pass
    refreshed["counts"] = counts
    return refreshed


def normalize_competency_title(value: object | None) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("ё", "е")
    text = re.sub(r"[^0-9a-zа-я+]+", " ", text)
    return " ".join(text.split())


def display_catalog_title(value: object | None) -> str:
    text = str(value or "").strip()
    return text[:1].upper() + text[1:] if text else ""
