"""Curriculum artifact templates and human-reviewable template proposals.

Methodologist-owned artifact templates for the UP (учебный план) planner plus
the proposal workflow that turns accepted skills into reviewable template
drafts (deterministic fallback + optional LLM consilium). Extracted from
``catalog/pipeline/storage.py``; ``storage`` re-exports the public functions so
the ``storage.foo`` / ``intake_storage.foo`` call sites stay unchanged.
"""

from __future__ import annotations

import json
import re
from typing import Any

from content_factory.catalog.db import CatalogConnection, CatalogRow
from content_factory.catalog.pipeline._storage_common import (
    _json_list,
    _normalize_catalog_key,
    _slug_catalog_key,
    _table_exists,
    _utc_now_iso,
)

from . import config


def load_curriculum_artifact_templates(con: CatalogConnection, active_only: bool = True) -> list[dict[str, Any]]:
    """Load active methodologist-managed artifact templates for the UP planner."""
    if not _table_exists(con, "curriculum_artifact_template"):
        return []
    status_filter = "WHERE status = 'active'" if active_only else ""
    rows = con.execute(
        f"""
        SELECT id, code, title, artifact_family, artifact_description,
               project_name_pattern, materials_pattern, storytelling_pattern,
               validation_criteria, priority, status, source
        FROM curriculum_artifact_template
        {status_filter}
        ORDER BY priority ASC, id ASC
        """
    ).fetchall()
    templates: list[dict[str, Any]] = []
    for row in rows:
        template = dict(row)
        template_id = int(template["id"])
        scopes: list[dict[str, Any]] = []
        if _table_exists(con, "curriculum_artifact_template_scope"):
            scope_rows = con.execute(
                """
                SELECT scope_type, scope_id, scope_name, normalized_scope_name, weight
                FROM curriculum_artifact_template_scope
                WHERE template_id = ?
                ORDER BY weight DESC, id ASC
                """,
                (template_id,),
            ).fetchall()
            for scope_row in scope_rows:
                scope = dict(scope_row)
                if not scope.get("normalized_scope_name") and scope.get("scope_name"):
                    scope["normalized_scope_name"] = _normalize_catalog_key(str(scope["scope_name"]))
                scopes.append(scope)
        template["scopes"] = scopes
        templates.append(template)
    return templates


def upsert_curriculum_artifact_template(
    con: CatalogConnection,
    *,
    code: str,
    title: str,
    artifact_family: str,
    artifact_description: str,
    project_name_pattern: str = "",
    materials_pattern: str = "",
    storytelling_pattern: str = "",
    validation_criteria: str = "",
    priority: int = 100,
    status: str = "active",
    source: str = "manual",
    scopes: list[dict[str, Any]] | None = None,
) -> int:
    """Create or update a methodology-owned artifact template."""
    normalized_code = _slug_catalog_key(code or title)
    con.execute(
        """
        INSERT INTO curriculum_artifact_template(
            code, title, artifact_family, artifact_description, project_name_pattern,
            materials_pattern, storytelling_pattern, validation_criteria, priority,
            status, source, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            title = excluded.title,
            artifact_family = excluded.artifact_family,
            artifact_description = excluded.artifact_description,
            project_name_pattern = excluded.project_name_pattern,
            materials_pattern = excluded.materials_pattern,
            storytelling_pattern = excluded.storytelling_pattern,
            validation_criteria = excluded.validation_criteria,
            priority = excluded.priority,
            status = excluded.status,
            source = excluded.source,
            updated_at = excluded.updated_at
        """,
        (
            normalized_code,
            title,
            artifact_family,
            artifact_description,
            project_name_pattern,
            materials_pattern,
            storytelling_pattern,
            validation_criteria,
            priority,
            status,
            source,
            _utc_now_iso(),
        ),
    )
    row = con.execute("SELECT id FROM curriculum_artifact_template WHERE code = ?", (normalized_code,)).fetchone()
    template_id = int(row["id"])
    con.execute("DELETE FROM curriculum_artifact_template_scope WHERE template_id = ?", (template_id,))
    for scope in scopes or []:
        scope_type = str(scope.get("scope_type") or "coverage_area").strip()
        scope_name = str(scope.get("scope_name") or "").strip()
        normalized_scope_name = str(scope.get("normalized_scope_name") or "").strip() or _normalize_catalog_key(scope_name)
        con.execute(
            """
            INSERT INTO curriculum_artifact_template_scope(
                template_id, scope_type, scope_id, scope_name, normalized_scope_name, weight
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                template_id,
                scope_type,
                scope.get("scope_id"),
                scope_name or None,
                normalized_scope_name or None,
                float(scope.get("weight", 1.0) or 1.0),
            ),
        )
    con.commit()
    return template_id


def load_curriculum_artifact_template_proposals(
    con: CatalogConnection,
    brief_id: int,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Load human-reviewable UP template proposals for a brief."""
    if not _table_exists(con, "curriculum_artifact_template_proposal"):
        return []
    params: list[object] = [brief_id]
    status_filter = ""
    if status:
        status_filter = "AND status = ?"
        params.append(status)
    rows = con.execute(
        f"""
        SELECT id, brief_id, plan_id, status, code, title, artifact_family,
               scope_type, scope_names_json, artifact_description,
               project_name_pattern, materials_pattern, storytelling_pattern,
               validation_criteria, covered_skill_ids_json, covered_skill_names_json,
               rationale, confidence, source, accepted_template_id, created_at, updated_at
        FROM curriculum_artifact_template_proposal
        WHERE brief_id = ?
          {status_filter}
        ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'accepted' THEN 1 ELSE 2 END,
                 id ASC
        """,
        tuple(params),
    ).fetchall()
    proposals: list[dict[str, Any]] = []
    for row in rows:
        proposal = dict(row)
        proposal["scope_names"] = [str(item) for item in _json_list(proposal.get("scope_names_json")) if str(item).strip()]
        proposal["covered_skill_ids"] = [int(item) for item in _json_list(proposal.get("covered_skill_ids_json")) if str(item).strip().isdigit()]
        proposal["covered_skill_names"] = [str(item) for item in _json_list(proposal.get("covered_skill_names_json")) if str(item).strip()]
        proposals.append(proposal)
    return proposals


def _artifact_family_for_scope(scope_name: str, skill_names: list[str]) -> str:
    scope_text = _normalize_catalog_key(scope_name)
    full_text = _normalize_catalog_key(" ".join([scope_name, *skill_names]))
    if any(token in scope_text for token in ["инженер", "репозитор", "ci", "тест", "инфраструкт", "развер", "наблюдаем", "резерв", "инцидент"]):
        return "configuration"
    if any(token in scope_text for token in ["архитект", "mvp", "приорит"]):
        return "design"
    if any(token in scope_text for token in ["workflow", "автоматизац", "рабочих процессов"]):
        return "production"
    if any(token in scope_text for token in ["интерв", "исслед", "гипотез", "эксперимент", "инсайт", "выявление", "контроль качества"]):
        return "analysis"
    if any(token in scope_text for token in ["маркет", "продаж", "монет", "правов", "финансов", "стратег", "okr", "позиционир", "ценност"]):
        return "document"
    if "поддерж" in scope_text or "обрат" in scope_text:
        return "practice"
    if any(token in full_text for token in ["ci", "репозитор", "тест", "развер", "монитор", "резерв", "инцидент"]):
        return "configuration"
    if any(token in full_text for token in ["интерв", "исслед", "гипотез", "эксперимент", "инсайт", "качество", "риск"]):
        return "analysis"
    return "practice"


def _compact_scope_title(scope_name: str) -> str:
    cleaned = re.sub(r"\s+", " ", scope_name).strip(" .;:")
    if not cleaned:
        return "проектный артефакт"
    parts = re.split(r"[:;,]", cleaned)
    title = parts[0].strip() if parts else cleaned
    words = title.split()
    return " ".join(words[:7]) if len(words) > 7 else title


def _proposal_title(scope_name: str, skill_names: list[str], family: str) -> str:
    family_titles = {
        "analysis": "Аналитический артефакт",
        "document": "Документальный артефакт",
        "configuration": "Конфигурационный артефакт",
        "design": "Проектный артефакт",
        "production": "Рабочий артефакт",
        "practice": "Практический артефакт",
    }
    return f"{family_titles.get(family, 'Проектный артефакт')}: {_compact_scope_title(scope_name)}"


def _proposal_payload(scope_name: str, skill_rows: list[CatalogRow]) -> dict[str, Any]:
    skill_names = [
        str(row["canonical_name"] or row["suggested_name"]).strip()
        for row in skill_rows
        if str(row["canonical_name"] or row["suggested_name"] or "").strip()
    ]
    skill_ids = [int(row["id"]) for row in skill_rows]
    family = _artifact_family_for_scope(scope_name, skill_names)
    title = _proposal_title(scope_name, skill_names, family)
    skills_placeholder = "{skills}"
    theme_placeholder = "{theme}"
    artifact_description = (
        f"{title.lower()} по теме «{theme_placeholder}»: студент предъявляет проверяемый результат, "
        f"в котором явно применены навыки {skills_placeholder}."
    )
    return {
        "code": f"proposal-{_slug_catalog_key(scope_name)}",
        "title": title,
        "artifact_family": family,
        "scope_type": "coverage_area",
        "scope_names": [scope_name],
        "artifact_description": artifact_description,
        "project_name_pattern": title,
        "materials_pattern": (
            f"Бриф продукта, шаблон артефакта «{title}», список навыков: {skills_placeholder}, "
            "рабочие документы/таблицы, LLM для черновиков и проверки полноты."
        ),
        "storytelling_pattern": (
            "Участник действует как основатель продукта: собирает рабочий артефакт для реального запуска, "
            "обосновывает решения данными и показывает, как применил навыки."
        ),
        "validation_criteria": (
            f"Артефакт «{title}» предъявлен; покрыты навыки {skills_placeholder}; есть проверяемые выводы, "
            "обоснования решений и следующий шаг для продукта."
        ),
        "covered_skill_ids": skill_ids,
        "covered_skill_names": skill_names,
        "rationale": (
            f"В брифе есть область «{scope_name}» и {len(skill_names)} accepted skills. "
            "Шаблон нужен, чтобы заменить generic-проект на проверяемый артефакт."
        ),
        "confidence": 0.78 if len(skill_names) >= 2 else 0.68,
        "source": "deterministic_fallback",
    }


def _brief_context_for_consilium(con: CatalogConnection, brief_id: int) -> dict[str, Any]:
    row = con.execute(
        """
        SELECT id, raw_text, role, seniority, domain, created_at
        FROM profile_brief
        WHERE id = ?
        """,
        (brief_id,),
    ).fetchone()
    return dict(row) if row else {"id": brief_id, "raw_text": "", "role": "", "seniority": "", "domain": ""}


def _scope_groups_for_consilium(grouped: dict[str, list[CatalogRow]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for scope_name, skill_rows in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        skills: list[dict[str, Any]] = []
        for row in skill_rows:
            skill_name = str(row["canonical_name"] or row["suggested_name"] or "").strip()
            skills.append(
                {
                    "id": int(row["id"]),
                    "name": skill_name,
                    "group_name": str(row["group_name"] or "").strip(),
                    "bloom": str(row["bloom"] or "").strip(),
                    "tools": str(row["tools"] or "").strip(),
                    "source_name": str(row["source_name"] or "").strip(),
                }
            )
        groups.append(
            {
                "scope_name": scope_name,
                "skill_count": len(skills),
                "skills": skills,
            }
        )
    return groups


def generate_curriculum_artifact_template_proposals(
    con: CatalogConnection,
    *,
    brief_id: int,
    plan_id: int | None = None,
    max_proposals: int = 10,
) -> list[dict[str, Any]]:
    """Generate idempotent template proposals from accepted skills.

    The preferred path is LLM consilium with strict prompts and validation.
    Deterministic generation remains a safe fallback for offline/dev runs.
    """
    if not _table_exists(con, "curriculum_artifact_template_proposal"):
        return []
    rows = con.execute(
        """
        SELECT ss.id, ss.suggested_name, ss.group_name, ss.coverage_area,
               ss.bloom, ss.tools, ss.source_name, ss.indicators_json,
               ss.canonical_skill_id, s.canonical_name
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
    grouped: dict[str, list[CatalogRow]] = {}
    for row in rows:
        scope_name = str(row["coverage_area"] or row["group_name"] or "Общее").strip()
        if not scope_name:
            continue
        grouped.setdefault(scope_name, []).append(row)

    fallback_payloads = [
        _proposal_payload(scope_name, skill_rows)
        for scope_name, skill_rows in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))[:max_proposals]
    ]
    payloads = fallback_payloads
    if config.USE_UP_TEMPLATE_CONSILIUM and grouped:
        try:
            from . import up_template_consilium

            payloads = up_template_consilium.propose(
                brief=_brief_context_for_consilium(con, brief_id),
                scope_groups=_scope_groups_for_consilium(grouped),
                max_proposals=max_proposals,
            )
        except Exception as exc:
            fallback_reason = f"LLM-консилиум недоступен или вернул некорректный JSON: {exc}"
            payloads = []
            for payload in fallback_payloads:
                payload = dict(payload)
                payload["source"] = "deterministic_fallback_after_llm_error"
                payload["rationale"] = f"{payload.get('rationale', '')} {fallback_reason}".strip()
                payloads.append(payload)

    proposals: list[dict[str, Any]] = []
    con.execute(
        """
        DELETE FROM curriculum_artifact_template_proposal
        WHERE brief_id = ?
          AND status = 'open'
        """,
        (brief_id,),
    )
    for payload in payloads:
        code = str(payload["code"])
        existing = con.execute(
            """
            SELECT id, status
            FROM curriculum_artifact_template_proposal
            WHERE brief_id = ? AND code = ?
            """,
            (brief_id, code),
        ).fetchone()
        if existing and str(existing["status"]) != "open":
            continue
        values = (
            brief_id,
            plan_id,
            "open",
            code,
            payload["title"],
            payload["artifact_family"],
            payload["scope_type"],
            json.dumps(payload["scope_names"], ensure_ascii=False),
            payload["artifact_description"],
            payload["project_name_pattern"],
            payload["materials_pattern"],
            payload["storytelling_pattern"],
            payload["validation_criteria"],
            json.dumps(payload["covered_skill_ids"], ensure_ascii=False),
            json.dumps(payload["covered_skill_names"], ensure_ascii=False),
            payload["rationale"],
            float(payload["confidence"]),
            str(payload.get("source") or "deterministic_fallback"),
            _utc_now_iso(),
        )
        con.execute(
            """
            INSERT INTO curriculum_artifact_template_proposal(
                brief_id, plan_id, status, code, title, artifact_family, scope_type,
                scope_names_json, artifact_description, project_name_pattern,
                materials_pattern, storytelling_pattern, validation_criteria,
                covered_skill_ids_json, covered_skill_names_json, rationale,
                confidence, source, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(brief_id, code) DO UPDATE SET
                plan_id = COALESCE(excluded.plan_id, curriculum_artifact_template_proposal.plan_id),
                title = excluded.title,
                artifact_family = excluded.artifact_family,
                scope_type = excluded.scope_type,
                scope_names_json = excluded.scope_names_json,
                artifact_description = excluded.artifact_description,
                project_name_pattern = excluded.project_name_pattern,
                materials_pattern = excluded.materials_pattern,
                storytelling_pattern = excluded.storytelling_pattern,
                validation_criteria = excluded.validation_criteria,
                covered_skill_ids_json = excluded.covered_skill_ids_json,
                covered_skill_names_json = excluded.covered_skill_names_json,
                rationale = excluded.rationale,
                confidence = excluded.confidence,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            values,
        )
        proposals.append(payload)
    con.commit()
    return load_curriculum_artifact_template_proposals(con, brief_id)


def update_curriculum_artifact_template_proposal(
    con: CatalogConnection,
    proposal_id: int,
    *,
    title: str,
    artifact_family: str,
    scope_type: str,
    scope_names: list[str],
    artifact_description: str,
    project_name_pattern: str,
    materials_pattern: str,
    storytelling_pattern: str,
    validation_criteria: str,
    rationale: str = "",
    confidence: float | None = None,
) -> None:
    con.execute(
        """
        UPDATE curriculum_artifact_template_proposal
        SET title = ?,
            artifact_family = ?,
            scope_type = ?,
            scope_names_json = ?,
            artifact_description = ?,
            project_name_pattern = ?,
            materials_pattern = ?,
            storytelling_pattern = ?,
            validation_criteria = ?,
            rationale = COALESCE(NULLIF(?, ''), rationale),
            confidence = COALESCE(?, confidence),
            updated_at = ?
        WHERE id = ?
        """,
        (
            title.strip() or "Шаблон УП",
            artifact_family,
            scope_type,
            json.dumps([name.strip() for name in scope_names if name.strip()], ensure_ascii=False),
            artifact_description.strip(),
            project_name_pattern.strip(),
            materials_pattern.strip(),
            storytelling_pattern.strip(),
            validation_criteria.strip(),
            rationale.strip(),
            confidence,
            _utc_now_iso(),
            proposal_id,
        ),
    )
    con.commit()


def accept_curriculum_artifact_template_proposal(con: CatalogConnection, proposal_id: int) -> dict[str, Any]:
    proposal = con.execute(
        """
        SELECT *
        FROM curriculum_artifact_template_proposal
        WHERE id = ?
        """,
        (proposal_id,),
    ).fetchone()
    if not proposal:
        return {"status": "missing", "proposal_id": proposal_id}
    scope_names = [str(item) for item in _json_list(proposal["scope_names_json"]) if str(item).strip()]
    template_id = upsert_curriculum_artifact_template(
        con,
        code=str(proposal["code"]),
        title=str(proposal["title"]),
        artifact_family=str(proposal["artifact_family"]),
        artifact_description=str(proposal["artifact_description"]),
        project_name_pattern=str(proposal["project_name_pattern"] or ""),
        materials_pattern=str(proposal["materials_pattern"] or ""),
        storytelling_pattern=str(proposal["storytelling_pattern"] or ""),
        validation_criteria=str(proposal["validation_criteria"] or ""),
        priority=80,
        status="active",
        source="proposal_accept",
        scopes=[
            {
                "scope_type": str(proposal["scope_type"] or "coverage_area"),
                "scope_name": scope_name,
                "weight": 1.0,
            }
            for scope_name in (scope_names or ["*"])
        ],
    )
    con.execute(
        """
        UPDATE curriculum_artifact_template_proposal
        SET status = 'accepted',
            accepted_template_id = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (template_id, _utc_now_iso(), proposal_id),
    )
    con.commit()
    return {"status": "accepted", "proposal_id": proposal_id, "template_id": template_id}


def reject_curriculum_artifact_template_proposal(con: CatalogConnection, proposal_id: int) -> None:
    con.execute(
        """
        UPDATE curriculum_artifact_template_proposal
        SET status = 'rejected',
            updated_at = ?
        WHERE id = ?
        """,
        (_utc_now_iso(), proposal_id),
    )
    con.commit()
