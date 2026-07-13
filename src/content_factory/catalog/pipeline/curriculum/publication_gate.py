"""Semantic publication gate (contract epic, slice 8).

Turns the report-only quality metrics (slices 1-7) into a publish/freeze verdict. A UP draft
is always buildable; this gate only decides whether the UP is ready to be published or used
for mass project generation. Each failed check can be released by an explicit methodical
waiver (author + reason + UP version), so the gate never becomes a hard dead end.

Pure leaf: depends only on stdlib.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PublicationWaiver:
    """An explicit methodical override for one gate check on a specific UP version."""

    code: str
    author: str
    reason: str
    up_version: str = ""

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "author": self.author, "reason": self.reason, "up_version": self.up_version}


@dataclass(frozen=True)
class GateFailure:
    code: str
    message: str
    value: float
    threshold: float

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, "value": self.value, "threshold": self.threshold}


@dataclass(frozen=True)
class GateResult:
    passed: bool
    failures: tuple[GateFailure, ...] = ()
    waived: tuple[GateFailure, ...] = ()
    waivers: tuple[PublicationWaiver, ...] = field(default=())

    def as_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "failures": [failure.as_dict() for failure in self.failures],
            "waived": [failure.as_dict() for failure in self.waived],
            "waivers": [waiver.as_dict() for waiver in self.waivers],
        }


#: Single-skill projects allowed as a share of all projects (labs are explicit exceptions).
SINGLE_SKILL_MAX_PCT = 25.0


def _num(metrics: dict, key: str, default: float = 0.0) -> float:
    try:
        return float(metrics.get(key, default) or 0)
    except (TypeError, ValueError):
        return default


def _candidate_failures(metrics: dict) -> list[GateFailure]:
    failures: list[GateFailure] = []

    def add(code: str, message: str, value: float, threshold: float) -> None:
        failures.append(GateFailure(code=code, message=message, value=value, threshold=threshold))

    if _num(metrics, "title_violation_count") > 0:
        add("title_violations", "Есть названия проектов, нарушающие ProjectTitlePolicy", _num(metrics, "title_violation_count"), 0)
    if _num(metrics, "generic_artifact_count") > 0:
        add("generic_artifacts", "Есть проекты с generic-артефактом вместо policy-контракта", _num(metrics, "generic_artifact_count"), 0)
    if _num(metrics, "policy_area_coverage_pct", 100) < 100:
        add("policy_area_incomplete", "Не все проекты классифицированы по policy-области", _num(metrics, "policy_area_coverage_pct", 100), 100)
    if _num(metrics, "testable_criteria_coverage_pct", 100) < 100:
        add("untestable_criteria", "Есть проекты с непроверяемыми критериями приёмки", _num(metrics, "testable_criteria_coverage_pct", 100), 100)
    if _num(metrics, "blocking_question_count") > 0:
        add("blocking_questions", "Есть незакрытые блокирующие вопросы брифа", _num(metrics, "blocking_question_count"), 0)
    if _num(metrics, "uncovered_required_area_count") > 0:
        add(
            "required_areas_uncovered",
            "Не все обязательные области брифа покрыты принятыми навыками",
            _num(metrics, "uncovered_required_area_count"),
            0,
        )
    if _num(metrics, "single_skill_project_pct") > SINGLE_SKILL_MAX_PCT:
        add("single_skill_excess", "Доля однонавыковых проектов выше допустимой", _num(metrics, "single_skill_project_pct"), SINGLE_SKILL_MAX_PCT)
    if bool(metrics.get("capstone_required")) and not bool(metrics.get("capstone_present")):
        add("capstone_missing", "Требуется capstone, но он отсутствует", 0, 1)
    return failures


def evaluate_publication_gate(
    metrics: dict,
    *,
    waivers: Iterable[PublicationWaiver] = (),
    up_version: str = "",
) -> GateResult:
    """Evaluate the publish gate; failures with a matching waiver are released, not blocking."""
    waiver_list = list(waivers)
    waiver_codes = {
        waiver.code for waiver in waiver_list if not waiver.up_version or not up_version or waiver.up_version == up_version
    }
    blocking: list[GateFailure] = []
    waived: list[GateFailure] = []
    for failure in _candidate_failures(metrics):
        (waived if failure.code in waiver_codes else blocking).append(failure)
    return GateResult(
        passed=not blocking,
        failures=tuple(blocking),
        waived=tuple(waived),
        waivers=tuple(waiver_list),
    )
