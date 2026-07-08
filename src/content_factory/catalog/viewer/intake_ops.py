"""Intake pipeline runtime hub.

Owns intake preflight/recovery, background job dispatch and execution, and
workspace cleanup. UI hydration lives in ``intake_workspace``; DAG/UP build
operations live in ``intake_dag``. No back-import of ``app.py`` (module stays
acyclic); ``app.py`` re-exports legacy symbols for older callers.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from content_factory.catalog.db import (
    CatalogConnection,
    catalog_database_url,
    open_catalog_connection,
    resolve_backend,
)
from content_factory.catalog.viewer._common import (
    column_exists,
    format_catalog_similarity,
    table_columns,
    table_exists,
)
from content_factory.catalog.viewer.curriculum_ops import build_deferred_curriculum_plan_payload
from content_factory.catalog.viewer.intake_dag import build_deferred_dag_payload, get_brief_dag_state
from content_factory.catalog.viewer.intake_jobs import (
    get_intake_job,
    update_intake_job,
)
from content_factory.catalog.viewer.intake_reviews import repair_intake_review_links
from content_factory.catalog.viewer.intake_worker import (
    HEARTBEAT_INTERVAL_SECONDS,
    claim_intake_job,
    heartbeat_intake_job,
    reclaim_expired_intake_jobs,
    release_intake_job,
    worker_identity,
)
from content_factory.catalog.viewer.intake_workspace import (
    build_candidate_recommended_action,
    build_similarity_hint,
)
from content_factory.catalog.viewer.labels import review_reason_label

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
