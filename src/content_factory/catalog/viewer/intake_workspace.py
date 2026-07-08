"""UI/workspace hydration for intake jobs.

This module owns deterministic candidate hints, job result hydration, workflow
steps, and readiness/blocker state for the intake UI. It reads catalog/DAG/review
state but does not execute the intake pipeline or mutate catalog decisions.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from content_factory.catalog.db import CatalogConnection, CatalogRow
from content_factory.catalog.viewer._common import (
    _as_dict,
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
from content_factory.catalog.viewer.intake_reviews import (
    count_open_candidate_competencies,
    count_open_prerequisite_edge_reviews_for_brief,
    count_open_skill_reviews_for_brief,
)
from content_factory.catalog.viewer.labels import review_reason_label
from content_factory.catalog.viewer.observability import build_decision_rationale


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
