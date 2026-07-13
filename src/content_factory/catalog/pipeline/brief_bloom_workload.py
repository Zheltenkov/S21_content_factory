"""Bloom-level and workload normalization for brief -> skill-candidate synthesis.

Pure text/logic helpers: clamp a Bloom level to seniority/audience, infer a Bloom
floor from action verbs, and extract a normalized workload (hours/weeks) from free
text, plus ``_normalized_spec`` which assembles a normalized brief spec from these.
Extracted from ``stage_brief_to_catalog`` as a leaf (imports only the shared ``BLOOM``
table + stdlib); the stage module re-exports the consumed helpers, so callers
(including ``stage_brief_to_catalog.extract_workload_from_text`` used by the viewer)
are unchanged.
"""

from __future__ import annotations

import re
from typing import Any

from content_factory.catalog.pipeline import config, language
from content_factory.catalog.pipeline.models import BLOOM

_BLOOM_BY_SCORE = {score: bloom for bloom, score in BLOOM.items()}
_HIGH_BLOOM_SIGNAL = re.compile(
    r"\b("
    r"созда(е|ё|й|т|ть)|разработ|спроект|собир|постро|сгенерир|"
    r"оцен|валидир|обоснов|выбер|защит"
    r")",
    re.IGNORECASE,
)
_ROUTINE_ACTION_SIGNAL = re.compile(
    r"\b("
    r"сформулир|формулир|определ|провед|настро|выяв|провер|"
    r"использ|примен|опис|фиксир|состав"
    r")",
    re.IGNORECASE,
)
_APPLY_ACTION_SIGNAL = re.compile(
    r"\b("
    r"примен|использ|настро|настра|разверт|развёрт|организ|провед|подготов|"
    r"рассчит|счит|вед[её]т|интегрир|автоматиз"
    r")",
    re.IGNORECASE,
)
_ANALYZE_ACTION_SIGNAL = re.compile(
    r"\b("
    r"анализ|сравнив|интерпрет|приоритиз|сегментир|оцени|валидир|проектир"
    r")",
    re.IGNORECASE,
)
_CREATE_ACTION_SIGNAL = re.compile(
    r"\b("
    r"созда|собир|разработ|спроект|постро"
    r")",
    re.IGNORECASE,
)


def _seniority_bloom_ceiling(spec: dict[str, Any] | None) -> str | None:
    seniority = str((spec or {}).get("seniority") or (spec or {}).get("target_seniority") or "").strip().casefold()
    if not seniority:
        return None
    if seniority in config.UP_MAX_BLOOM_BY_SENIORITY:
        return config.UP_MAX_BLOOM_BY_SENIORITY[seniority]
    for key, ceiling in config.UP_MAX_BLOOM_BY_SENIORITY.items():
        if key and key in seniority:
            return ceiling
    return None


def _clamp_bloom_for_audience(level: str, spec: dict[str, Any] | None, text: str | None = None) -> str:
    ceiling = _seniority_bloom_ceiling(spec)
    if not ceiling or ceiling not in BLOOM or BLOOM[level] <= BLOOM[ceiling]:
        return level
    source_text = text or ""
    has_high_signal = bool(_HIGH_BLOOM_SIGNAL.search(source_text))
    has_routine_signal = bool(_ROUTINE_ACTION_SIGNAL.search(source_text))
    if not has_high_signal or has_routine_signal:
        return _BLOOM_BY_SCORE[BLOOM[ceiling]]
    return level


def _bloom_floor_for_text(text: str | None) -> str | None:
    source_text = text or ""
    if _CREATE_ACTION_SIGNAL.search(source_text):
        return "create"
    if _ANALYZE_ACTION_SIGNAL.search(source_text):
        return "analyze"
    if _APPLY_ACTION_SIGNAL.search(source_text):
        return "apply"
    return None


def normalize_bloom(value: str | None, spec: dict[str, Any] | None = None, text: str | None = None) -> str:
    mapping = {
        "remember": "remember",
        "recall": "remember",
        "knowledge": "remember",
        "understand": "understand",
        "comprehend": "understand",
        "apply": "apply",
        "application": "apply",
        "analyze": "analyze",
        "analyse": "analyze",
        "evaluate": "evaluate",
        "evaluation": "evaluate",
        "create": "create",
        "creation": "create",
        "знает": "remember",
        "понимает": "understand",
        "умеет": "apply",
        "анализирует": "analyze",
        "оценивает": "evaluate",
        "создает": "create",
        "создаёт": "create",
    }
    key = (value or "understand").strip().casefold()
    normalized = mapping.get(key, "understand")
    floor = _bloom_floor_for_text(text)
    if floor and BLOOM[normalized] < BLOOM[floor]:
        normalized = floor
    return _clamp_bloom_for_audience(normalized, spec, text)


def _number(value: str) -> float:
    return float(value.replace(",", "."))


def _extract_workload_from_text(text: str) -> dict[str, Any]:
    """Extract target workload from a free-form Russian/English brief."""
    source = text.casefold().replace("ё", "е")
    duration_months: tuple[float, float] | None = None
    duration_weeks: tuple[float, float] | None = None
    hours_per_week: float | None = None

    month_range = re.search(r"(\d+(?:[,.]\d+)?)\s*[-–—]\s*(\d+(?:[,.]\d+)?)\s*месяц", source)
    if month_range:
        duration_months = (_number(month_range.group(1)), _number(month_range.group(2)))
    else:
        month_single = re.search(r"(\d+(?:[,.]\d+)?)\s*месяц", source)
        if month_single:
            value = _number(month_single.group(1))
            duration_months = (value, value)

    week_range = re.search(r"(\d+(?:[,.]\d+)?)\s*[-–—]\s*(\d+(?:[,.]\d+)?)\s*недел", source)
    if week_range:
        duration_weeks = (_number(week_range.group(1)), _number(week_range.group(2)))
    else:
        week_single = re.search(r"(\d+(?:[,.]\d+)?)\s*недел", source)
        if week_single:
            value = _number(week_single.group(1))
            duration_weeks = (value, value)

    weekly = re.search(r"(\d+(?:[,.]\d+)?)\s*(?:академич(?:еских)?\s*)?(?:астрономич(?:еских)?\s*)?час(?:ов|а)?\s*(?:в|/)\s*недел", source)
    if weekly:
        hours_per_week = _number(weekly.group(1))

    weeks_min: float | None = None
    weeks_max: float | None = None
    if duration_weeks:
        weeks_min, weeks_max = duration_weeks
    elif duration_months:
        weeks_min = duration_months[0] * 4.345
        weeks_max = duration_months[1] * 4.345

    if hours_per_week is None or weeks_min is None or weeks_max is None:
        return {}

    total_min = round(weeks_min * hours_per_week)
    total_max = round(weeks_max * hours_per_week)
    return {
        "duration_months_min": round(duration_months[0], 2) if duration_months else None,
        "duration_months_max": round(duration_months[1], 2) if duration_months else None,
        "duration_weeks_min": round(weeks_min, 2),
        "duration_weeks_max": round(weeks_max, 2),
        "hours_per_week": round(hours_per_week, 2),
        "target_total_hours_min": int(total_min),
        "target_total_hours_max": int(total_max),
        "target_total_hours": int(round((total_min + total_max) / 2)),
    }


def extract_workload_from_text(text: str) -> dict[str, Any]:
    """Public wrapper used by persisted-plan rebuilds."""
    return _extract_workload_from_text(text)


def _normalized_spec(raw: dict[str, Any]) -> dict[str, Any]:
    role = str(raw.get("target_role") or raw.get("role") or "").strip()
    seniority = str(raw.get("target_seniority") or raw.get("seniority") or "").strip()
    domain = str(raw.get("domain") or "").strip()
    artifact_type = str(raw.get("artifact_type") or "learner_brief").strip() or "learner_brief"
    operator_role = str(raw.get("operator_role") or "").strip() or None
    program_goal = str(raw.get("program_goal") or "").strip()
    must_include_areas = [
        str(item).strip()
        for item in (raw.get("must_include_areas") or [])
        if str(item).strip()
    ]
    sub_queries = []
    seen_queries: set[str] = set()
    for item in (raw.get("sub_queries") or []):
        query = str(item).strip()
        norm = query.casefold()
        if not query or norm in seen_queries:
            continue
        seen_queries.add(norm)
        sub_queries.append(query)
    return {
        "artifact_type": artifact_type,
        "role": role,
        "seniority": seniority,
        "domain": domain,
        "operator_role": operator_role,
        "program_goal": program_goal,
        "must_include_areas": [language.localize_area_label(area) for area in must_include_areas],
        "sub_queries": sub_queries,
    }
