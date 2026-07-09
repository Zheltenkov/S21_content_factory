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
from enum import Enum
from typing import Any, TypeVar

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
from content_factory.audit.text_utils import normalize_for_match

_EnumT = TypeVar("_EnumT", bound=Enum)

# Technology tokens shared by the fact-claim extractor and the tech/version
# checkers — lives on the leaf so both can import it without a cycle.
TECH_KEYWORDS = {
    "alpine",
    "bash",
    "busybox",
    "c11",
    "docker",
    "gcc",
    "github",
    "gitlab",
    "gnu",
    "java",
    "node",
    "node.js",
    "pcre2",
    "posix",
    "python",
    "ubuntu",
}

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


def _hash_cache_key(namespace: str, value: str) -> str:
    """Создаём стабильный ключ кэша без хранения длинных утверждений в имени."""

    normalized = normalize_for_match(value)
    digest = hashlib.sha1(f"{namespace}|{normalized}".encode()).hexdigest()
    return digest


def _model_context_priority(kind: str, relative_path: str) -> tuple[int, str]:
    """Сначала даём модели README, затем чек-лист, затем дополнительные материалы."""

    order = {"readme": 0, "checklist": 1, "material": 2}
    return order.get(kind, 9), relative_path.lower()


def _enum_or_default(enum_class: type[_EnumT], value: object, default: _EnumT) -> _EnumT:
    """Безопасно разбираем строковое значение перечисления."""

    if value is None:
        return default
    try:
        return enum_class(str(value).strip().lower())
    except Exception:  # noqa: BLE001 - модель может вернуть произвольную строку.
        return default


def _parse_confidence(value: object) -> float:
    """Приводит уверенность модели к числу от 0 до 1."""

    if isinstance(value, int | float):
        return max(0.0, min(1.0, float(value)))
    if value is None:
        return 0.5
    normalized = str(value).strip().lower()
    aliases = {
        "low": 0.35,
        "низкая": 0.35,
        "medium": 0.6,
        "средняя": 0.6,
        "high": 0.85,
        "высокая": 0.85,
    }
    if normalized in aliases:
        return aliases[normalized]
    try:
        return max(0.0, min(1.0, float(normalized)))
    except ValueError:
        return 0.5


def _parse_optional_int(value: object) -> int | None:
    """Безопасно разбирает номер строки из ответа модели."""

    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None
