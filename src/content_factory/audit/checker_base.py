"""Shared foundation for audit checkers.

Holds the per-run ``CheckContext``, the ``BaseChecker`` interface, and the
``Finding`` factory plus severity helpers. Extracted from ``checks.py`` so
individual checker families can move into their own modules while importing this
leaf (never ``checks``), which keeps the dependency graph acyclic. ``checks``
re-imports these names, so existing
``from content_factory.audit.checks import CheckContext / BaseChecker / _finding``
consumers (orchestrator, extra_checkers, tests) are unaffected.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from content_factory.audit.cache import AuditCache
from content_factory.audit.domain import (
    AuditSettings,
    ContentUnit,
    Criterion,
    Evidence,
    ExtractedEntity,
    Finding,
    IssueKind,
    Severity,
    TextLocation,
    Verdict,
)
from content_factory.audit.openrouter import OpenRouterClient

SEVERITY_RANK: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.MINOR: 1,
    Severity.MAJOR: 2,
    Severity.CRITICAL: 3,
}


class CheckContext:
    """Контекст, общий для всех проверяющих модулей."""

    def __init__(
        self,
        settings: AuditSettings,
        model_client: OpenRouterClient | None = None,
        fact_model_client: OpenRouterClient | None = None,
        tech_model_client: OpenRouterClient | None = None,
        cache: AuditCache | None = None,
    ) -> None:
        self.settings = settings
        self.model_client = model_client
        self.fact_model_client = fact_model_client
        self.tech_model_client = tech_model_client
        self.cache = cache
        self.model_usage: dict[str, Any] = {
            "calls_total": 0,
            "cache_hits": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
            "by_model": {},
        }
        self.prompt_versions: dict[str, str] = {}

    def record_model_result(self, client: OpenRouterClient, cache_hit: bool, prompt_version: str) -> None:
        """Собираем учёт вызовов модели и используемых версий промптов."""

        self.prompt_versions[prompt_version.split(":", 1)[0]] = prompt_version
        if cache_hit:
            self.model_usage["cache_hits"] += 1
            return

        usage = getattr(client, "last_call_usage", {}) or {}
        self.model_usage["calls_total"] += 1
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            self.model_usage[key] += int(usage.get(key, 0) or 0)
        self.model_usage["cost_usd"] += float(usage.get("cost_usd", 0.0) or 0.0)

        by_model = self.model_usage["by_model"]
        model_stats = by_model.setdefault(
            client.model,
            {"calls_total": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0},
        )
        model_stats["calls_total"] += 1
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            model_stats[key] += int(usage.get(key, 0) or 0)
        model_stats["cost_usd"] += float(usage.get("cost_usd", 0.0) or 0.0)


class BaseChecker(ABC):
    """Базовый интерфейс проверяющего модуля."""

    name: str

    @abstractmethod
    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        """Возвращает найденные случаи по единице контента."""


def _worse_severity(left: Severity, right: Severity) -> Severity:
    """Возвращает более высокий уровень критичности."""

    return left if SEVERITY_RANK[left] >= SEVERITY_RANK[right] else right


def _max_severity(values: Iterable[Severity]) -> Severity:
    """Выбирает максимальную критичность из набора сигналов."""

    result = Severity.INFO
    for value in values:
        result = _worse_severity(result, value)
    return result


def _finding(
    unit: ContentUnit,
    checker_name: str,
    criterion: Criterion,
    severity: Severity,
    verdict: Verdict,
    confidence: float,
    quote: str | None,
    location: TextLocation | None,
    evidence: list[Evidence],
    recommendation: str,
    needs_human_review: bool,
    extra: dict[str, object] | None = None,
    source: str | None = None,
    checked_at: datetime | None = None,
    support_status: str | None = None,
    latest_version: str | None = None,
    recommended_version: str | None = None,
    prompt_version: str | None = None,
    issue_kind: IssueKind | None = None,
) -> Finding:
    """Создаём найденный случай со стабильным идентификатором."""

    normalized_extra = extra or {}
    resolved_issue_kind = issue_kind or _infer_issue_kind(checker_name, criterion, verdict, normalized_extra)
    raw = "|".join(
        [
            unit.unit_id,
            checker_name,
            criterion.value,
            resolved_issue_kind.value,
            severity.value,
            quote or "",
            location.file_path if location else "",
            str(location.line_start if location else ""),
        ]
    )
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return Finding(
        finding_id=f"fnd_{digest}",
        unit_id=unit.unit_id,
        branch=unit.branch,
        criterion=criterion,
        issue_kind=resolved_issue_kind,
        severity=severity,
        verdict=verdict,
        confidence=confidence,
        quote=quote,
        location=location,
        evidence=evidence,
        source=source,
        checked_at=checked_at,
        support_status=support_status,
        latest_version=latest_version,
        recommended_version=recommended_version,
        prompt_version=prompt_version,
        recommendation=recommendation,
        needs_human_review=needs_human_review,
        checker_name=checker_name,
        extra=normalized_extra,
    )


def _infer_issue_kind(
    checker_name: str,
    criterion: Criterion,
    verdict: Verdict,
    extra: dict[str, object],
) -> IssueKind:
    """Отделяет обязательные дефекты от методических пожеланий и вопросов к данным."""

    raw = extra.get("issue_kind")
    if raw:
        try:
            return IssueKind(str(raw))
        except ValueError:
            pass
    if verdict == Verdict.UNKNOWN:
        return IssueKind.QUESTION
    issue_type = str(extra.get("issue_type") or "")
    if criterion in {Criterion.WORKLOAD, Criterion.MARKET_FIT, Criterion.EXAM, Criterion.LANGUAGE}:
        return IssueKind.QUESTION
    if checker_name in {"curriculum_relevance_checker", "model_rubric_checker"}:
        if issue_type in {"missing_key_topic", "topic_review", "outdated_approach", "language_tooling_conflict"}:
            return IssueKind.IMPROVEMENT
    return IssueKind.DEFECT
