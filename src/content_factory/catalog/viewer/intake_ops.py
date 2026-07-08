"""Intake pipeline hub — job orchestration, DAG build, brief text, review queue.

Extracted from ``viewer/app.py`` (slice 6, the final domain slice). The intake hub:
create/queue/execute/update intake jobs (background ThreadPoolExecutor), the
brief -> catalog -> DAG -> curriculum pipeline stages, brief-text extraction
(txt/csv/docx), DAG build + apply-decision flows, the review queue (skill /
prerequisite-edge reviews) and its formatting, plus intake workspace/workflow state.
Consumed by ``catalog/web/routers/intake.py`` and ``reviews.py`` (and ``up.py`` via
``build_curriculum_plan_for_brief``). Depends on shared helpers in ``_common`` / ``labels``
/ ``observability`` / ``curriculum_ops`` + stdlib; all pipeline module aliases are local
imports inside the bodies. Owns the intake runtime-state globals. No back-import of
``app.py`` (module stays acyclic); ``app.py`` re-exports every symbol for the routers.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import threading
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from content_factory.catalog.db import (
    CatalogConnection,
    CatalogRow,
    catalog_database_url,
    open_catalog_connection,
    resolve_backend,
)
from content_factory.catalog.viewer._common import (
    UploadedFile,
    _as_dict,
    column_exists,
    extract_quoted_name,
    fetch_all,
    format_catalog_similarity,
    format_percent,
    parse_brief_id,
    parse_optional_float,
    table_columns,
    table_exists,
    utc_now_iso,
)
from content_factory.catalog.viewer.curriculum_ops import (
    build_deferred_curriculum_plan_payload,
    update_jobs_curriculum_plan_payload,
)
from content_factory.catalog.viewer.intake_worker import (
    HEARTBEAT_INTERVAL_SECONDS,
    claim_intake_job,
    heartbeat_intake_job,
    reclaim_expired_intake_jobs,
    release_intake_job,
    worker_identity,
)
from content_factory.catalog.viewer.labels import (
    intake_job_status_label,
    intake_stage_label,
    review_entity_label,
    review_reason_label,
    review_severity_label,
    review_status_label,
    review_text_label,
)
from content_factory.catalog.viewer.observability import build_decision_rationale

if TYPE_CHECKING:
    from content_factory.catalog.pipeline.models import Evidence, SkillCandidate


INTAKE_SCHEMA_READY: set[str] = set()
INTAKE_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="intake")
INTAKE_STALE_TIMEOUT_SECONDS = 180


def _intake_schema_ready_key(db_path: Path) -> str:
    """Return the backend-specific identity used for one-time intake repairs."""

    backend = resolve_backend()
    if backend == "postgres":
        url = catalog_database_url() or ""
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest() if url else "missing-url"
        return f"postgres:{digest}"
    return f"{backend}:{db_path.resolve()}"


def _reason_set(reasons: list[str] | tuple[str, ...] | str | None) -> set[str]:
    if reasons is None:
        return set()
    if isinstance(reasons, str):
        parts = {part.strip() for part in re.split(r"[,;]\s*", reasons) if part.strip()}
        lowered = reasons.casefold()
        if "подозрительный match" in lowered or "catalog_match_suspicious" in lowered:
            parts.add("catalog_match_suspicious")
        return parts
    return {str(reason).strip() for reason in reasons if str(reason).strip()}


def build_similarity_hint(
    score: float | int | None,
    resolution: str | None,
    has_nearest: bool,
    reasons: list[str] | tuple[str, ...] | str | None = None,
) -> dict[str, str]:
    """Explain how a catalog similarity score should be interpreted."""
    reason_set = _reason_set(reasons)
    if "catalog_match_suspicious" in reason_set:
        return {
            "label": "Подозрительный матч",
            "class": "weak",
            "recommendation": "Не используйте canonical skill автоматически. Нужно проверить смысл, группу и индикаторы.",
        }
    try:
        bounded_score = None if score is None else max(0.0, min(100.0, float(score)))
    except (TypeError, ValueError):
        bounded_score = None
    if bounded_score is None:
        return {
            "label": "Нет данных",
            "class": "neutral",
            "recommendation": "Нет ближайшего совпадения для методологической сверки.",
        }
    normalized_resolution = str(resolution or "").casefold()
    if normalized_resolution in {"matched", "alias"}:
        return {
            "label": "Покрывает",
            "class": "strong",
            "recommendation": "Кандидат уже покрыт существующим skill. Используйте canonical skill в DAG.",
        }
    if normalized_resolution == "fuzzy" or bounded_score >= 90.0:
        return {
            "label": "Почти эквивалент",
            "class": "strong",
            "recommendation": "Лучше привязать к существующему skill, если индикаторы покрывают смысл брифа.",
        }
    if has_nearest and bounded_score >= 75.0:
        return {
            "label": "Частично похоже",
            "class": "medium",
            "recommendation": "Проверьте индикаторы ближайшего skill: если они покрывают требование, используйте привязку.",
        }
    if has_nearest:
        return {
            "label": "Слабое совпадение",
            "class": "weak",
            "recommendation": "Не привязывайте автоматически. Обычно это новый skill или кандидат на отклонение.",
        }
    return {
        "label": "Новое",
        "class": "neutral",
        "recommendation": "Похожего skill не найдено. Решение: добавить новый или отклонить как нерелевантный.",
    }


def build_candidate_recommended_action(
    score: float | int | None,
    resolution: str | None,
    has_nearest: bool,
    nearest_name: str | None = None,
    reasons: list[str] | tuple[str, ...] | str | None = None,
    decision: str | None = None,
) -> dict[str, str]:
    """Return deterministic methodologist action for a resolved candidate."""
    normalized_decision = str(decision or "").casefold()
    normalized_resolution = str(resolution or "").casefold()
    reason_set = _reason_set(reasons)
    target = str(nearest_name or "").strip()
    try:
        bounded_score = None if score is None else max(0.0, min(100.0, float(score)))
    except (TypeError, ValueError):
        bounded_score = None

    if normalized_decision == "accepted":
        return {
            "code": "done",
            "label": "Уже принято",
            "target": target,
            "detail": "Кандидат используется в каталоге/DAG.",
        }
    if normalized_decision == "rejected":
        return {
            "code": "rejected",
            "label": "Отклонено",
            "target": "",
            "detail": "Кандидат не используется для покрытия брифа.",
        }
    if "catalog_match_suspicious" in reason_set:
        return {
            "code": "check",
            "label": "Проверить match",
            "target": target,
            "detail": "Есть риск ложного совпадения: группа, смысл или coverage area конфликтуют.",
        }
    if has_nearest and normalized_resolution in {"matched", "alias", "fuzzy"}:
        return {
            "code": "link",
            "label": "Покрыть существующим",
            "target": target,
            "detail": "Проверьте индикаторы nearest skill и привяжите, если смысл закрыт.",
        }
    if has_nearest and bounded_score is not None and bounded_score >= 75.0:
        return {
            "code": "link",
            "label": "Вероятно покрыть существующим",
            "target": target,
            "detail": "Похожесть высокая: сначала проверьте ближайший skill, потом решайте про новый.",
        }
    if normalized_resolution == "new" or not has_nearest:
        return {
            "code": "create",
            "label": "Создать новый skill",
            "target": "",
            "detail": "Похожего покрытия нет или оно слишком слабое.",
        }
    return {
        "code": "review",
        "label": "Оставить на review",
        "target": target,
        "detail": "Недостаточно данных для безопасного автодействия.",
    }


def load_nearest_skill_preview(conn: CatalogConnection, skill_id: int | None, indicator_limit: int = 3) -> dict[str, Any] | None:
    """Load a compact catalog preview for the nearest matched skill."""
    if not skill_id or not table_exists(conn, "skill"):
        return None
    skill_cols = table_columns(conn, "skill")
    if "name" in skill_cols and "canonical_name" in skill_cols:
        name_expr = "COALESCE(s.name, s.canonical_name)"
    elif "canonical_name" in skill_cols:
        name_expr = "s.canonical_name"
    elif "name" in skill_cols:
        name_expr = "s.name"
    else:
        name_expr = "s.normalized_name"
    canonical_expr = "s.canonical_name" if "canonical_name" in skill_cols else name_expr
    has_skill_group = table_exists(conn, "skill_group") and "group_id" in skill_cols
    if has_skill_group:
        row = conn.execute(
            f"""
            SELECT s.id, {name_expr} AS name, {canonical_expr} AS canonical_name, sg.name AS group_name
            FROM skill s
            LEFT JOIN skill_group sg ON sg.id = s.group_id
            WHERE s.id = ?
            """,
            (skill_id,),
        ).fetchone()
    else:
        row = conn.execute(
            f"""
            SELECT s.id, {name_expr} AS name, {canonical_expr} AS canonical_name, NULL AS group_name
            FROM skill s
            WHERE s.id = ?
            """,
            (skill_id,),
        ).fetchone()
    if not row:
        return None
    preview = {
        "id": int(row["id"]),
        "name": row["canonical_name"] or row["name"],
        "group": row["group_name"],
        "indicators": [],
    }
    if table_exists(conn, "indicator"):
        indicator_cols = table_columns(conn, "indicator")
        text_col = "text" if "text" in indicator_cols else None
        if text_col:
            select_cols = ["id", text_col]
            if "indicator_type" in indicator_cols:
                select_cols.append("indicator_type")
            if "complexity_label" in indicator_cols:
                select_cols.append("complexity_label")
            if "complexity_band" in indicator_cols:
                select_cols.append("complexity_band")
            order_sql = "sort_order, id" if "sort_order" in indicator_cols else "id"
            active_filter = "AND COALESCE(is_active, 1) = 1" if "is_active" in indicator_cols else ""
            rows = conn.execute(
                f"""
                SELECT {', '.join(select_cols)}
                FROM indicator
                WHERE skill_id = ?
                {active_filter}
                ORDER BY {order_sql}
                LIMIT ?
                """,
                (skill_id, indicator_limit),
            ).fetchall()
            preview["indicators"] = [
                {
                    "text": str(indicator[text_col] or ""),
                    "type": str(indicator["indicator_type"] or "") if "indicator_type" in indicator.keys() else "",
                    "complexity": (
                        str(indicator["complexity_label"] or "")
                        if "complexity_label" in indicator.keys()
                        else str(indicator["complexity_band"] or "") if "complexity_band" in indicator.keys() else ""
                    ),
                }
                for indicator in rows
                if str(indicator[text_col] or "").strip()
            ]
    return preview


def _ensure_intake_review_schema(conn: CatalogConnection, db_path: Path) -> None:
    """One-time-per-database review-link repair (schema readiness only).

    Kept separate from the recovery path so the worker can call it without
    triggering job re-dispatch (which would recurse)."""
    ready_key = _intake_schema_ready_key(db_path)
    if ready_key not in INTAKE_SCHEMA_READY:
        repair_intake_review_links(conn)
        INTAKE_SCHEMA_READY.add(ready_key)


def _dispatch_pending_intake_jobs(conn: CatalogConnection, db_path: Path) -> None:
    """Submit pending jobs to the executor — those reclaimed after a restart, or
    created but not yet picked up. Idempotent: ``claim_intake_job`` gates double-run,
    so a redundant submit is a fast no-op.

    Intentionally NOT called per-request: a read page-load must not kick off intake
    pipelines, and per-request submits would bypass tests that stub ``queue_intake_job``.
    Wired into a background recovery poller in slice 3."""
    if not table_exists(conn, "intake_job"):
        return
    rows = conn.execute(
        "SELECT id FROM intake_job WHERE status = 'pending' ORDER BY created_at LIMIT 20"
    ).fetchall()
    for row in rows:
        INTAKE_EXECUTOR.submit(execute_intake_job, db_path, int(row["id"]))


def ensure_intake_runtime_schema(conn: CatalogConnection, db_path: Path) -> None:
    """Per-request intake pre-flight (web): schema readiness + crashed-job recovery.

    The catalog schema is Postgres/alembic-managed, so this runs the runtime repairs:
    a one-time review-link repair per database, plus reclaim of jobs whose worker lease
    expired (crash / app restart) — requeued for retry while attempts remain, else
    failed, so in-flight work is never silently lost. The worker itself calls only
    ``_ensure_intake_review_schema`` (no reclaim on every progress tick)."""
    _ensure_intake_review_schema(conn, db_path)
    repair_stale_intake_jobs(conn)


def prune_empty_generated_catalog_nodes(conn: CatalogConnection) -> dict[str, int]:
    """Remove empty generated taxonomy nodes; keep manual empty nodes editable."""
    stats: dict[str, int] = {}
    if table_exists(conn, "skill_set") and table_exists(conn, "skill_set_item"):
        stats["skill_set_orphan_items"] = conn.execute(
            """
            DELETE FROM skill_set_item
            WHERE skill_set_id NOT IN (SELECT id FROM skill_set)
               OR skill_id NOT IN (SELECT id FROM skill)
            """
        ).rowcount
        stats["skill_set_empty_archived"] = conn.execute(
            """
            UPDATE skill_set
            SET status = 'archived',
                updated_at = ?
            WHERE status != 'archived'
              AND NOT EXISTS (
                  SELECT 1
                  FROM skill_set_item ssi
                  WHERE ssi.skill_set_id = skill_set.id
              )
            """,
            (datetime.now(UTC).isoformat(),),
        ).rowcount

    if table_exists(conn, "skill_group") and table_exists(conn, "skill"):
        stats["skill_group_empty_generated_archived"] = conn.execute(
            """
            UPDATE skill_group
            SET status = 'deprecated',
                updated_at = ?
            WHERE COALESCE(source, '') IN ('derived', 'live_snapshot', 'intake_accept')
              AND status != 'deprecated'
              AND NOT EXISTS (
                  SELECT 1
                  FROM skill s_active
                  WHERE s_active.group_id = skill_group.id
                    AND COALESCE(s_active.is_active, 1) = 1
              )
              AND EXISTS (
                  SELECT 1
                  FROM skill s_any
                  WHERE s_any.group_id = skill_group.id
              )
            """,
            (datetime.now(UTC).isoformat(),),
        ).rowcount
        stats["skill_group_empty_generated_deleted"] = conn.execute(
            """
            DELETE FROM skill_group
            WHERE COALESCE(source, '') IN ('derived', 'live_snapshot', 'intake_accept')
              AND NOT EXISTS (
                  SELECT 1
                  FROM skill s
                  WHERE s.group_id = skill_group.id
              )
            """
        ).rowcount

    if table_exists(conn, "competency") and table_exists(conn, "profile_competency") and table_exists(conn, "competency_skill"):
        stats["profile_competency_empty_deleted"] = conn.execute(
            """
            DELETE FROM profile_competency
            WHERE NOT EXISTS (
                SELECT 1
                FROM competency_skill cs
                WHERE cs.profile_competency_id = profile_competency.id
            )
            AND (
                title_in_source IS NULL
                OR title_in_source = ''
                OR title_in_source = (
                    SELECT title FROM competency c WHERE c.id = profile_competency.competency_id
                )
            )
            """
        ).rowcount
    conn.commit()
    return stats


def repair_stale_intake_jobs(conn: CatalogConnection, stale_after_seconds: int = INTAKE_STALE_TIMEOUT_SECONDS) -> int:
    """Recover intake jobs whose worker lease expired (crash / app restart).

    Requeues them for retry while attempts remain, else fails them — so in-flight
    work survives a restart instead of being silently dropped. Lease-based (see
    ``intake_worker``); supersedes the old in-memory active-set + fixed-timeout
    heuristic. ``stale_after_seconds`` is kept for signature/backward compatibility.
    """
    if not table_exists(conn, "intake_job"):
        return 0
    result = reclaim_expired_intake_jobs(conn)
    return result["requeued"] + result["failed"]


def create_intake_job(
    conn: CatalogConnection,
    *,
    source_kind: str,
    source_name: str | None,
    file_path: str | None,
    brief_text: str,
    use_council: bool,
) -> int:
    current_time = utc_now_iso()
    cursor = conn.execute(
        """
        INSERT INTO intake_job(
            source_kind,
            source_name,
            file_path,
            brief_text,
            status,
            current_stage,
            progress_note,
            use_council,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, 'pending', 'queued', 'Задача поставлена в очередь на обработку.', ?, ?, ?)
        """,
        (source_kind, source_name, file_path, brief_text, 1 if use_council else 0, current_time, current_time),
    )
    conn.commit()
    return int(cursor.lastrowid or 0)


def update_intake_job(
    conn: CatalogConnection,
    job_id: int,
    *,
    status: str | None = None,
    current_stage: str | None = None,
    progress_note: str | None = None,
    error_text: str | None = None,
    result_payload: dict[str, Any] | None = None,
    mark_started: bool = False,
    mark_finished: bool = False,
) -> None:
    fields: list[str] = ["updated_at = ?"]
    params: list[object] = [utc_now_iso()]

    if status is not None:
        fields.append("status = ?")
        params.append(status)
    if current_stage is not None:
        fields.append("current_stage = ?")
        params.append(current_stage)
    if progress_note is not None:
        fields.append("progress_note = ?")
        params.append(progress_note)
    if error_text is not None:
        fields.append("error_text = ?")
        params.append(error_text)
    if result_payload is not None:
        fields.append("result_payload = ?")
        params.append(json.dumps(result_payload, ensure_ascii=False))
    if mark_started:
        fields.append("started_at = ?")
        params.append(utc_now_iso())
    if mark_finished:
        fields.append("finished_at = ?")
        params.append(utc_now_iso())

    params.append(job_id)
    conn.execute(f"UPDATE intake_job SET {', '.join(fields)} WHERE id = ?", tuple(params))
    conn.commit()


def get_intake_job(conn: CatalogConnection, job_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM intake_job WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return None
    job = dict(row)
    if job.get("result_payload"):
        try:
            job["result_payload"] = json.loads(job["result_payload"])
        except json.JSONDecodeError:
            job["result_payload"] = None
    job["status_label"] = intake_job_status_label(str(job.get("status")))
    job["current_stage_label"] = intake_stage_label(str(job.get("current_stage")))
    return job


def get_intake_job_brief_id(conn: CatalogConnection, job_id: int) -> tuple[dict[str, Any] | None, int | None]:
    job = get_intake_job(conn, job_id)
    payload = job.get("result_payload") if job else None
    brief_id = payload.get("brief_id") if isinstance(payload, dict) else None
    return job, brief_id if isinstance(brief_id, int) else None


def list_recent_intake_jobs(conn: CatalogConnection, limit: int = 8) -> list[dict[str, Any]]:
    items = fetch_all(
        conn,
        """
        SELECT
            id,
            source_kind,
            source_name,
            status,
            current_stage,
            use_council,
            created_at,
            finished_at
        FROM intake_job
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
    for item in items:
        item["status_label"] = intake_job_status_label(str(item.get("status")))
        item["current_stage_label"] = intake_stage_label(str(item.get("current_stage")))
    return items


def repair_intake_review_links(conn: CatalogConnection) -> int:
    if not table_exists(conn, "review_queue") or not table_exists(conn, "skill_suggestion"):
        return 0

    updated = 0
    rows = conn.execute(
        """
        SELECT id, source_ref, details
        FROM review_queue
        WHERE entity_id IS NULL
          AND source_ref LIKE 'brief:%'
        ORDER BY id
        """
    ).fetchall()
    for row in rows:
        brief_id = parse_brief_id(row["source_ref"])
        suggestion_name = extract_quoted_name(row["details"])
        if brief_id is None or not suggestion_name:
            continue
        match_rows = conn.execute(
            """
            SELECT id
            FROM skill_suggestion
            WHERE brief_id = ? AND suggested_name = ?
            ORDER BY id
            """,
            (brief_id, suggestion_name),
        ).fetchall()
        if len(match_rows) != 1:
            continue
        conn.execute("UPDATE review_queue SET entity_id = ? WHERE id = ?", (match_rows[0]["id"], row["id"]))
        updated += 1
    if updated:
        conn.commit()
    return updated


def get_latest_job_id_for_brief(conn: CatalogConnection, brief_id: int) -> int | None:
    row = conn.execute(
        """
        SELECT id
        FROM intake_job
        WHERE json_valid(result_payload)
          AND CAST(json_extract(result_payload, '$.brief_id') AS INTEGER) = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (brief_id,),
    ).fetchone()
    return int(row["id"]) if row else None


def get_brief_dag_state(conn: CatalogConnection, brief_id: int) -> dict[str, Any]:
    accepted_atomic = conn.execute(
        """
        SELECT COUNT(*)
        FROM skill_suggestion
        WHERE brief_id = ?
          AND entity_type = 'skill'
          AND atomicity = 'atomic'
          AND decision = 'accepted'
        """,
        (brief_id,),
    ).fetchone()[0]
    pending_atomic = conn.execute(
        """
        SELECT COUNT(*)
        FROM skill_suggestion
        WHERE brief_id = ?
          AND entity_type = 'skill'
          AND atomicity = 'atomic'
          AND decision = 'needs_review'
        """,
        (brief_id,),
    ).fetchone()[0]
    open_reviews = conn.execute(
        """
        SELECT COUNT(*)
        FROM review_queue
        WHERE source_ref = ?
          AND entity_type = 'skill'
          AND status = 'open'
          AND NOT (
              json_valid(details)
              AND json_extract(details, '$.review_kind') = 'prerequisite_edge'
          )
        """,
        (f"brief:{brief_id}",),
    ).fetchone()[0]
    prerequisite_rows = conn.execute(
        "SELECT COUNT(*) FROM skill_prerequisite WHERE brief_id = ?",
        (brief_id,),
    ).fetchone()[0] if table_exists(conn, "skill_prerequisite") and column_exists(conn, "skill_prerequisite", "brief_id") else 0
    brief_row = conn.execute(
        "SELECT role, domain FROM profile_brief WHERE id = ?",
        (brief_id,),
    ).fetchone()
    return {
        "brief_id": brief_id,
        "role": brief_row["role"] if brief_row else None,
        "domain": brief_row["domain"] if brief_row else None,
        "latest_job_id": get_latest_job_id_for_brief(conn, brief_id),
        "accepted_atomic_count": int(accepted_atomic),
        "pending_atomic_count": int(pending_atomic),
        "open_review_count": int(open_reviews),
        "prerequisite_count": int(prerequisite_rows),
    }


def load_prerequisite_edge_decisions(conn: CatalogConnection, brief_id: int) -> dict[str, str]:
    if not table_exists(conn, "prerequisite_edge_decision"):
        return {}
    return {
        str(row["edge_key"]): str(row["decision"])
        for row in conn.execute(
            """
            SELECT edge_key, decision
            FROM prerequisite_edge_decision
            WHERE brief_id = ?
            """,
            (brief_id,),
        )
    }


def parse_review_details_json(details: str | None) -> dict[str, Any]:
    if not details:
        return {}
    try:
        data = json.loads(details)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def split_review_reason_codes(reason_code: str | None, details: dict[str, Any] | None = None) -> list[str]:
    codes: list[str] = []
    raw_reasons = (details or {}).get("reasons")
    if isinstance(raw_reasons, list):
        codes.extend(str(item).strip() for item in raw_reasons if str(item).strip())
    if reason_code:
        codes.extend(part.strip() for part in str(reason_code).split(",") if part.strip())
    seen: set[str] = set()
    unique_codes: list[str] = []
    for code in codes:
        if code not in seen:
            seen.add(code)
            unique_codes.append(code)
    return unique_codes


def split_edge_label(edge_label: object | None) -> tuple[str, str]:
    text = str(edge_label or "").strip()
    if " -> " in text:
        src, dst = text.split(" -> ", 1)
        return src.strip(), dst.strip()
    if "→" in text:
        src, dst = text.split("→", 1)
        return src.strip(), dst.strip()
    return text or "первый навык", "следующий навык"


def format_prerequisite_edge_review(item: dict[str, Any]) -> None:
    details = parse_review_details_json(str(item.get("details") or ""))
    if details.get("review_kind") != "prerequisite_edge":
        item["display_reason"] = review_text_label(item.get("reason_label"))
        item["display_check"] = review_text_label(item.get("details"))
        return

    src_name, dst_name = split_edge_label(details.get("edge_label"))
    reason_codes = split_review_reason_codes(str(item.get("reason_code") or ""), details)
    reason_labels = [review_reason_label(code) for code in reason_codes]
    confidence = format_percent(details.get("confidence"))
    relation_type = str(details.get("relation_type") or "").strip()

    item["display_reason"] = "; ".join(reason_labels) if reason_labels else "Связь требует методологической проверки"

    notes: list[str] = [
        f"Проверяемая связь: «{src_name}» должен быть изучен до «{dst_name}».",
    ]
    if confidence:
        notes.append(f"Уверенность системы: {confidence}.")
    if relation_type == "soft":
        notes.append("Тип связи: мягкая методическая связь. Она не считается обязательной, пока методолог её не подтвердит.")
    if "bloom_direction" in reason_codes:
        notes.append("Причина проверки: возможен спорный порядок по уровню сложности. Проверьте, не должен ли второй навык идти раньше первого.")
    if "ai_proposed" in reason_codes:
        notes.append("Причина проверки: связь предложена автоматически, поэтому её нельзя использовать в рабочем графе без подтверждения.")
    notes.append("Что решить: подтвердить связь, если первый навык действительно нужен как основа для второго; отклонить, если порядок неверный или связь только тематическая.")
    item["display_check"] = "\n".join(notes)


def save_prerequisite_edge_decision(
    conn: CatalogConnection,
    *,
    brief_id: int,
    details: dict[str, Any],
    decision: str,
    resolution_note: str,
) -> None:
    if not table_exists(conn, "prerequisite_edge_decision"):
        return
    edge_key = str(details.get("edge_key") or "").strip()
    if "->" not in edge_key:
        return
    src_raw, dst_raw = edge_key.split("->", 1)

    def suggestion_id(raw: object) -> int | None:
        value = str(raw or "").strip()
        if value.startswith("S") and value[1:].isdigit():
            return int(value[1:])
        if value.isdigit():
            return int(value)
        return None

    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO prerequisite_edge_decision(
            brief_id, edge_key, src_suggestion_id, dst_suggestion_id,
            relation_type, confidence, source, decision, resolution_note, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(brief_id, edge_key) DO UPDATE SET
            src_suggestion_id = excluded.src_suggestion_id,
            dst_suggestion_id = excluded.dst_suggestion_id,
            relation_type = excluded.relation_type,
            confidence = excluded.confidence,
            source = excluded.source,
            decision = excluded.decision,
            resolution_note = excluded.resolution_note,
            updated_at = excluded.updated_at
        """,
        (
            brief_id,
            edge_key,
            suggestion_id(details.get("src_id") or src_raw),
            suggestion_id(details.get("dst_id") or dst_raw),
            str(details.get("relation_type") or "soft"),
            parse_optional_float(str(details.get("confidence"))) if details.get("confidence") is not None else None,
            str(details.get("source") or "review"),
            decision,
            resolution_note.strip() or None,
            now,
        ),
    )


def build_deferred_dag_payload(state: dict[str, Any], *, status: str, message: str) -> dict[str, Any]:
    return {
        "status": status,
        "message": message,
        "accepted_atomic_candidates": int(state["accepted_atomic_count"]),
        "pending_atomic_candidates": int(state["pending_atomic_count"]),
        "open_review_count": int(state["open_review_count"]),
        "nodes": 0,
        "edges": 0,
        "removed_cycle": 0,
        "removed_transitive": 0,
        "acyclic": True,
        "waves": [],
        "order": [],
        "final_edges": [],
        "edge_review_queue": [],
        "used_candidate_ids": [],
    }


def update_jobs_dag_payload(
    conn: CatalogConnection,
    brief_id: int,
    dag_payload: dict[str, Any],
    persisted_update: dict[str, Any] | None = None,
) -> None:
    rows = conn.execute(
        """
        SELECT id, result_payload
        FROM intake_job
        WHERE status = 'succeeded'
          AND json_valid(result_payload)
          AND CAST(json_extract(result_payload, '$.brief_id') AS INTEGER) = ?
        """,
        (brief_id,),
    ).fetchall()
    for row in rows:
        payload = json.loads(row["result_payload"])
        payload["dag"] = dag_payload
        if persisted_update and isinstance(payload.get("persisted"), dict):
            payload["persisted"].update(persisted_update)
        conn.execute(
            "UPDATE intake_job SET result_payload = ?, updated_at = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), utc_now_iso(), row["id"]),
        )
    conn.commit()


def clear_brief_dag_artifacts(conn: CatalogConnection, brief_id: int) -> None:
    if table_exists(conn, "skill_prerequisite") and column_exists(conn, "skill_prerequisite", "brief_id"):
        conn.execute("DELETE FROM skill_prerequisite WHERE brief_id = ?", (brief_id,))
    if table_exists(conn, "review_queue"):
        conn.execute(
            """
            DELETE FROM review_queue
            WHERE source_ref = ?
              AND json_valid(details)
              AND json_extract(details, '$.review_kind') = 'prerequisite_edge'
            """,
            (f"brief:{brief_id}",),
        )
    conn.commit()


def clear_brief_curriculum_plan_artifacts(conn: CatalogConnection, brief_id: int) -> None:
    if table_exists(conn, "curriculum_plan_row"):
        conn.execute(
            """
            DELETE FROM curriculum_plan_row
            WHERE plan_id IN (SELECT id FROM curriculum_plan WHERE brief_id = ?)
            """,
            (brief_id,),
        )
    if table_exists(conn, "curriculum_plan"):
        conn.execute("DELETE FROM curriculum_plan WHERE brief_id = ?", (brief_id,))
    conn.commit()


def refresh_brief_dag_state(
    conn: CatalogConnection,
    brief_id: int,
    *,
    status: str = "deferred",
    message: str | None = None,
) -> dict[str, Any]:
    state = get_brief_dag_state(conn, brief_id)
    if message is None:
        if state["accepted_atomic_count"]:
            message = "Граф будет пересчитан по текущему набору принятых атомарных навыков."
            status = "stale" if state["prerequisite_count"] else status
        else:
            message = "Граф пока пуст: нет принятых атомарных навыков."
    dag_payload = build_deferred_dag_payload(state, status=status, message=message)
    update_jobs_dag_payload(
        conn,
        brief_id,
        dag_payload,
        persisted_update={
            "skill_prerequisite": 0,
            "prerequisite_reviews": 0,
            "review_open": int(state["open_review_count"]),
        },
    )
    return state


def count_brief_template_proposals(conn: CatalogConnection, brief_id: int) -> dict[str, int]:
    if not table_exists(conn, "curriculum_artifact_template_proposal"):
        return {"total": 0, "open": 0, "accepted": 0, "rejected": 0}
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total_count,
            SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_count,
            SUM(CASE WHEN status = 'accepted' THEN 1 ELSE 0 END) AS accepted_count,
            SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected_count
        FROM curriculum_artifact_template_proposal
        WHERE brief_id = ?
        """,
        (brief_id,),
    ).fetchone()
    if not row:
        return {"total": 0, "open": 0, "accepted": 0, "rejected": 0}
    return {
        "total": int(row["total_count"] or 0),
        "open": int(row["open_count"] or 0),
        "accepted": int(row["accepted_count"] or 0),
        "rejected": int(row["rejected_count"] or 0),
    }


def get_brief_catalog_apply_state(conn: CatalogConnection, brief_id: int) -> dict[str, Any]:
    accepted_atomic = 0
    active_promotions = 0
    active_promoted_skills = 0
    if table_exists(conn, "skill_suggestion"):
        accepted_atomic = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM skill_suggestion
                WHERE brief_id = ?
                  AND entity_type = 'skill'
                  AND atomicity = 'atomic'
                  AND decision = 'accepted'
                """,
                (brief_id,),
            ).fetchone()[0]
        )
    if table_exists(conn, "skill_promotion_log") and table_exists(conn, "skill_suggestion"):
        promotion_row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_promotions,
                    COUNT(DISTINCT spl.skill_id) AS distinct_skills
                FROM skill_promotion_log spl
                JOIN skill_suggestion ss ON ss.id = spl.suggestion_id
                WHERE ss.brief_id = ?
                  AND spl.status = 'active'
                """,
                (brief_id,),
            ).fetchone()
        if isinstance(promotion_row, CatalogRow):
            total_promotions = promotion_row["total_promotions"]
            distinct_skills = promotion_row["distinct_skills"]
        else:
            total_promotions = promotion_row[0]
            distinct_skills = promotion_row[1]
        active_promotions = int(total_promotions or 0)
        active_promoted_skills = int(distinct_skills or 0)
    skill_set_items = 0
    if table_exists(conn, "skill_set") and table_exists(conn, "skill_set_item"):
        skill_set_items = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM skill_set_item ssi
                JOIN skill_set ss ON ss.id = ssi.skill_set_id
                WHERE ss.source_type = 'brief'
                  AND ss.source_id = ?
                  AND ss.status = 'active'
                """,
                (brief_id,),
            ).fetchone()[0]
        )
    templates = count_brief_template_proposals(conn, brief_id)
    # Several accepted candidates can legitimately resolve into one canonical skill.
    # DAG/UP readiness must compare skillset rows with unique promoted skills, not
    # with the raw candidate count, otherwise deduplication blocks the workflow.
    catalog_applied = bool(
        accepted_atomic
        and active_promotions >= accepted_atomic
        and skill_set_items >= active_promoted_skills
    )
    return {
        "accepted_atomic": accepted_atomic,
        "active_promotions": active_promotions,
        "active_promoted_skills": active_promoted_skills,
        "skill_set_items": skill_set_items,
        "template_proposals": templates["total"],
        "open_template_proposals": templates["open"],
        "accepted_template_proposals": templates["accepted"],
        "catalog_applied": catalog_applied,
    }


def count_open_skill_reviews_for_brief(conn: CatalogConnection, brief_id: int) -> int:
    if not table_exists(conn, "review_queue"):
        return 0
    return int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM review_queue
            WHERE source_ref = ?
              AND entity_type = 'skill'
              AND status = 'open'
              AND NOT (
                  json_valid(details)
                  AND json_extract(details, '$.review_kind') = 'prerequisite_edge'
              )
            """,
            (f"brief:{brief_id}",),
        ).fetchone()[0]
    )


def count_open_prerequisite_edge_reviews_for_brief(conn: CatalogConnection, brief_id: int) -> int:
    if not table_exists(conn, "review_queue"):
        return 0
    return int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM review_queue
            WHERE source_ref = ?
              AND entity_type = 'prerequisite_edge'
              AND status = 'open'
              AND json_valid(details)
              AND json_extract(details, '$.review_kind') = 'prerequisite_edge'
            """,
            (f"brief:{brief_id}",),
        ).fetchone()[0]
    )


def load_brief_catalog_promotion_summary(conn: CatalogConnection, brief_id: int, limit: int = 10) -> dict[str, Any]:
    if not all(table_exists(conn, name) for name in ("skill_promotion_log", "skill_suggestion", "skill")):
        return {"total": 0, "items": []}
    rows = fetch_all(
        conn,
        """
        SELECT
            spl.skill_id,
            spl.suggestion_id,
            spl.status,
            ss.suggested_name,
            ss.resolution,
            s.canonical_name,
            COALESCE(sg.name, ss.group_name, '') AS group_name
        FROM skill_promotion_log spl
        JOIN skill_suggestion ss ON ss.id = spl.suggestion_id
        JOIN skill s ON s.id = spl.skill_id
        LEFT JOIN skill_group sg ON sg.id = s.group_id
        WHERE ss.brief_id = ?
          AND spl.status = 'active'
        ORDER BY spl.id DESC
        LIMIT ?
        """,
        (brief_id, limit),
    )
    total = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM skill_promotion_log spl
            JOIN skill_suggestion ss ON ss.id = spl.suggestion_id
            WHERE ss.brief_id = ?
              AND spl.status = 'active'
            """,
            (brief_id,),
        ).fetchone()[0]
    )
    return {"total": total, "items": rows}


def update_jobs_catalog_payload(
    conn: CatalogConnection,
    brief_id: int,
    *,
    catalog_state: dict[str, Any],
    persisted_update: dict[str, Any] | None = None,
) -> None:
    rows = conn.execute(
        """
        SELECT id, result_payload
        FROM intake_job
        WHERE status = 'succeeded'
          AND json_valid(result_payload)
          AND CAST(json_extract(result_payload, '$.brief_id') AS INTEGER) = ?
        """,
        (brief_id,),
    ).fetchall()
    for row in rows:
        payload = json.loads(row["result_payload"])
        payload["catalog_state"] = catalog_state
        if persisted_update and isinstance(payload.get("persisted"), dict):
            payload["persisted"].update(persisted_update)
        conn.execute(
            "UPDATE intake_job SET result_payload = ?, updated_at = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), utc_now_iso(), row["id"]),
        )
    conn.commit()


def apply_brief_catalog_decisions(conn: CatalogConnection, brief_id: int) -> dict[str, Any]:
    """Apply accepted skill decisions to the canonical catalog as a batch step."""
    from content_factory.catalog.pipeline import llm as intake_llm
    from content_factory.catalog.pipeline import storage as intake_storage

    clear_brief_dag_artifacts(conn, brief_id)
    clear_brief_curriculum_plan_artifacts(conn, brief_id)
    promotion_stats = intake_storage.sync_promotions_for_brief(conn, brief_id)
    skill_set = intake_storage.sync_brief_skill_set(conn, brief_id)
    plan_payload = build_deferred_curriculum_plan_payload(
        "УП ещё не строился: примите нужные шаблоны УП и запустите построение DAG/УП."
    )
    save_meta = intake_storage.save_curriculum_plan(conn, brief_id, plan_payload)
    plan_payload["plan_id"] = save_meta["plan_id"]
    plan_payload["row_count"] = save_meta["row_count"]
    try:
        intake_llm.set_usage_context(brief_id=brief_id, stage="up_template_consilium")
        template_proposals = intake_storage.generate_curriculum_artifact_template_proposals(
            conn,
            brief_id=brief_id,
            plan_id=int(save_meta["plan_id"]),
        )
    finally:
        intake_llm.clear_usage_context()

    catalog_state = get_brief_catalog_apply_state(conn, brief_id)
    catalog_state.update(
        {
            "last_apply_promoted": int(promotion_stats.get("promoted", 0) or 0),
            "last_apply_reverted": int(promotion_stats.get("reverted", 0) or 0),
            "skill_set_status": skill_set.get("status"),
            "skill_set_id": skill_set.get("skill_set_id"),
        }
    )
    update_jobs_catalog_payload(
        conn,
        brief_id,
        catalog_state=catalog_state,
        persisted_update={
            "catalog_promoted": int(catalog_state.get("active_promotions") or 0),
            "catalog_reverted": int(promotion_stats.get("reverted", 0) or 0),
            "template_proposals": len(template_proposals),
            "skill_set_items": int(catalog_state.get("skill_set_items") or 0),
        },
    )
    state = refresh_brief_dag_state(
        conn,
        brief_id,
        status="catalog_applied",
        message="Справочник и набор навыков обновлены. Теперь можно принять шаблоны УП и построить DAG/УП.",
    )
    plan_payload["template_proposal_count"] = len(template_proposals)
    plan_payload["template_proposal_status"] = "open" if template_proposals else "none"
    conn.execute(
        "UPDATE curriculum_plan SET payload_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (json.dumps(plan_payload, ensure_ascii=False), int(save_meta["plan_id"])),
    )
    conn.commit()
    update_jobs_curriculum_plan_payload(
        conn,
        brief_id,
        plan_payload,
        persisted_update={"curriculum_plan_rows": 0},
    )
    return {
        "brief_id": brief_id,
        "catalog_state": catalog_state,
        "dag_state": state,
        "template_proposals": len(template_proposals),
        "promotion_stats": promotion_stats,
        "skill_set": skill_set,
    }


def hydrate_job_result_payload(conn: CatalogConnection, result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return result
    brief_id = result.get("brief_id")
    if not isinstance(brief_id, int) or not isinstance(result.get("candidates"), list):
        return result
    from content_factory.catalog.pipeline import config as intake_config

    suggestion_rows = conn.execute(
        """
        SELECT id, suggested_name, source_name, group_name, entity_type, atomicity, decision,
               confidence, council_agreement, resolution, match_score,
               nearest_skill_id, nearest_name, nearest_group
        FROM skill_suggestion
        WHERE brief_id = ?
        ORDER BY id
        """,
        (brief_id,),
    ).fetchall()
    rows_by_key: dict[tuple[str, str, str, str], list[CatalogRow]] = defaultdict(list)
    id_to_row: dict[int, CatalogRow] = {}
    for row in suggestion_rows:
        key = (
            str(row["suggested_name"] or ""),
            str(row["group_name"] or ""),
            str(row["entity_type"] or ""),
            str(row["atomicity"] or ""),
        )
        rows_by_key[key].append(row)
        id_to_row[int(row["id"])] = row

    review_status_by_entity: dict[int, str] = {}
    for row in conn.execute(
        """
        SELECT entity_id, status
        FROM review_queue
        WHERE source_ref = ?
          AND entity_id IS NOT NULL
        ORDER BY id
        """,
        (f"brief:{brief_id}",),
    ):
        review_status_by_entity[int(row["entity_id"])] = str(row["status"])

    coverage_by_name: dict[str, str] = {}
    if isinstance(result.get("coverage"), dict):
        for row in result["coverage"].get("rows", []):
            if not isinstance(row, dict):
                continue
            area = str(row.get("area") or "").strip()
            if not area:
                continue
            for candidate_name in row.get("candidate_names") or []:
                name = str(candidate_name or "").strip()
                if name:
                    coverage_by_name[name] = area

    for candidate in result["candidates"]:
        if not isinstance(candidate, dict):
            continue
        suggestion_id = candidate.get("suggestion_id")
        row = id_to_row.get(int(suggestion_id)) if isinstance(suggestion_id, int) else None
        if row is None:
            key = (
                str(candidate.get("name") or ""),
                str(candidate.get("group") or ""),
                str(candidate.get("entity_type") or ""),
                str(candidate.get("atomicity") or ""),
            )
            row_list = rows_by_key.get(key)
            row = row_list.pop(0) if row_list else None
        if row is None:
            continue
        suggestion_id = int(row["id"])
        candidate["suggestion_id"] = suggestion_id
        candidate["decision"] = str(row["decision"] or candidate.get("decision") or "pending")
        confidence_value = float(row["confidence"]) if row["confidence"] is not None else None
        council_agreement_value = float(row["council_agreement"]) if row["council_agreement"] is not None else None
        candidate["confidence"] = f"{confidence_value:.2f}" if confidence_value is not None else "—"
        candidate["council_agreement"] = f"{council_agreement_value:.2f}" if council_agreement_value is not None else None
        match_score_value = float(row["match_score"]) if row["match_score"] is not None else None
        candidate["match_score"], candidate["novelty_score"] = format_catalog_similarity(match_score_value)
        candidate["resolution"] = row["resolution"] or candidate.get("resolution")
        candidate["source_name"] = row["source_name"] or candidate.get("source_name")
        candidate["nearest_skill_id"] = row["nearest_skill_id"] or candidate.get("nearest_skill_id")
        candidate["nearest_name"] = row["nearest_name"] or candidate.get("nearest_name")
        candidate["nearest_group"] = row["nearest_group"] or candidate.get("nearest_group")
        nearest_id = None
        try:
            nearest_id = int(candidate["nearest_skill_id"]) if candidate.get("nearest_skill_id") else None
        except (TypeError, ValueError):
            nearest_id = None
        candidate["similarity_hint"] = build_similarity_hint(
            match_score_value,
            str(candidate.get("resolution") or ""),
            bool(nearest_id),
            candidate.get("reasons"),
        )
        nearest_preview = load_nearest_skill_preview(conn, nearest_id)
        if nearest_preview:
            candidate["nearest_preview"] = nearest_preview
            candidate["nearest_name"] = candidate.get("nearest_name") or nearest_preview.get("name")
            candidate["nearest_group"] = candidate.get("nearest_group") or nearest_preview.get("group")
        candidate["recommended_action"] = build_candidate_recommended_action(
            match_score_value,
            str(candidate.get("resolution") or ""),
            bool(nearest_id),
            str(candidate.get("nearest_name") or ""),
            candidate.get("reasons"),
            str(candidate.get("decision") or ""),
        )
        candidate["decision_rationale"] = build_decision_rationale(candidate)
        default_review_status = (
            "resolved"
            if candidate["decision"] == "accepted"
            else ("ignored" if candidate["decision"] == "rejected" else "open")
        )
        candidate["review_status"] = review_status_by_entity.get(suggestion_id, default_review_status)
        candidate["can_review_inline"] = candidate.get("entity_type") == "skill" and candidate.get("atomicity") == "atomic"
        if not candidate.get("coverage_area"):
            parent_name = str(candidate.get("parent_name") or "").strip()
            own_name = str(candidate.get("name") or "").strip()
            candidate["coverage_area"] = coverage_by_name.get(parent_name) or coverage_by_name.get(own_name)
        if (
            candidate["decision"] == "accepted"
            and confidence_value is not None
            and confidence_value >= intake_config.AUTO_ACCEPT_CONFIDENCE
            and council_agreement_value is not None
            and council_agreement_value >= intake_config.AUTO_ACCEPT_COUNCIL_AGREEMENT
        ):
            candidate["reasons"] = review_reason_label("auto_accept_policy")

    if isinstance(result.get("council_metrics"), dict):
        candidates = [item for item in result["candidates"] if isinstance(item, dict)]
        resolved_candidates = [
            item
            for item in candidates
            if item.get("entity_type") == "skill" and item.get("atomicity") == "atomic"
        ]
        council_candidates = [item for item in resolved_candidates if item.get("council_agreement") not in {None, "", "—"}]
        result["council_metrics"].update(
            {
                "sent_to_council": len(council_candidates),
                "auto_accepted": len(
                    [item for item in resolved_candidates if item.get("decision") == "accepted" and item.get("council_agreement") in {None, "", "—"}]
                ),
                "accepted_after_council": len(
                    [item for item in council_candidates if item.get("decision") == "accepted"]
                ),
                "review_after_council": len(
                    [item for item in council_candidates if item.get("decision") == "needs_review"]
                ),
                "needs_review_total": len([item for item in candidates if item.get("decision") == "needs_review"]),
                "accepted_total": len([item for item in resolved_candidates if item.get("decision") == "accepted"]),
                "matched_total": len([item for item in resolved_candidates if item.get("resolution") == "matched"]),
                "alias_total": len([item for item in resolved_candidates if item.get("resolution") == "alias"]),
                "fuzzy_total": len([item for item in resolved_candidates if item.get("resolution") == "fuzzy"]),
                "new_total": len([item for item in resolved_candidates if item.get("resolution") == "new"]),
            }
        )

    if not isinstance(result.get("dag"), dict):
        state = get_brief_dag_state(conn, brief_id)
        result["dag"] = build_deferred_dag_payload(
            state,
            status="waiting_catalog",
            message="DAG строится отдельным шагом после применения проверенных навыков в справочник.",
        )
    if not isinstance(result.get("curriculum_plan"), dict):
        result["curriculum_plan"] = build_deferred_curriculum_plan_payload(
            "УП строится отдельным шагом после применения навыков в справочник, принятия шаблонов и построения DAG."
        )
    result["catalog_state"] = get_brief_catalog_apply_state(conn, brief_id)

    if isinstance(result.get("persisted"), dict):
        result["persisted"]["review_open"] = int(get_brief_dag_state(conn, brief_id)["open_review_count"])
        result["persisted"]["curriculum_plan_rows"] = int(result.get("curriculum_plan", {}).get("row_count", 0) or 0)
        result["persisted"]["catalog_promoted"] = int(result["catalog_state"].get("active_promotions") or 0)
        result["persisted"]["skill_set_items"] = int(result["catalog_state"].get("skill_set_items") or 0)
        result["persisted"]["template_proposals"] = int(result["catalog_state"].get("template_proposals") or 0)
    return result


def build_intake_workflow_steps(
    job: dict[str, Any] | None,
    result: dict[str, Any] | None,
    dag_build_state: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not job:
        return []

    job_status = str(job.get("status") or "")
    candidates = result.get("candidates") if isinstance(result, dict) else []
    candidates = candidates if isinstance(candidates, list) else []
    accepted_count = len([item for item in candidates if isinstance(item, dict) and item.get("decision") == "accepted"])
    review_count = len([item for item in candidates if isinstance(item, dict) and item.get("decision") == "needs_review"])

    persisted = result.get("persisted") if isinstance(result, dict) and isinstance(result.get("persisted"), dict) else {}
    if isinstance(persisted, dict) and persisted.get("review_open") is not None:
        try:
            review_count = int(persisted.get("review_open") or 0)
        except (TypeError, ValueError):
            pass
    promoted_count = int(persisted.get("catalog_promoted") or 0) if isinstance(persisted, dict) else 0
    template_proposals = int(persisted.get("template_proposals") or 0) if isinstance(persisted, dict) else 0
    catalog_state = result.get("catalog_state") if isinstance(result, dict) and isinstance(result.get("catalog_state"), dict) else {}
    catalog_applied = bool(catalog_state.get("catalog_applied")) if isinstance(catalog_state, dict) else False

    dag_payload = result.get("dag") if isinstance(result, dict) and isinstance(result.get("dag"), dict) else {}
    curriculum_plan = result.get("curriculum_plan") if isinstance(result, dict) and isinstance(result.get("curriculum_plan"), dict) else {}
    int(dag_payload.get("nodes") or 0) if isinstance(dag_payload, dict) else 0
    plan_id = curriculum_plan.get("plan_id") if isinstance(curriculum_plan, dict) else None

    if job_status in {"pending", "running"}:
        review_status = "active"
        catalog_status = "pending"
        up_status = "pending"
    elif job_status == "failed":
        review_status = "warn"
        catalog_status = "pending"
        up_status = "pending"
    else:
        review_status = "active" if review_count else "done"
        catalog_status = "done" if catalog_applied else ("active" if accepted_count else "pending")
        templates_status = "done" if template_proposals else ("active" if catalog_applied else "pending")
        up_status = "done" if plan_id else ("active" if catalog_applied and template_proposals else "pending")
    if job_status in {"pending", "running", "failed"}:
        templates_status = "pending"

    accepted_atomic = dag_build_state.get("accepted_atomic_count") if isinstance(dag_build_state, dict) else accepted_count
    open_review = dag_build_state.get("open_review_count") if isinstance(dag_build_state, dict) else review_count

    return [
        {
            "key": "brief",
            "label": "Бриф",
            "status": "done",
            "description": "Текст или документ принят в обработку.",
            "href": f"/intake/jobs/{job['id']}",
        },
        {
            "key": "review",
            "label": "Проверка навыков",
            "status": review_status,
            "description": (
                f"Открыто вопросов: {open_review}."
                if review_status == "active"
                else ("Intake завершился ошибкой." if review_status == "warn" else "Кандидаты проверены.")
            ),
            "href": "/reviews" if review_count else f"/intake/jobs/{job['id']}",
        },
        {
            "key": "catalog",
            "label": "Справочник и набор навыков",
            "status": catalog_status,
            "description": f"Принято: {accepted_atomic or accepted_count}, промоций: {promoted_count}.",
            "href": f"/intake/jobs/{job['id']}",
        },
        {
            "key": "templates",
            "label": "Шаблоны УП",
            "status": templates_status,
            "description": f"Предложений: {template_proposals}." if template_proposals else "Появятся после применения навыков в справочник.",
            "href": f"/up/plans/{plan_id}/template-proposals" if plan_id and template_proposals else f"/intake/jobs/{job['id']}",
        },
        {
            "key": "up",
            "label": "DAG и УП",
            "status": up_status,
            "description": "Черновик доступен." if plan_id else "Строится после набора навыков, шаблонов и DAG.",
            "href": f"/up/plans/{plan_id}" if plan_id else "/up",
        },
    ]


def build_intake_workspace_state(
    conn: CatalogConnection,
    job: dict[str, Any] | None,
    result: dict[str, Any] | None,
    dag_build_state: dict[str, Any] | None,
) -> dict[str, Any]:
    if not job:
        return {"next_step": None, "blockers": [], "catalog_summary": {"total": 0, "items": []}}

    job_id = int(job["id"])
    job_status = str(job.get("status") or "")
    brief_id = result.get("brief_id") if isinstance(result, dict) else None
    brief_id = brief_id if isinstance(brief_id, int) else None

    catalog_state = result.get("catalog_state") if isinstance(result, dict) and isinstance(result.get("catalog_state"), dict) else {}
    result_dict = _as_dict(result)
    curriculum_plan = _as_dict(result_dict.get("curriculum_plan"))
    dag_payload = _as_dict(result_dict.get("dag"))
    plan_id = curriculum_plan.get("plan_id") if isinstance(curriculum_plan, dict) else None

    open_skill_reviews = count_open_skill_reviews_for_brief(conn, brief_id) if brief_id is not None else 0
    open_edge_reviews = count_open_prerequisite_edge_reviews_for_brief(conn, brief_id) if brief_id is not None else 0
    open_competency_reviews = count_open_candidate_competencies(conn)
    accepted_atomic = int(catalog_state.get("accepted_atomic") or 0) if isinstance(catalog_state, dict) else 0
    active_promotions = int(catalog_state.get("active_promotions") or 0) if isinstance(catalog_state, dict) else 0
    open_templates = int(catalog_state.get("open_template_proposals") or 0) if isinstance(catalog_state, dict) else 0
    catalog_pending = accepted_atomic > 0 and active_promotions < accepted_atomic
    dag_built = str(dag_payload.get("status") or "").casefold() == "built" and int(dag_payload.get("nodes") or 0) > 0
    plan_ready = bool(plan_id and int(curriculum_plan.get("row_count") or len(curriculum_plan.get("rows") or [])) > 0)
    skills_resolved = job_status not in {"pending", "running", "failed"} and open_skill_reviews == 0

    blockers: list[dict[str, Any]] = []
    if open_skill_reviews:
        blockers.append(
            {
                "code": "open_skill_reviews",
                "label": "Открытые навыки",
                "count": open_skill_reviews,
                "severity": "warn",
                "description": "Нужно принять, привязать или отклонить спорные навыки.",
                "href": "/reviews?status=open",
            }
        )
    if catalog_pending:
        blockers.append(
            {
                "code": "catalog_pending",
                "label": "Accepted не применены",
                "count": accepted_atomic - active_promotions,
                "severity": "warn",
                "description": "Принятые навыки ещё не записаны в канонический справочник, синонимы и набор навыков.",
                "href": f"/intake/jobs/{job_id}",
            }
        )
    if open_competency_reviews:
        blockers.append(
            {
                "code": "open_competency_reviews",
                "label": "Кандидатные компетенции",
                "count": open_competency_reviews,
                "severity": "warn",
                "description": "Нужно принять или отклонить новые competency-группировки.",
                "href": "/catalog-admin/candidate-competencies",
            }
        )
    if open_templates:
        blockers.append(
            {
                "code": "open_templates",
                "label": "Шаблоны УП",
                "count": open_templates,
                "severity": "info",
                "description": "Проверьте предложенные шаблоны артефактов перед сборкой УП.",
                "href": f"/up/plans/{plan_id}/template-proposals" if plan_id else "/up",
            }
        )
    if open_edge_reviews:
        blockers.append(
            {
                "code": "open_prerequisite_edges",
                "label": "Рёбра DAG",
                "count": open_edge_reviews,
                "severity": "warn",
                "description": "Проверьте предложенные связи перед финальным использованием графа в УП.",
                "href": "/reviews?status=open&entity_type=prerequisite_edge",
            }
        )
    if not dag_built and not plan_ready and accepted_atomic:
        blockers.append(
            {
                "code": "dag_missing",
                "label": "DAG не построен",
                "count": 1,
                "severity": "info",
                "description": "После проверок нужно построить граф и учебный план.",
                "href": f"/intake/jobs/{job_id}",
            }
        )

    if job_status in {"pending", "running"}:
        next_step = {
            "code": "wait",
            "label": "Дождаться обработки",
            "description": "Intake-задача ещё выполняется.",
            "method": "get",
            "href": f"/intake/jobs/{job_id}",
            "disabled": True,
        }
    elif job_status == "failed":
        next_step = {
            "code": "failed",
            "label": "Посмотреть ошибку",
            "description": "Pipeline завершился ошибкой.",
            "method": "get",
            "href": f"/intake/jobs/{job_id}",
            "disabled": True,
        }
    elif open_skill_reviews:
        next_step = {
            "code": "open_reviews",
            "label": "Открыть проверку навыков",
            "description": f"Осталось спорных навыков: {open_skill_reviews}.",
            "method": "get",
            "href": "/reviews?status=open",
        }
    elif catalog_pending:
        next_step = {
            "code": "apply_catalog",
            "label": "Применить принятые навыки в справочник",
            "description": f"Будет применено: {accepted_atomic - active_promotions}.",
            "method": "post",
            "href": f"/intake/jobs/{job_id}/next-step",
        }
    elif open_competency_reviews:
        next_step = {
            "code": "candidate_competencies",
            "label": "Проверить кандидатные компетенции",
            "description": f"Открыто competency-группировок: {open_competency_reviews}.",
            "method": "get",
            "href": "/catalog-admin/candidate-competencies",
        }
    elif open_templates and plan_id:
        next_step = {
            "code": "templates",
            "label": "Проверить шаблоны УП",
            "description": f"Открыто предложений: {open_templates}.",
            "method": "get",
            "href": f"/up/plans/{plan_id}/template-proposals",
        }
    elif open_edge_reviews:
        next_step = {
            "code": "review_dag_edges",
            "label": "Проверить рёбра DAG",
            "description": f"Открыто связей на проверке: {open_edge_reviews}.",
            "method": "get",
            "href": "/reviews?status=open&entity_type=prerequisite_edge",
        }
    elif not plan_ready:
        next_step = {
            "code": "build_dag",
            "label": "Построить DAG и УП",
            "description": "Собрать граф и учебный план из принятого набора навыков.",
            "method": "post",
            "href": f"/intake/jobs/{job_id}/next-step",
        }
    else:
        next_step = {
            "code": "open_up",
            "label": "Открыть учебный план",
            "description": "Черновик УП готов к проверке.",
            "method": "get",
            "href": f"/up/plans/{plan_id}",
        }

    return {
        "brief_id": brief_id,
        "next_step": next_step,
        "blockers": blockers,
        "catalog_summary": load_brief_catalog_promotion_summary(conn, brief_id) if brief_id is not None else {"total": 0, "items": []},
        "open_skill_reviews": open_skill_reviews,
        "open_edge_reviews": open_edge_reviews,
        "open_competency_reviews": open_competency_reviews,
        "catalog_pending": catalog_pending,
        "skills_resolved": skills_resolved,
        "dag_built": dag_built,
        "plan_ready": plan_ready,
        "show_downstream_sections": skills_resolved
        and (
            dag_built
            or plan_ready
            or (
                not catalog_pending
                and not open_competency_reviews
                and not open_templates
            )
        ),
    }


def apply_candidate_decision(
    conn: CatalogConnection,
    suggestion_id: int,
    target_decision: str,
    resolution_note: str | None = None,
) -> int | None:
    from content_factory.catalog.pipeline import storage

    row = conn.execute(
        """
        SELECT id, brief_id
        FROM skill_suggestion
        WHERE id = ?
        """,
        (suggestion_id,),
    ).fetchone()
    if not row:
        return None

    brief_id = int(row["brief_id"])
    review_status_map = {
        "accepted": "resolved",
        "needs_review": "open",
        "rejected": "ignored",
    }
    review_status = review_status_map.get(target_decision, "open")
    now = utc_now_iso()
    reviewed_at = None if review_status == "open" else now
    conn.execute(
        "UPDATE skill_suggestion SET decision = ? WHERE id = ?",
        (target_decision, suggestion_id),
    )
    conn.execute(
        """
        UPDATE review_queue
        SET status = ?,
            resolution_note = COALESCE(?, resolution_note),
            reviewed_at = ?,
            updated_at = ?
        WHERE source_ref = ?
          AND entity_id = ?
        """,
        (review_status, resolution_note, reviewed_at, now, f"brief:{brief_id}", suggestion_id),
    )
    if target_decision != "accepted":
        storage.revert_suggestion_promotion(conn, suggestion_id)
    clear_brief_dag_artifacts(conn, brief_id)
    clear_brief_curriculum_plan_artifacts(conn, brief_id)
    catalog_state = get_brief_catalog_apply_state(conn, brief_id)
    update_jobs_catalog_payload(
        conn,
        brief_id,
        catalog_state=catalog_state,
        persisted_update={
            "catalog_promoted": int(catalog_state.get("active_promotions") or 0),
            "skill_set_items": int(catalog_state.get("skill_set_items") or 0),
            "curriculum_plan_rows": 0,
        },
    )
    update_jobs_curriculum_plan_payload(
        conn,
        brief_id,
        build_deferred_curriculum_plan_payload(
            "УП инвалидирован изменением решения по skill. Примените решения в справочник и заново постройте DAG/УП."
        ),
        persisted_update={"curriculum_plan_rows": 0},
    )
    conn.commit()
    return brief_id


def load_accepted_skill_candidates(
    conn: CatalogConnection, brief_id: int
) -> tuple[list[SkillCandidate], dict[str, int]]:
    from content_factory.catalog.pipeline.models import IndicatorSpec, SkillCandidate

    rows = conn.execute(
        """
        SELECT
            ss.id,
            ss.suggested_name,
            ss.group_name,
            ss.coverage_area,
            ss.bloom,
            ss.indicators_json,
            ss.tools,
            ss.evidence_ids,
            ss.resolution,
            ss.canonical_skill_id,
            s.canonical_name,
            ss.confidence,
            ss.council_agreement
        FROM skill_suggestion ss
        LEFT JOIN skill s ON s.id = ss.canonical_skill_id
        WHERE ss.brief_id = ?
          AND ss.entity_type = 'skill'
          AND ss.atomicity = 'atomic'
          AND ss.decision = 'accepted'
        ORDER BY ss.id
        """,
        (brief_id,),
    ).fetchall()

    bloom_fallback = {"remember", "understand", "apply", "analyze", "evaluate", "create"}
    cands = []
    tmp_to_db: dict[str, int] = {}
    for row in rows:
        bloom_label = str(row["bloom"] or "remember").strip().casefold()
        if bloom_label not in bloom_fallback:
            bloom_label = "remember"
        raw_indicators = json.loads(row["indicators_json"] or "[]")
        indicators = []
        for item in raw_indicators:
            if not isinstance(item, dict):
                continue
            indicator_bloom = str(item.get("bloom") or bloom_label).strip().casefold()
            if indicator_bloom not in bloom_fallback:
                indicator_bloom = bloom_label
            indicators.append(
                IndicatorSpec(
                    text=str(item.get("text") or row["suggested_name"]),
                    bloom=indicator_bloom,
                )
            )
        if not indicators:
            indicators = [IndicatorSpec(text=row["suggested_name"], bloom=bloom_label)]
        tmp_id = f"S{row['id']}"
        candidate = SkillCandidate(
            tmp_id=tmp_id,
            name=row["suggested_name"],
            group=row["group_name"] or "Без группы",
            coverage_area=row["coverage_area"],
            indicators=indicators,
            tools=json.loads(row["tools"] or "[]"),
            evidence_ids=[str(item) for item in json.loads(row["evidence_ids"] or "[]") if item is not None],
            confidence=float(row["confidence"] or 0.0),
            council_agreement=float(row["council_agreement"]) if row["council_agreement"] is not None else None,
            entity_type="skill",
            atomicity="atomic",
            resolution=row["resolution"],
            canonical_skill_id=row["canonical_skill_id"],
            canonical_name=row["canonical_name"],
            canonical_group=None,
            decision="accepted",
        )
        cands.append(candidate)
        tmp_to_db[tmp_id] = int(row["id"])
    return cands, tmp_to_db


def load_brief_spec_for_plan(conn: CatalogConnection, brief_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT raw_text, role, seniority, domain FROM profile_brief WHERE id = ?",
        (brief_id,),
    ).fetchone()
    if not row:
        return {}
    from content_factory.catalog.pipeline import stage_brief_to_catalog
    from content_factory.catalog.pipeline import storage as intake_storage

    spec = {
        "role": row["role"],
        "seniority": row["seniority"],
        "domain": row["domain"],
    }
    spec.update({key: value for key, value in stage_brief_to_catalog.extract_workload_from_text(str(row["raw_text"] or "")).items() if value is not None})
    spec["artifact_templates"] = intake_storage.load_curriculum_artifact_templates(conn)
    return spec


def build_curriculum_plan_for_brief(
    conn: CatalogConnection,
    brief_id: int,
    candidates: list[SkillCandidate] | None = None,
    dag_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from content_factory.catalog.pipeline import stage_dag_to_up, storage

    clear_brief_curriculum_plan_artifacts(conn, brief_id)
    accepted_candidates, _tmp_to_db = load_accepted_skill_candidates(conn, brief_id)
    cands = accepted_candidates if candidates is None else candidates
    effective_dag_payload = dag_payload or build_deferred_dag_payload(get_brief_dag_state(conn, brief_id), status="deferred", message="DAG не построен")
    spec = load_brief_spec_for_plan(conn, brief_id)
    plan_payload = stage_dag_to_up.run(spec, cands, effective_dag_payload)
    save_meta = storage.save_curriculum_plan(conn, brief_id, plan_payload)
    plan_payload["plan_id"] = save_meta["plan_id"]
    plan_payload["row_count"] = save_meta["row_count"]
    template_stats = count_brief_template_proposals(conn, brief_id)
    plan_payload["template_proposal_count"] = template_stats["total"]
    plan_payload["template_proposal_status"] = "open" if template_stats["open"] else ("done" if template_stats["total"] else "none")
    conn.execute(
        "UPDATE curriculum_plan SET payload_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (json.dumps(plan_payload, ensure_ascii=False), int(save_meta["plan_id"])),
    )
    conn.commit()
    update_jobs_curriculum_plan_payload(
        conn,
        brief_id,
        plan_payload,
        persisted_update={
            "curriculum_plan_rows": save_meta["row_count"],
            "template_proposals": template_stats["total"],
        },
    )
    return plan_payload


def build_dag_for_brief(conn: CatalogConnection, brief_id: int) -> dict[str, Any]:
    from content_factory.catalog.pipeline import llm as intake_llm
    from content_factory.catalog.pipeline import stage_catalog_to_dag, storage

    catalog_state = get_brief_catalog_apply_state(conn, brief_id)
    if not bool(catalog_state.get("catalog_applied")):
        clear_brief_dag_artifacts(conn, brief_id)
        clear_brief_curriculum_plan_artifacts(conn, brief_id)
        state = refresh_brief_dag_state(
            conn,
            brief_id,
            status="waiting_catalog",
            message="DAG не построен: сначала примените принятые навыки в справочник и набор навыков.",
        )
        plan_payload = build_deferred_curriculum_plan_payload(
            "УП не построен: сначала примените принятые skills в справочник, затем примите шаблоны и запустите DAG."
        )
        update_jobs_curriculum_plan_payload(
            conn,
            brief_id,
            plan_payload,
            persisted_update={"curriculum_plan_rows": 0},
        )
        return {
            "brief_id": brief_id,
            "state": state,
            "catalog_state": catalog_state,
            "dag": build_deferred_dag_payload(
                state,
                status="waiting_catalog",
                message="DAG не построен: сначала примените принятые навыки в справочник и набор навыков.",
            ),
            "curriculum_plan": plan_payload,
        }

    clear_brief_dag_artifacts(conn, brief_id)
    cands, tmp_to_db = load_accepted_skill_candidates(conn, brief_id)
    if not cands:
        clear_brief_curriculum_plan_artifacts(conn, brief_id)
        plan_payload = build_deferred_curriculum_plan_payload(
            "Черновик УП пока не строится: ещё нет принятых навыков с валидным DAG."
        )
        save_meta = storage.save_curriculum_plan(conn, brief_id, plan_payload)
        plan_payload["plan_id"] = save_meta["plan_id"]
        plan_payload["row_count"] = save_meta["row_count"]
        state = refresh_brief_dag_state(
            conn,
            brief_id,
            status="deferred",
            message="Граф пока пуст: ещё нет принятых атомарных навыков. Он построится автоматически после первого принятия.",
        )
        update_jobs_curriculum_plan_payload(
            conn,
            brief_id,
            plan_payload,
            persisted_update={"curriculum_plan_rows": 0},
        )
        return {
            "brief_id": brief_id,
            "state": state,
            "dag": build_deferred_dag_payload(
                state,
                status="deferred",
                message="Граф пока пуст: ещё нет принятых атомарных навыков. Он построится автоматически после первого принятия.",
            ),
            "curriculum_plan": plan_payload,
        }

    intake_llm.set_usage_context(stage="dag", brief_id=brief_id)
    try:
        edges, dag, removed_cycle, removed_transitive, dag_payload = stage_catalog_to_dag.run(
            cands,
            edge_decisions=load_prerequisite_edge_decisions(conn, brief_id),
        )
    finally:
        intake_llm.set_usage_context(stage=None)
    prereq_count = storage.save_prerequisites(conn, brief_id, dag, cands, tmp_to_db)
    prereq_review_count = storage.save_prerequisite_reviews(conn, brief_id, dag_payload["edge_review_queue"])
    dag_payload["status"] = "built"
    dag_payload["message"] = "Граф построен по текущему набору принятых атомарных навыков и пересчитывается автоматически."
    dag_payload["accepted_atomic_candidates"] = len(cands)
    dag_payload["prerequisite_rows"] = prereq_count
    dag_payload["prerequisite_review_rows"] = prereq_review_count
    plan_payload = build_curriculum_plan_for_brief(conn, brief_id, cands, dag_payload)
    update_jobs_dag_payload(
        conn,
        brief_id,
        dag_payload,
        persisted_update={
            "skill_prerequisite": prereq_count,
            "prerequisite_reviews": prereq_review_count,
            "review_open": int(get_brief_dag_state(conn, brief_id)["open_review_count"]),
        },
    )
    return {
        "brief_id": brief_id,
        "state": get_brief_dag_state(conn, brief_id),
        "dag": dag_payload,
        "curriculum_plan": plan_payload,
        "edges": len(edges),
        "removed_cycle": len(removed_cycle),
        "removed_transitive": len(removed_transitive),
    }


def list_dag_build_options(conn: CatalogConnection) -> list[dict[str, Any]]:
    if not table_exists(conn, "profile_brief") or not table_exists(conn, "skill_suggestion"):
        return []
    rows = conn.execute(
        """
        SELECT pb.id, pb.role, pb.domain
        FROM profile_brief pb
        WHERE EXISTS (SELECT 1 FROM skill_suggestion ss WHERE ss.brief_id = pb.id)
        ORDER BY pb.id DESC
        """
    ).fetchall()
    options = []
    for row in rows:
        state = get_brief_dag_state(conn, int(row["id"]))
        options.append(state)
    return options


def decode_uploaded_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def extract_docx_text(data: bytes) -> str:
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        document_xml = archive.read("word/document.xml")
    root = ET.fromstring(document_xml)
    for paragraph in root.findall(".//w:p", namespace):
        texts = [node.text for node in paragraph.findall(".//w:t", namespace) if node.text]
        line = "".join(texts).strip()
        if line:
            paragraphs.append(line)
    return "\n".join(paragraphs)


def extract_csv_text(data: bytes) -> str:
    decoded = decode_uploaded_text(data)
    sample = decoded[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel

    rows: list[str] = []
    reader = csv.reader(io.StringIO(decoded), dialect)
    for row in reader:
        cells = [cell.replace("\ufeff", "").strip() for cell in row]
        non_empty = [cell for cell in cells if cell]
        if not non_empty:
            continue
        if len(non_empty) == 1:
            rows.append(non_empty[0])
            continue
        head, tail = non_empty[0], non_empty[1:]
        if len(tail) == 1:
            rows.append(f"{head}: {tail[0]}")
            continue
        rows.append(f"{head}: {' | '.join(tail)}")
    return "\n\n".join(rows)


def extract_brief_text_from_bytes(data: bytes, suffix: str) -> str:
    if suffix in {".txt", ".md"}:
        return decode_uploaded_text(data).strip()
    if suffix == ".csv":
        return extract_csv_text(data).strip()
    if suffix == ".docx":
        return extract_docx_text(data).strip()
    raise ValueError("Поддерживаются только файлы .txt, .md, .csv и .docx.")


def load_brief_text(
    form_data: dict[str, str],
    files: dict[str, UploadedFile],
) -> tuple[str, str | None, str, str | None]:
    """Resolve the intake brief from an uploaded file or pasted text.

    Server-side filesystem paths are intentionally NOT accepted here: reading an
    arbitrary process-local path from web form data was a local file-disclosure
    vector, so only multipart upload (``brief_file``) and pasted ``brief`` text are
    supported. The 4th tuple element (legacy ``file_path``) is always ``None``.
    """
    uploaded_file = files.get("brief_file")
    if uploaded_file:
        suffix = Path(uploaded_file.filename).suffix.casefold()
        brief_text = extract_brief_text_from_bytes(uploaded_file.data, suffix)
        return brief_text, uploaded_file.filename, "file", None

    brief_text = form_data.get("brief", "").strip()
    if brief_text:
        return brief_text, None, "text", None

    return "", None, "text", None


def run_intake_pipeline(
    conn: CatalogConnection,
    db_path: Path,
    brief_text: str,
    intake_job_id: int | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    from content_factory.catalog.pipeline import config as intake_config
    from content_factory.catalog.pipeline import llm as intake_llm
    from content_factory.catalog.pipeline import stage_brief_to_catalog, stage_normalize, storage
    from content_factory.catalog.pipeline.catalog_repo import CatalogRepo

    ensure_intake_runtime_schema(conn, db_path)

    def notify(stage: str, note: str) -> None:
        if progress_callback:
            progress_callback(stage, note)

    repo = CatalogRepo(str(db_path))
    try:
        intake_llm.set_usage_context(job_id=intake_job_id, brief_id=None, stage="decompose")
        notify("decompose", "Декомпозиция свободного брифа в роль, уровень и поисковые подзапросы.")
        spec = stage_brief_to_catalog.decompose(brief_text)

        intake_llm.set_usage_context(job_id=intake_job_id, brief_id=None, stage="draft")
        notify("draft", "Черновик навыков из брифа без внешнего поиска.")
        raw_candidates, coverage = stage_brief_to_catalog.synthesize_draft_from_brief(brief_text, spec)

        intake_llm.set_usage_context(job_id=intake_job_id, brief_id=None, stage="atomize")
        notify("atomize", "Проверка атомарности кандидатов, разбиение составных формулировок и реклассификация не-навыков.")
        atomized_candidates = stage_brief_to_catalog.atomize_candidates(raw_candidates, spec)

        intake_llm.set_usage_context(job_id=intake_job_id, brief_id=None, stage="normalize")
        notify("normalize", "Нормализация названий и безопасное схлопывание дублирующих atomic skills.")
        candidates, normalize_report = stage_normalize.run(atomized_candidates, spec)

        intake_llm.set_usage_context(job_id=intake_job_id, brief_id=None, stage="resolve")
        notify("resolve", "Сопоставление навыков-кандидатов с текущим каталогом.")
        evidence: list[Evidence] = []
        stage_brief_to_catalog.resolve_candidates(candidates, evidence, repo)

        gray_candidates = stage_brief_to_catalog.select_evidence_enrichment_candidates(candidates)
        if gray_candidates:
            intake_llm.set_usage_context(job_id=intake_job_id, brief_id=None, stage="search")
            notify("search", f"Сбор external evidence только для серой зоны: {len(gray_candidates)} кандидатов.")
            evidence = stage_brief_to_catalog.gather_evidence_for_gray_zone(candidates, spec, cache_conn=conn)
            intake_llm.set_usage_context(job_id=intake_job_id, brief_id=None, stage="resolve")
            notify("resolve", "Повторный резолв после evidence enrichment серой зоны.")
            stage_brief_to_catalog.resolve_candidates(candidates, evidence, repo)
        else:
            notify("search", "Внешний поиск не потребовался: кандидаты закрылись текущим каталогом.")

        coverage = stage_brief_to_catalog.build_coverage_audit(spec, candidates, normalize_report=normalize_report)
        council_metrics_preview = {
            "sent_to_council": len(stage_brief_to_catalog.select_council_candidates(candidates)),
        }
        if intake_config.USE_COUNCIL and council_metrics_preview["sent_to_council"] > 0:
            intake_llm.set_usage_context(job_id=intake_job_id, brief_id=None, stage="council")
            notify(
                "council",
                f"Экспертное жюри проверяет спорные навыки: {council_metrics_preview['sent_to_council']} кандидатов.",
            )
            stage_brief_to_catalog.run_council(candidates)
        else:
            notify("council", "Council не потребовался: спорных навыков для panel нет.")
        intake_llm.set_usage_context(job_id=intake_job_id, brief_id=None, stage="triage")
        notify("triage", "Финальный триаж: что принять автоматически, а что отправить на review.")
        stage_brief_to_catalog.triage_candidates(candidates, spec)
        candidate_metrics = stage_brief_to_catalog.build_candidate_metrics(candidates)
    finally:
        intake_llm.clear_usage_context()
        repo.close()

    notify("persist", "Запись результатов в каталог и очередь проверки.")
    brief_id = storage.save_brief(conn, brief_text, spec)
    evidence_map = storage.save_evidence(conn, brief_id, evidence)
    tmp_to_db = storage.save_suggestions(conn, brief_id, candidates, evidence_map)
    by_tid = {candidate.tmp_id: candidate for candidate in candidates}
    atomize_events = []
    for candidate in atomized_candidates:
        if candidate.atomicity == "composite":
            atomize_events.append(
                {
                    "parent_name": candidate.name,
                    "verdict": "composite",
                    "children": [child.name for child in atomized_candidates if child.parent_tmp_id == candidate.tmp_id],
                    "rationale": candidate.atomize_rationale,
                }
            )
        elif candidate.atomicity == "non_skill":
            atomize_events.append(
                {
                    "parent_name": candidate.name,
                    "verdict": "non_skill",
                    "entity_type": candidate.entity_type,
                    "children": [],
                    "rationale": candidate.atomize_rationale,
                }
            )

    notify("ready_for_review", "Intake-анализ завершён. Методолог принимает skills, затем явно применяет решения в справочник.")
    dag_state = get_brief_dag_state(conn, brief_id)
    dag_payload = build_deferred_dag_payload(
        dag_state,
        status="waiting_catalog",
        message="DAG ещё не строился: сначала завершите проверку skills и примените решения в справочник.",
    )
    curriculum_plan = build_deferred_curriculum_plan_payload(
        "УП ещё не строился: сначала примените проверенные skills в справочник, примите шаблоны УП и постройте DAG.",
        audience_level=str(spec.get("seniority") or "Начальный"),
    )

    return {
        "brief_id": brief_id,
        "spec": spec,
        "candidates": [
            {
                "name": candidate.name,
                "source_name": candidate.source_name,
                "group": candidate.group,
                "coverage_area": candidate.coverage_area or (
                    by_tid[candidate.parent_tmp_id].coverage_area
                    if candidate.parent_tmp_id and candidate.parent_tmp_id in by_tid
                    else None
                ),
                "bloom": candidate.bloom,
                "entity_type": candidate.entity_type,
                "atomicity": candidate.atomicity,
                "suggestion_id": tmp_to_db.get(candidate.tmp_id),
                "parent_tmp_id": candidate.parent_tmp_id,
                "parent_name": by_tid[candidate.parent_tmp_id].name if candidate.parent_tmp_id and candidate.parent_tmp_id in by_tid else None,
                "resolution": candidate.resolution,
                "canonical_name": candidate.canonical_name,
                "match_score": format_catalog_similarity(candidate.match_score)[0],
                "novelty_score": format_catalog_similarity(candidate.match_score)[1],
                "nearest_skill_id": candidate.nearest_skill_id,
                "nearest_name": candidate.nearest_name,
                "nearest_group": candidate.nearest_group,
                "similarity_hint": build_similarity_hint(candidate.match_score, candidate.resolution, bool(candidate.nearest_skill_id), candidate.reasons),
                "recommended_action": build_candidate_recommended_action(
                    candidate.match_score,
                    candidate.resolution,
                    bool(candidate.nearest_skill_id),
                    candidate.nearest_name,
                    candidate.reasons,
                    candidate.decision,
                ),
                "confidence": f"{candidate.confidence:.2f}" if candidate.confidence else "—",
                "council_agreement": None if candidate.council_agreement is None else f"{candidate.council_agreement:.2f}",
                "decision": candidate.decision,
                "review_status": "open" if candidate.decision == "needs_review" else ("resolved" if candidate.decision == "accepted" else "ignored"),
                "can_review_inline": candidate.entity_type == "skill" and candidate.atomicity == "atomic",
                "reasons": ", ".join(review_reason_label(reason) for reason in candidate.reasons) if candidate.reasons else "",
                "tools": ", ".join(candidate.tools) if candidate.tools else "—",
            }
            for candidate in candidates
            if candidate.atomicity in {"atomic", "non_skill"}
        ],
        "atomize": {
            "raw_count": len(raw_candidates),
            "atomic_count": len([candidate for candidate in atomized_candidates if candidate.atomicity == "atomic"]),
            "composite_count": len([candidate for candidate in atomized_candidates if candidate.atomicity == "composite"]),
            "non_skill_count": len([candidate for candidate in atomized_candidates if candidate.atomicity == "non_skill"]),
            "events": atomize_events,
        },
        "normalize": normalize_report,
        "coverage": coverage,
        "dag": dag_payload,
        "curriculum_plan": curriculum_plan,
        "persisted": {
            "evidence_source": len(evidence),
            "skill_suggestion": len(candidates),
            "skill_prerequisite": int(dag_payload.get("prerequisite_rows", 0) or 0),
            "prerequisite_reviews": int(dag_payload.get("prerequisite_review_rows", 0) or 0),
            "curriculum_plan_rows": int(curriculum_plan.get("row_count", 0) or 0),
            "review_open": int(dag_state["open_review_count"]),
            "catalog_promoted": 0,
            "catalog_reverted": 0,
            "template_proposals": 0,
        },
        "meta": {
            "use_live": intake_config.USE_LIVE,
            "use_council": intake_config.USE_COUNCIL,
            "model_plan": intake_config.MODEL_PLAN,
            "model_search": intake_config.MODEL_SEARCH,
            "model_panel": intake_config.MODEL_PANEL,
        },
        "council_metrics": candidate_metrics,
    }


def execute_intake_job(db_path: Path, job_id: int) -> None:
    owner = worker_identity()
    conn = open_catalog_connection(db_path)
    heartbeat_stop = threading.Event()
    heartbeat_thread: threading.Thread | None = None
    try:
        _ensure_intake_review_schema(conn, db_path)
        # Atomically take the lease. If another worker holds it (or the job is gone),
        # do nothing — this is what prevents a double-run across workers/retries.
        if not claim_intake_job(conn, job_id, owner):
            return
        job = get_intake_job(conn, job_id)
        if not job:
            release_intake_job(conn, job_id, owner)
            return
        update_intake_job(conn, job_id, progress_note="Запуск intake-пайплайна.")

        def _run_heartbeat() -> None:
            heartbeat_conn = open_catalog_connection(db_path)
            try:
                while not heartbeat_stop.wait(HEARTBEAT_INTERVAL_SECONDS):
                    heartbeat_intake_job(heartbeat_conn, job_id, owner)
            finally:
                heartbeat_conn.close()

        heartbeat_thread = threading.Thread(
            target=_run_heartbeat, name=f"intake-hb-{job_id}", daemon=True
        )
        heartbeat_thread.start()

        def progress(stage: str, note: str) -> None:
            worker_conn = open_catalog_connection(db_path)
            try:
                _ensure_intake_review_schema(worker_conn, db_path)
                update_intake_job(worker_conn, job_id, current_stage=stage, progress_note=note)
            finally:
                worker_conn.close()

        result = run_intake_pipeline(
            conn,
            db_path,
            str(job["brief_text"]),
            intake_job_id=job_id,
            progress_callback=progress,
        )
        update_intake_job(
            conn,
            job_id,
            status="succeeded",
            current_stage="completed",
            progress_note="Обработка завершена.",
            result_payload=result,
            mark_finished=True,
        )
        release_intake_job(conn, job_id, owner)
    except Exception as exc:
        update_intake_job(
            conn,
            job_id,
            status="failed",
            current_stage="failed",
            progress_note="Пайплайн завершился с ошибкой.",
            error_text=str(exc),
            mark_finished=True,
        )
        try:
            release_intake_job(conn, job_id, owner)
        except Exception:
            pass
    finally:
        heartbeat_stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=5)
        conn.close()


def queue_intake_job(db_path: Path, job_id: int) -> None:
    INTAKE_EXECUTOR.submit(execute_intake_job, db_path, job_id)


def update_review_status(conn: CatalogConnection, review_id: int, new_status: str, resolution_note: str) -> None:
    from content_factory.catalog.pipeline import competency_catalog, storage

    repair_intake_review_links(conn)
    review_row = conn.execute(
        """
        SELECT id, entity_type, entity_id, source_ref, reason_code, details
        FROM review_queue
        WHERE id = ?
        """,
        (review_id,),
    ).fetchone()
    if not review_row:
        return

    reviewed_at = datetime.now(UTC).isoformat() if new_status != "open" else None
    conn.execute(
        """
        UPDATE review_queue
        SET status = ?,
            resolution_note = ?,
            reviewed_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (new_status, resolution_note.strip() or None, reviewed_at, datetime.now(UTC).isoformat(), review_id),
    )
    brief_id = parse_brief_id(review_row["source_ref"])
    suggestion_id = review_row["entity_id"]
    details = parse_review_details_json(review_row["details"])
    rebuild_after_review = False
    if review_row["entity_type"] == "competency" and suggestion_id:
        competency_id = int(suggestion_id)
        if new_status == "resolved":
            competency_catalog.resolve_competency_candidate(conn, competency_id=competency_id, accepted=True)
        elif new_status == "ignored":
            competency_catalog.resolve_competency_candidate(conn, competency_id=competency_id, accepted=False)
        else:
            competency_catalog.reopen_competency_candidate(conn, competency_id=competency_id)
    elif brief_id is not None and details.get("review_kind") == "prerequisite_edge":
        if new_status in {"resolved", "ignored"}:
            save_prerequisite_edge_decision(
                conn,
                brief_id=brief_id,
                details=details,
                decision="accepted" if new_status == "resolved" else "rejected",
                resolution_note=resolution_note,
            )
        elif table_exists(conn, "prerequisite_edge_decision"):
            conn.execute(
                "DELETE FROM prerequisite_edge_decision WHERE brief_id = ? AND edge_key = ?",
                (brief_id, str(details.get("edge_key") or "")),
            )
        clear_brief_dag_artifacts(conn, brief_id)
    elif suggestion_id and brief_id is not None:
        mapped_decision = "needs_review"
        if new_status == "resolved":
            mapped_decision = "accepted"
        elif new_status == "ignored":
            mapped_decision = "rejected"
        conn.execute(
            "UPDATE skill_suggestion SET decision = ? WHERE id = ?",
            (mapped_decision, suggestion_id),
        )
        if mapped_decision == "accepted":
            storage.promote_suggestion_to_catalog(conn, suggestion_id)
        else:
            storage.revert_suggestion_promotion(conn, suggestion_id)
        clear_brief_dag_artifacts(conn, brief_id)
        rebuild_after_review = True
    conn.commit()
    if brief_id is not None and rebuild_after_review:
        build_dag_for_brief(conn, brief_id)


def clear_intake_workspace(conn: CatalogConnection) -> dict[str, int]:
    """Clear transient intake artifacts while keeping canonical catalog tables intact."""
    from content_factory.catalog.pipeline import competency_catalog
    from content_factory.catalog.pipeline import storage as intake_storage

    stats: dict[str, int] = {}
    intake_competency_ids: set[int] = set()

    if table_exists(conn, "review_queue"):
        intake_competency_ids.update(
            int(row["entity_id"])
            for row in conn.execute(
                """
                SELECT entity_id
                FROM review_queue
                WHERE entity_type = 'competency'
                  AND entity_id IS NOT NULL
                  AND source_ref LIKE 'intake_accept:%'
                """
            ).fetchall()
        )

    if table_exists(conn, "profile_competency") and table_exists(conn, "profile"):
        intake_competency_ids.update(
            int(row["competency_id"])
            for row in conn.execute(
                """
                SELECT DISTINCT pc.competency_id
                FROM profile_competency pc
                JOIN profile p ON p.id = pc.profile_id
                WHERE p.slug = ?
                """,
                (competency_catalog.SERVICE_PROFILE_SLUG,),
            ).fetchall()
            if row["competency_id"] is not None
        )

    if table_exists(conn, "skill_promotion_log"):
        active_promotions = conn.execute(
            """
            SELECT suggestion_id
            FROM skill_promotion_log
            WHERE status = 'active'
            ORDER BY id
            """
        ).fetchall()
        reverted = 0
        for row in active_promotions:
            result = intake_storage.revert_suggestion_promotion(conn, int(row["suggestion_id"]))
            if result.get("status") == "reverted":
                reverted += 1
        stats["skill_promotions_reverted"] = reverted

    if table_exists(conn, "indicator"):
        indicator_cols = table_columns(conn, "indicator")
        clauses = []
        params: list[object] = []
        if "source_scale_title" in indicator_cols:
            clauses.append("source_scale_title = 'intake-live'")
        if "source_profile_name" in indicator_cols:
            clauses.append("source_profile_name = ?")
            params.append(competency_catalog.SERVICE_PROFILE_NAME)
        if clauses:
            stats["indicator_intake"] = conn.execute(
                f"DELETE FROM indicator WHERE {' OR '.join(clauses)}",
                tuple(params),
            ).rowcount

    if table_exists(conn, "indicator_level_cell") and table_exists(conn, "indicator_row"):
        stats["indicator_level_cell_intake"] = conn.execute(
            """
            DELETE FROM indicator_level_cell
            WHERE indicator_row_id IN (
                SELECT id FROM indicator_row WHERE COALESCE(notes, '') LIKE 'intake_accept:%'
            )
            """
        ).rowcount

    if table_exists(conn, "indicator_row"):
        stats["indicator_row_intake"] = conn.execute(
            "DELETE FROM indicator_row WHERE COALESCE(notes, '') LIKE 'intake_accept:%'"
        ).rowcount

    if table_exists(conn, "competency_skill") and table_exists(conn, "profile_competency") and table_exists(conn, "profile"):
        stats["competency_skill_intake"] = conn.execute(
            """
            DELETE FROM competency_skill
            WHERE id IN (
                SELECT cs.id
                FROM competency_skill cs
                JOIN profile_competency pc ON pc.id = cs.profile_competency_id
                JOIN profile p ON p.id = pc.profile_id
                WHERE p.slug = ?
            )
            """,
            (competency_catalog.SERVICE_PROFILE_SLUG,),
        ).rowcount

    if table_exists(conn, "profile_competency") and table_exists(conn, "profile"):
        stats["profile_competency_intake_orphan"] = conn.execute(
            """
            DELETE FROM profile_competency
            WHERE id IN (
                SELECT pc.id
                FROM profile_competency pc
                JOIN profile p ON p.id = pc.profile_id
                WHERE p.slug = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM competency_skill cs WHERE cs.profile_competency_id = pc.id
                  )
            )
            """,
            (competency_catalog.SERVICE_PROFILE_SLUG,),
        ).rowcount

    if table_exists(conn, "profile"):
        stats["profile_intake_empty"] = conn.execute(
            """
            DELETE FROM profile
            WHERE slug = ?
              AND NOT EXISTS (
                  SELECT 1 FROM profile_competency pc WHERE pc.profile_id = profile.id
              )
            """,
            (competency_catalog.SERVICE_PROFILE_SLUG,),
        ).rowcount

    if intake_competency_ids and table_exists(conn, "competency") and table_exists(conn, "profile_competency"):
        ordered_intake_competency_ids = sorted(intake_competency_ids)
        placeholders = ", ".join("?" for _ in ordered_intake_competency_ids)
        stats["competency_intake_candidate_orphan"] = conn.execute(
            f"""
            DELETE FROM competency
            WHERE id IN ({placeholders})
              AND NOT EXISTS (
                  SELECT 1 FROM profile_competency pc WHERE pc.competency_id = competency.id
              )
            """,
            tuple(ordered_intake_competency_ids),
        ).rowcount

    if table_exists(conn, "review_queue"):
        stats["review_queue"] = conn.execute(
            """
            DELETE FROM review_queue
            WHERE source_ref LIKE 'brief:%'
               OR source_ref LIKE 'intake_accept:%'
            """
        ).rowcount

    if table_exists(conn, "skill_prerequisite") and column_exists(conn, "skill_prerequisite", "brief_id"):
        stats["skill_prerequisite"] = conn.execute("DELETE FROM skill_prerequisite WHERE brief_id IS NOT NULL").rowcount

    if table_exists(conn, "prerequisite_edge_decision") and column_exists(conn, "prerequisite_edge_decision", "brief_id"):
        stats["prerequisite_edge_decision"] = conn.execute(
            "DELETE FROM prerequisite_edge_decision WHERE brief_id IS NOT NULL"
        ).rowcount

    if table_exists(conn, "skill_set") and table_exists(conn, "skill_set_item"):
        runtime_skill_sets = conn.execute(
            """
            SELECT id
            FROM skill_set
            WHERE source_type IN ('brief', 'curriculum_plan')
               OR source_ref LIKE 'brief:%'
               OR source_ref LIKE '%;brief:%'
            """
        ).fetchall()
        runtime_skill_set_ids = [int(row["id"]) for row in runtime_skill_sets]
        if runtime_skill_set_ids:
            placeholders = ", ".join("?" for _ in runtime_skill_set_ids)
            stats["skill_set_item_runtime"] = conn.execute(
                f"DELETE FROM skill_set_item WHERE skill_set_id IN ({placeholders})",
                tuple(runtime_skill_set_ids),
            ).rowcount
            stats["skill_set_runtime"] = conn.execute(
                f"DELETE FROM skill_set WHERE id IN ({placeholders})",
                tuple(runtime_skill_set_ids),
            ).rowcount

    if table_exists(conn, "curriculum_plan_row") and table_exists(conn, "curriculum_plan"):
        stats["curriculum_plan_row"] = conn.execute(
            """
            DELETE FROM curriculum_plan_row
            WHERE plan_id IN (SELECT id FROM curriculum_plan WHERE brief_id IS NOT NULL)
            """
        ).rowcount

    if table_exists(conn, "curriculum_plan"):
        stats["curriculum_plan"] = conn.execute("DELETE FROM curriculum_plan WHERE brief_id IS NOT NULL").rowcount

    if table_exists(conn, "curriculum_artifact_template_proposal"):
        stats["curriculum_artifact_template_proposal"] = conn.execute(
            "DELETE FROM curriculum_artifact_template_proposal WHERE brief_id IS NOT NULL"
        ).rowcount

    if table_exists(conn, "skill_suggestion"):
        stats["skill_suggestion"] = conn.execute("DELETE FROM skill_suggestion WHERE brief_id IS NOT NULL").rowcount

    if table_exists(conn, "skill_promotion_log"):
        stats["skill_promotion_log"] = conn.execute("DELETE FROM skill_promotion_log").rowcount

    if table_exists(conn, "evidence_source"):
        stats["evidence_source"] = conn.execute("DELETE FROM evidence_source WHERE brief_id IS NOT NULL").rowcount

    if table_exists(conn, "evidence_query_cache"):
        stats["evidence_query_cache"] = conn.execute("DELETE FROM evidence_query_cache").rowcount

    if table_exists(conn, "intake_job"):
        stats["intake_job"] = conn.execute("DELETE FROM intake_job").rowcount

    if table_exists(conn, "profile_brief"):
        stats["profile_brief"] = conn.execute("DELETE FROM profile_brief").rowcount

    prune_stats = prune_empty_generated_catalog_nodes(conn)
    stats.update({f"prune_{key}": value for key, value in prune_stats.items()})
    conn.commit()
    return {key: int(value or 0) for key, value in stats.items()}


def count_open_candidate_competencies(conn: CatalogConnection) -> int:
    if not table_exists(conn, "review_queue"):
        return 0
    return int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM review_queue
            WHERE entity_type = 'competency'
              AND status = 'open'
              AND source_ref LIKE 'intake_accept:%'
            """
        ).fetchone()[0]
    )


def list_reviews(
    conn: CatalogConnection,
    status_filter: str,
    severity_filter: str,
    reason_filter: str,
    entity_type_filter: str = "all",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]], list[dict[str, str]]]:
    repair_intake_review_links(conn)
    params: list[object] = []
    where_parts: list[str] = []
    if status_filter != "all":
        where_parts.append("status = ?")
        params.append(status_filter)
    if severity_filter != "all":
        where_parts.append("severity = ?")
        params.append(severity_filter)
    if reason_filter != "all":
        where_parts.append("reason_code = ?")
        params.append(reason_filter)
    if entity_type_filter != "all":
        where_parts.append("entity_type = ?")
        params.append(entity_type_filter)
    where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    status_totals = fetch_all(
        conn,
        """
        SELECT status, COUNT(*) AS cnt
        FROM review_queue
        GROUP BY status
        ORDER BY CASE status
            WHEN 'open' THEN 1
            WHEN 'resolved' THEN 2
            WHEN 'ignored' THEN 3
            ELSE 4
        END
        """,
    )
    for item in status_totals:
        item["status_label"] = review_status_label(str(item["status"]))

    breakdown = fetch_all(
        conn,
        f"""
        SELECT reason_code, severity, COUNT(*) AS cnt
        FROM review_queue
        {where_clause}
        GROUP BY reason_code, severity
        ORDER BY cnt DESC, reason_code
        """,
        tuple(params),
    )
    for item in breakdown:
        item["reason_label"] = review_reason_label(str(item["reason_code"]))
        item["severity_label"] = review_severity_label(str(item["severity"]))

    items = fetch_all(
        conn,
        f"""
        SELECT id, entity_type, entity_id, source_ref, reason_code, severity, details, status, resolution_note, created_at, reviewed_at
        FROM review_queue
        {where_clause}
        ORDER BY
            CASE severity
                WHEN 'error' THEN 1
                WHEN 'warning' THEN 2
                ELSE 3
            END,
            created_at DESC
        LIMIT 500
        """,
        tuple(params),
    )
    for item in items:
        item["reason_label"] = review_reason_label(str(item["reason_code"]))
        item["severity_label"] = review_severity_label(str(item["severity"]))
        item["status_label"] = review_status_label(str(item["status"]))
        format_prerequisite_edge_review(item)

    reason_options = [
        {"code": row["reason_code"], "label": review_reason_label(str(row["reason_code"]))}
        for row in conn.execute("SELECT DISTINCT reason_code FROM review_queue ORDER BY reason_code")
    ]
    entity_type_options = [
        {"code": row["entity_type"], "label": review_entity_label(str(row["entity_type"]))}
        for row in conn.execute("SELECT DISTINCT entity_type FROM review_queue ORDER BY entity_type")
    ]
    return status_totals, breakdown, items, reason_options, entity_type_options
