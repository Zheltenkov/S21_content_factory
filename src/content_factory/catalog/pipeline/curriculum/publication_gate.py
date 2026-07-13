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

from .methodology_profile import MethodologyProfile


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


def _num(metrics: dict, key: str, default: float = 0.0) -> float:
    try:
        return float(metrics.get(key, default) or 0)
    except (TypeError, ValueError):
        return default


def _non_exempt_single_skill_pct(metrics: dict, profile: MethodologyProfile) -> float:
    """Single-skill share EXCLUDING the profile's exempt project kinds (labs etc.)."""
    project_count = _num(metrics, "project_count")
    if project_count <= 0:
        return _num(metrics, "single_skill_project_pct")  # legacy metric without by-type facts
    by_type = metrics.get("single_skill_count_by_type")
    if not isinstance(by_type, dict):
        return _num(metrics, "single_skill_project_pct")  # legacy plan: raw pct
    exempt = set(profile.single_skill_exempt_kinds)
    non_exempt = sum(int(count or 0) for kind, count in by_type.items() if kind not in exempt)
    return round(non_exempt / project_count * 100, 1)


def _candidate_failures(metrics: dict, profile: MethodologyProfile) -> list[GateFailure]:
    failures: list[GateFailure] = []
    thresholds = profile.publication_thresholds

    def add(code: str, message: str, value: float, threshold: float) -> None:
        failures.append(GateFailure(code=code, message=message, value=value, threshold=threshold))

    # Universal mechanical/judgment checks (profile-independent).
    if _num(metrics, "title_violation_count") > 0:
        add("title_violations", "Есть названия проектов, нарушающие ProjectTitlePolicy", _num(metrics, "title_violation_count"), 0)
    if _num(metrics, "generic_artifact_count") > 0:
        add("generic_artifacts", "Есть проекты с generic-артефактом вместо policy-контракта", _num(metrics, "generic_artifact_count"), 0)
    if _num(metrics, "testable_criteria_coverage_pct", 100) < 100:
        add("untestable_criteria", "Есть проекты с непроверяемыми критериями приёмки", _num(metrics, "testable_criteria_coverage_pct", 100), 100)
    if _num(metrics, "blocking_question_count") > 0:
        add("blocking_questions", "Есть незакрытые блокирующие вопросы брифа", _num(metrics, "blocking_question_count"), 0)
    if _num(metrics, "uncovered_required_area_count") > 0:
        add("required_areas_uncovered", "Не все обязательные области брифа покрыты принятыми навыками", _num(metrics, "uncovered_required_area_count"), 0)

    # Profile-interpreted checks (thresholds come from the resolved profile).
    coverage = _num(metrics, "policy_area_coverage_pct", 100)
    if coverage < thresholds.required_policy_coverage_pct:
        add("policy_area_incomplete", "Не все проекты классифицированы по policy-области", coverage, thresholds.required_policy_coverage_pct)
    single_skill_pct = _non_exempt_single_skill_pct(metrics, profile)
    if single_skill_pct > thresholds.single_skill_max_pct:
        add("single_skill_excess", "Доля однонавыковых проектов выше допустимой профилем", single_skill_pct, thresholds.single_skill_max_pct)

    # Capstone requirement is interpreted by the profile's capstone policy.
    capstone_required = (
        profile.capstone_policy == "always"
        or (profile.capstone_policy == "follow_design" and bool(metrics.get("capstone_required")))
    )
    if capstone_required and not bool(metrics.get("capstone_present")):
        add("capstone_missing", "Требуется capstone, но он отсутствует", 0, 1)
    return failures


def evaluate_publication_gate(
    metrics: dict,
    *,
    profile: MethodologyProfile | None,
    waivers: Iterable[PublicationWaiver] = (),
    up_version: str = "",
) -> GateResult:
    """Evaluate the publish gate through a RESOLVED profile (no hidden default).

    ``profile`` is None only when the plan names a profile version we do not know: the draft
    still opens, but publication is blocked with ``methodology_profile_unavailable`` — the
    plan is never silently re-scored by a different profile. Failures with a matching waiver
    are released, not blocking.
    """
    waiver_list = list(waivers)
    waiver_codes = {
        waiver.code for waiver in waiver_list if not waiver.up_version or not up_version or waiver.up_version == up_version
    }
    if profile is None:
        unavailable = GateFailure(
            code="methodology_profile_unavailable",
            message="Профиль методологии, которым построен УП, недоступен в этой версии кода",
            value=0,
            threshold=1,
        )
        return GateResult(passed=False, failures=(unavailable,), waived=(), waivers=tuple(waiver_list))
    blocking: list[GateFailure] = []
    waived: list[GateFailure] = []
    for failure in _candidate_failures(metrics, profile):
        (waived if failure.code in waiver_codes else blocking).append(failure)
    return GateResult(
        passed=not blocking,
        failures=tuple(blocking),
        waived=tuple(waived),
        waivers=tuple(waiver_list),
    )
