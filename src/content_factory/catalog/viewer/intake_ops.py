"""Intake pipeline assembly.

Owns the deterministic brief -> candidates -> persisted review payload flow.
Runtime execution/leases live in ``intake_runtime``; UI hydration lives in
``intake_workspace``; cleanup lives in ``intake_cleanup``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from content_factory.catalog.db import CatalogConnection
from content_factory.catalog.viewer._common import format_catalog_similarity
from content_factory.catalog.viewer.curriculum_ops import build_deferred_curriculum_plan_payload
from content_factory.catalog.viewer.intake_dag import build_deferred_dag_payload, get_brief_dag_state
from content_factory.catalog.viewer.intake_workspace import (
    build_candidate_recommended_action,
    build_similarity_hint,
)
from content_factory.catalog.viewer.labels import review_reason_label

if TYPE_CHECKING:
    from content_factory.catalog.pipeline.models import Evidence


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
    from content_factory.catalog.viewer.intake_runtime import ensure_intake_runtime_schema

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
