"""Экстракторы per-criterion исходов из отчётов рубрики и дидактики.

Чистые функции: разбирают сериализованные отчёты (`rubric_json`, `didactic_json`) в
плоский список pass/fail на критерий. Только объективные критерии (рубрика — `script`;
дидактика — по дименшенам). Истинный pass берётся ДО маскировки политикой
(`details.original_score`), иначе `score`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CriterionOutcome:
    """Исход одного критерия в одном прогоне."""

    id: str
    passed: bool
    kind: str  # "rubric" | "didactic"


def rubric_outcomes(rubric_json: Any) -> list[CriterionOutcome]:
    """Истинный pass/fail по объективным (`script`) критериям рубрики."""
    if not isinstance(rubric_json, dict):
        return []
    items = rubric_json.get("items")
    if not isinstance(items, list):
        return []
    outcomes: list[CriterionOutcome] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("check_method")) != "script":
            continue  # калибруем только объективные критерии
        raw_details = item.get("details")
        details = raw_details if isinstance(raw_details, dict) else {}
        raw = details.get("original_score", item.get("score"))
        outcomes.append(
            CriterionOutcome(id=str(item.get("id")), passed=raw == 1, kind="rubric")
        )
    return outcomes


def didactic_outcomes(didactic_json: Any) -> list[CriterionOutcome]:
    """Pass/fail по дидактическим дименшенам (fail = дименшен ниже пола)."""
    if not isinstance(didactic_json, dict):
        return []
    dimensions = didactic_json.get("dimensions")
    if not isinstance(dimensions, list):
        return []
    abstain = didactic_json.get("abstain_reasons")
    below = {
        reason.split(":", 1)[1]
        for reason in (abstain if isinstance(abstain, list) else [])
        if isinstance(reason, str) and reason.startswith("below_floor:")
    }
    outcomes: list[CriterionOutcome] = []
    for dim in dimensions:
        if not isinstance(dim, dict):
            continue
        name = str(dim.get("dimension"))
        outcomes.append(
            CriterionOutcome(id=f"didactic:{name}", passed=name not in below, kind="didactic")
        )
    return outcomes
