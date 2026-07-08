"""Intake pipeline hub — pipeline orchestration, DAG build, and workspace state.

Extracted from ``viewer/app.py`` (slice 6, the final domain slice). The intake hub:
queues/executes intake jobs (background ThreadPoolExecutor), runs the brief ->
catalog -> DAG -> curriculum pipeline stages, builds DAG/apply-decision flows,
and assembles intake workspace/workflow state. DAG/UP build operations live in
``intake_dag``. Consumed by ``catalog/web/routers/intake.py`` and ``reviews.py``.
Depends on shared helpers in ``_common`` / ``labels`` / ``observability`` /
``curriculum_ops`` / ``intake_dag`` / ``intake_reviews`` + stdlib; all pipeline module
aliases are local imports inside the bodies. Owns the intake runtime-state globals. No
back-import of ``app.py`` (module stays acyclic); ``app.py`` re-exports legacy symbols
for the routers.
"""

from __future__ import annotations

import hashlib
import re
import threading
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
    _as_dict,
    column_exists,
    format_catalog_similarity,
    table_columns,
    table_exists,
)
from content_factory.catalog.viewer.curriculum_ops import build_deferred_curriculum_plan_payload
from content_factory.catalog.viewer.intake_catalog_apply import load_brief_catalog_promotion_summary
from content_factory.catalog.viewer.intake_dag import (
    build_deferred_dag_payload,
    get_brief_catalog_apply_state,
    get_brief_dag_state,
)
from content_factory.catalog.viewer.intake_jobs import (
    get_intake_job,
    update_intake_job,
)
from content_factory.catalog.viewer.intake_reviews import (
    count_open_candidate_competencies,
    count_open_prerequisite_edge_reviews_for_brief,
    count_open_skill_reviews_for_brief,
    repair_intake_review_links,
)
from content_factory.catalog.viewer.intake_worker import (
    HEARTBEAT_INTERVAL_SECONDS,
    claim_intake_job,
    heartbeat_intake_job,
    reclaim_expired_intake_jobs,
    release_intake_job,
    worker_identity,
)
from content_factory.catalog.viewer.labels import review_reason_label
from content_factory.catalog.viewer.observability import build_decision_rationale

if TYPE_CHECKING:
    from content_factory.catalog.pipeline.models import Evidence


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
