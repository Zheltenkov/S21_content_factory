"""Catalog-matching and coverage audit for brief -> skill-candidate synthesis.

Pure logic: normalize/compare tokens, decide whether a candidate safely matches a
catalog skill (``group_is_compatible`` / ``is_catalog_match_safe``), and build the
coverage audit over a brief spec. Extracted from ``stage_brief_to_catalog`` as a leaf
(imports only the ``SkillCandidate`` model + stdlib). The stage module re-exports the
constants and helpers its synthesis path (and the viewer's
``stage_brief_to_catalog.build_coverage_audit``) still use, so callers are unchanged.
"""

from __future__ import annotations

import re
from typing import Any

from content_factory.catalog.pipeline.models import SkillCandidate

_GENERIC_CATALOG_GROUPS = {
    "прочие навыки",
    "прочее",
    "other",
    "uncategorized",
    "misc",
    "miscellaneous",
}

_ACTION_TOKEN_STOPWORDS = {
    "проведение",
    "формулирование",
    "проектирование",
    "настройка",
    "разработка",
    "подготовка",
    "оценка",
    "анализ",
    "расчет",
    "расчёт",
    "организация",
    "внедрение",
    "развертывание",
    "развёртывание",
    "приоритизация",
    "создание",
    "работа",
    "использование",
    "применение",
}

_DOMAIN_HINTS: dict[str, tuple[str, ...]] = {
    "research": ("исслед", "интерв", "customer", "discovery", "клиент", "пользователь", "jtbd", "persona", "персон"),
    "product": ("продукт", "mvp", "гипотез", "ценност", "сегмент", "сценар", "roadmap", "беклог", "backlog"),
    "engineering": ("код", "code", "review", "go", "asp", "sql", "api", "репозитор", "git", "ci", "cd", "тест", "архитект", "deploy", "деплой", "docker"),
    "infrastructure": ("монитор", "observability", "backup", "бэкап", "runbook", "инцидент", "логирован", "алерт", "cloud", "облак"),
    "marketing": ("маркет", "позиционир", "landing", "лендинг", "воронк", "канал", "привлеч"),
    "monetization": ("монет", "тариф", "pricing", "unit", "economics", "экономик", "пробн", "доступ", "продаж"),
    "legal": ("прав", "юрид", "legal", "договор", "документ", "администр"),
    "finance": ("финанс", "budget", "бюджет", "налог", "платеж"),
    "support": ("support", "поддерж", "feedback", "обратн", "triage", "sla"),
    "strategy": ("стратег", "okr", "risk", "рис", "управлен", "governance"),
    "ai": ("ai", "llm", "human-in-the-loop", "hitl", "prompt", "промпт"),
}


_COVERAGE_STOPWORDS = {
    "и", "или", "для", "по", "с", "на", "в", "из", "к", "от", "как", "а", "не", "это",
    "the", "and", "or", "of", "to", "with", "in",
    "навыки", "умения", "компетенции", "область", "области", "контур", "базовый", "базовая",
    "минимальный", "ключевой", "ключевые", "цифрового", "цифровых", "продукта", "продуктов",
    "продукт", "program", "brief", "learner", "graduate", "outcomes",
}

_PROGRAM_ARTIFACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bкогорт"), "Описывает формирование когорты, а не skill выпускника."),
    (re.compile(r"\bкритери(и|я)\s+отбор"), "Описывает правила набора в программу, а не skill выпускника."),
    (re.compile(r"\bучастник(ов|а|и)?\b"), "Описывает управление участниками программы, а не skill выпускника."),
    (re.compile(r"\bпреподавател"), "Описывает ресурс программы, а не skill выпускника."),
    (re.compile(r"\bнаставник|\bментор"), "Описывает состав команды сопровождения программы, а не learner-skill."),
    (re.compile(r"\bюрист|\bмаркетолог"), "Описывает staffing/outsourcing решение программы, а не learner-skill."),
    (re.compile(r"\bфаундер"), "Описывает организационную модель команды, а не наблюдаемый skill."),
    (re.compile(r"\bразмер\s+команд|\bчисленност"), "Описывает оргдизайн команды/потока, а не канонический skill."),
]


def _reclassify_program_artifacts(cands: list[SkillCandidate], spec: dict[str, Any]) -> None:
    if str(spec.get("artifact_type") or "").strip() not in {"program_brief", "mixed"}:
        return
    for cand in cands:
        text = f"{cand.name} {cand.group}".casefold()
        for pattern, rationale in _PROGRAM_ARTIFACT_PATTERNS:
            if pattern.search(text):
                cand.entity_type = "curriculum_section"
                cand.atomicity = "non_skill"
                cand.decision = "needs_review"
                cand.reasons = ["non_skill:curriculum_section"]
                cand.atomize_rationale = rationale
                break


def _norm_tokens(value: str) -> set[str]:
    # Грубая нормализация нужна только для coverage-аудита и не влияет на канонические имена.
    tokens = {
        token
        for token in re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9\-/]{3,}", value.casefold().replace("ё", "е"))
        if token not in _COVERAGE_STOPWORDS
    }
    return tokens


def _candidate_text(candidate: SkillCandidate) -> str:
    indicator_text = " ".join(indicator.text for indicator in candidate.indicators)
    return " ".join(
        part for part in [candidate.name, candidate.group, candidate.coverage_area or "", indicator_text] if part
    )


def _lexical_overlap(left: str, right: str) -> float:
    left_tokens = _norm_tokens(left)
    right_tokens = _norm_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = left_tokens & right_tokens
    return len(intersection) / max(min(len(left_tokens), len(right_tokens)), 1)


def _semantic_domains(*values: str | None) -> set[str]:
    text = " ".join(value or "" for value in values).casefold().replace("ё", "е")
    domains: set[str] = set()
    for domain, hints in _DOMAIN_HINTS.items():
        if any(hint in text for hint in hints):
            domains.add(domain)
    return domains


def _is_generic_catalog_group(value: str | None) -> bool:
    normalized = " ".join((value or "").casefold().replace("ё", "е").split())
    return normalized in _GENERIC_CATALOG_GROUPS or "прочие" in normalized or "uncategorized" in normalized


def _meaningful_tokens(*values: str | None) -> set[str]:
    tokens = _norm_tokens(" ".join(value or "" for value in values))
    return {token for token in tokens if token not in _ACTION_TOKEN_STOPWORDS and len(token) >= 3}


def _has_exact_catalog_name(candidate: SkillCandidate) -> bool:
    canonical = (candidate.canonical_name or "").strip()
    if not canonical:
        return False
    variants = [candidate.name, candidate.source_name or ""]
    canonical_norm = " ".join(canonical.casefold().replace("ё", "е").split())
    return any(" ".join(value.casefold().replace("ё", "е").split()) == canonical_norm for value in variants if value)


def group_is_compatible(candidate: SkillCandidate) -> bool:
    """Check whether candidate context and canonical catalog context are semantically compatible."""
    candidate_domains = _semantic_domains(candidate.name, candidate.group, candidate.coverage_area)
    canonical_domains = _semantic_domains(candidate.canonical_name, candidate.canonical_group)
    if candidate_domains and canonical_domains and not (candidate_domains & canonical_domains):
        return False

    candidate_tokens = _meaningful_tokens(candidate.name, candidate.coverage_area, candidate.group)
    canonical_tokens = _meaningful_tokens(candidate.canonical_name, candidate.canonical_group)
    if candidate_tokens and canonical_tokens and candidate_tokens & canonical_tokens:
        return True

    # Exact names can be accepted even when the domain dictionary does not know the terminology yet.
    return _has_exact_catalog_name(candidate)


def is_catalog_match_safe(candidate: SkillCandidate, spec: dict[str, Any] | None = None) -> bool:
    """Guard against false positive catalog matches before auto-accepting a candidate."""
    if candidate.resolution not in {"matched", "alias", "fuzzy"}:
        return True
    if candidate.match_score is None:
        return False
    score = float(candidate.match_score)
    if score < 95.0:
        return False
    artifact_type = str((spec or {}).get("artifact_type") or "").strip()
    if artifact_type in {"program_brief", "mixed"} and _is_generic_catalog_group(candidate.canonical_group):
        return False
    if candidate.resolution == "fuzzy" and score < 98.0:
        return False
    return group_is_compatible(candidate)


def _build_coverage_audit(
    spec: dict[str, Any],
    cands: list[SkillCandidate],
    coverage_rows: list[dict[str, Any]] | None = None,
    compaction_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # Coverage нужен как продуктовый сигнал: видно, какие обязательные области закрыты, а какие выпали.
    areas = [str(item).strip() for item in spec.get("must_include_areas") or [] if str(item).strip()]
    if not areas:
        return {"covered_count": 0, "partial_count": 0, "uncovered_count": 0, "rows": []}

    skills = [cand for cand in cands if cand.entity_type == "skill" and cand.atomicity == "atomic"]
    coverage_index: dict[str, dict[str, Any]] = {}
    for row in coverage_rows or []:
        area = str(row.get("area") or "").strip()
        if not area:
            continue
        coverage_index[area] = {
            "status": str(row.get("status") or "").strip() or "uncovered",
            "rationale": str(row.get("rationale") or "").strip(),
            "candidate_names": [str(name).strip() for name in row.get("candidate_names") or [] if str(name).strip()],
        }
    dropped_by_area: dict[str, list[str]] = {}
    cap_reason_by_area: dict[str, str] = {}
    for event in compaction_events or []:
        area = str(event.get("coverage_area") or "").strip()
        reason = str(event.get("reason") or "").strip()
        dropped_names = [str(name).strip() for name in event.get("dropped_names") or [] if str(name).strip()]
        if not area or not dropped_names:
            continue
        dropped_by_area.setdefault(area, []).extend(dropped_names)
        if reason.startswith("program_brief_area_cap"):
            cap_reason_by_area[area] = reason

    audit_rows: list[dict[str, Any]] = []
    covered_count = 0
    partial_count = 0
    uncovered_count = 0
    for area in areas:
        explicit = [cand for cand in skills if (cand.coverage_area or "").strip() == area]
        if explicit:
            status = "covered"
            candidate_names = [cand.name for cand in explicit]
            rationale = "Покрыто кандидатами, явно привязанными к области."
        else:
            lexical = [cand for cand in skills if _lexical_overlap(area, _candidate_text(cand)) >= 0.34]
            if lexical:
                status = "partial"
                candidate_names = [cand.name for cand in lexical]
                rationale = "Найдено только частичное лексическое пересечение с кандидатами."
            else:
                status = "uncovered"
                candidate_names = []
                rationale = "Для области не найдено ни одного кандидата."

        if area in coverage_index:
            status = coverage_index[area]["status"] or status
            candidate_names = coverage_index[area]["candidate_names"] or candidate_names
            rationale = coverage_index[area]["rationale"] or rationale

        dropped_names = list(dict.fromkeys(dropped_by_area.get(area, [])))
        if dropped_names and area in cap_reason_by_area and status == "covered":
            status = "partial"
            rationale = (
                f"Область покрыта не полностью: часть кандидатов скрыта правилом {cap_reason_by_area[area]}. "
                "Нужно проверить, не являются ли они важными поднавыками."
            )

        if status == "covered":
            covered_count += 1
        elif status == "partial":
            partial_count += 1
        else:
            uncovered_count += 1

        audit_rows.append(
            {
                "area": area,
                "status": status,
                "candidate_names": candidate_names,
                "dropped_candidate_names": dropped_names,
                "rationale": rationale,
            }
        )

    return {
        "covered_count": covered_count,
        "partial_count": partial_count,
        "uncovered_count": uncovered_count,
        "rows": audit_rows,
    }


def build_coverage_audit(
    spec: dict[str, Any],
    cands: list[SkillCandidate],
    coverage_rows: list[dict[str, Any]] | None = None,
    normalize_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    compaction_events = []
    if isinstance(normalize_report, dict) and isinstance(normalize_report.get("compaction_events"), list):
        compaction_events = normalize_report["compaction_events"]
    return _build_coverage_audit(spec, cands, coverage_rows, compaction_events)
