"""Проверка актуальности и совместимости зависимостей проекта.

Извлекает зависимости из манифестов, сверяет их с официальными реестрами (с
запасным модельным поиском) и ищет конфликты совместимости. Вынесено из
``checks.py``; импортирует только листовой ``checker_base`` и модуль
``dependencies`` (никогда ``checks``), что держит граф ацикличным. ``checks``
реэкспортирует ``DependencyFreshnessChecker``, поэтому ``default_checkers`` и
тесты не меняются. Общие примитивы (``_dependency_registry_metadata``,
``_dependency_quote``, ``_dependency_name_with_spec``) живут в ``checker_base``,
так как ими пользуются и другие проверяющие (rights, региональная доступность).
"""

from __future__ import annotations

import json
from typing import Any

from content_factory.audit.checker_base import (
    BaseChecker,
    CheckContext,
    _cached_model_json,
    _checked_at_from_record,
    _dependency_name_with_spec,
    _dependency_quote,
    _dependency_registry_metadata,
    _enum_or_default,
    _external_check_error,
    _finding,
    _first_result_item,
    _first_source_url,
    _hash_cache_key,
    _model_text,
    _optional_model_text,
    _parse_confidence,
    _severity_from_verdict,
    _source_summary,
    _sources_from_item,
    _support_status_from_verdict,
    _verdict_from_model_value,
)
from content_factory.audit.dependencies import (
    CompatibilityIssue,
    DependencyCandidate,
    DependencyMetadata,
    DependencyRegistryClient,
    dependency_identity,
    extract_dependency_candidates,
    find_compatibility_issues,
    is_pinned_outdated,
    is_unbounded_spec,
)
from content_factory.audit.domain import (
    ContentUnit,
    Criterion,
    Evidence,
    ExtractedEntity,
    Finding,
    Severity,
    Verdict,
)
from content_factory.audit.openrouter import OpenRouterError


def _finding_from_dependency_issue(unit: ContentUnit, checker_name: str, issue: CompatibilityIssue) -> Finding:
    """Преобразует конфликт зависимостей в строку отчёта."""

    detail = (
        f"{issue.dependency.name}{issue.dependency.spec} требует {issue.related_name}{issue.required_spec}; "
        f"в проекте указано: {_dependency_name_with_spec(issue.related_name, issue.declared_spec)}. {issue.reason}"
    )
    return _finding(
        unit,
        checker_name,
        Criterion.TECHNOLOGY_FRESHNESS,
        Severity.MAJOR,
        Verdict.WARNING,
        0.8,
        _dependency_quote(issue.dependency),
        issue.dependency.location,
        [Evidence(title="Совместимость зависимостей", detail=detail)],
        "Согласовать версии зависимостей или явно добавить недостающую peer-зависимость.",
        True,
        extra={
            "dependency": issue.dependency.name,
            "related_dependency": issue.related_name,
            "declared_spec": issue.declared_spec,
            "required_spec": issue.required_spec,
        },
        support_status="конфликт ограничений",
    )


def _finding_from_dependency_model_item(
    unit: ContentUnit,
    checker_name: str,
    candidate: DependencyCandidate,
    item: dict[str, Any],
    record: dict[str, Any],
    cache_hit: bool,
    prompt_version: str,
) -> Finding:
    """Преобразует запасную проверку зависимости через Perplexity в строку отчёта."""

    verdict = _verdict_from_model_value(item.get("verdict"), Verdict.UNKNOWN)
    severity = _enum_or_default(Severity, item.get("severity"), _severity_from_verdict(verdict))
    evidence_text = _model_text(item, ("evidence", "reason", "explanation"), "Запасная проверка зависимости без пояснения.")
    sources = _sources_from_item(item)
    return _finding(
        unit,
        checker_name,
        Criterion.TECHNOLOGY_FRESHNESS,
        severity,
        verdict,
        _parse_confidence(item.get("confidence")),
        _dependency_quote(candidate),
        candidate.location,
        [Evidence(title="Запасная проверка зависимости", detail=evidence_text, url=_first_source_url(sources))],
        str(item.get("recommendation") or "Проверить зависимость вручную."),
        verdict != Verdict.PASS,
        extra={"cache_hit": cache_hit, "ecosystem": candidate.ecosystem},
        source=_source_summary(sources),
        checked_at=_checked_at_from_record(record),
        support_status=str(item.get("support_status") or _support_status_from_verdict(verdict)),
        latest_version=_optional_model_text(item.get("latest_version")),
        recommended_version=_optional_model_text(item.get("recommended_version")),
        prompt_version=prompt_version,
    )


class DependencyFreshnessChecker(BaseChecker):
    """Проверяет зависимости проекта через официальные реестры и запасной поиск."""

    name = "dependency_freshness_checker"
    prompt_version = "dependency_freshness_checker:v1"
    max_candidates = 50
    SYSTEM_PROMPT = """Ты проверяешь актуальность зависимости проекта.
Официальный реестр не дал уверенного ответа, поэтому нужен запасной поиск по открытым источникам.
Верни только JSON: {"verdict":"pass|warning|fail|unknown","severity":"info|minor|major","confidence":0.0,
"support_status":"","latest_version":"","recommended_version":"","evidence":"","sources":[{"title":"","url":""}],"recommendation":""}.
Не придумывай версии и источники. Если источников недостаточно, ставь verdict='unknown'.
Все пояснения и рекомендации пиши на русском языке."""

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        del entities
        candidates = extract_dependency_candidates(unit)[: self.max_candidates]
        if not candidates:
            return []
        registry_candidates = [candidate for candidate in candidates if candidate.group not in {"engine", "runtime"}]
        if not registry_candidates:
            return []
        if not context.settings.allow_network and context.fact_model_client is None:
            return [self._network_required_finding(unit, registry_candidates)]

        findings: list[Finding] = []
        metadata_by_key: dict[tuple[str, str], DependencyMetadata] = {}
        registry_client = DependencyRegistryClient(context.settings.link_timeout_seconds)
        for candidate in registry_candidates:
            metadata = self._registry_metadata(candidate, registry_client, context)
            if metadata is None:
                fallback = self._fallback_model_finding(unit, candidate, context)
                if fallback is not None:
                    findings.append(fallback)
                continue
            metadata_by_key[dependency_identity(candidate)] = metadata
            dependency_finding = self._finding_from_dependency(unit, candidate, metadata)
            if dependency_finding is not None:
                findings.append(dependency_finding)

        findings.extend(self._compatibility_findings(unit, candidates, metadata_by_key))
        return findings

    def _registry_metadata(
        self,
        candidate: DependencyCandidate,
        registry_client: DependencyRegistryClient,
        context: CheckContext,
    ) -> DependencyMetadata | None:
        """Получает метаданные официального реестра с кэшированием."""

        return _dependency_registry_metadata(candidate, registry_client, context)

    def _finding_from_dependency(
        self,
        unit: ContentUnit,
        candidate: DependencyCandidate,
        metadata: DependencyMetadata,
    ) -> Finding | None:
        """Создаёт находку по актуальности одной зависимости."""

        if candidate.ecosystem == "docker" and candidate.spec == "latest":
            return _finding(
                unit,
                self.name,
                Criterion.TECHNOLOGY_FRESHNESS,
                Severity.MINOR,
                Verdict.WARNING,
                0.8,
                _dependency_quote(candidate),
                candidate.location,
                [Evidence(title="Docker", detail="Образ использует тег latest.", url=metadata.source_url)],
                "Закрепить конкретный тег образа, чтобы окружение проекта было воспроизводимым.",
                True,
                source=metadata.source_url,
                checked_at=metadata.checked_at,
                support_status="не закреплено",
            )
        if is_unbounded_spec(candidate.spec) and candidate.name.lower() not in {"python", "node"}:
            return _finding(
                unit,
                self.name,
                Criterion.TECHNOLOGY_FRESHNESS,
                Severity.INFO,
                Verdict.UNKNOWN,
                0.7,
                _dependency_quote(candidate),
                candidate.location,
                [Evidence(title="Официальный реестр", detail="Версия зависимости не ограничена.", url=metadata.source_url)],
                "Закрепить допустимый диапазон версий или подтвердить, что плавающая версия допустима.",
                True,
                source=metadata.source_url,
                checked_at=metadata.checked_at,
                support_status="не закреплено",
                latest_version=metadata.latest_version,
                recommended_version=metadata.latest_version,
            )
        if is_pinned_outdated(candidate.spec, metadata.latest_version):
            return _finding(
                unit,
                self.name,
                Criterion.TECHNOLOGY_FRESHNESS,
                Severity.MINOR,
                Verdict.WARNING,
                0.85,
                _dependency_quote(candidate),
                candidate.location,
                [Evidence(title="Официальный реестр", detail="Закреплённая версия ниже последней.", url=metadata.source_url)],
                "Проверить совместимость и обновить зависимость до поддерживаемой версии.",
                True,
                source=metadata.source_url,
                checked_at=metadata.checked_at,
                support_status="есть новая версия",
                latest_version=metadata.latest_version,
                recommended_version=metadata.latest_version,
            )
        return _finding(
            unit,
            self.name,
            Criterion.TECHNOLOGY_FRESHNESS,
            Severity.INFO,
            Verdict.PASS,
            0.75,
            _dependency_quote(candidate),
            candidate.location,
            [Evidence(title="Официальный реестр", detail="Зависимость проверена, явных проблем не найдено.", url=metadata.source_url)],
            "Действий не требуется; при обновлении проекта повторить проверку совместимости.",
            False,
            source=metadata.source_url,
            checked_at=metadata.checked_at,
            support_status="проверено",
            latest_version=metadata.latest_version,
        )

    def _compatibility_findings(
        self,
        unit: ContentUnit,
        candidates: list[DependencyCandidate],
        metadata_by_key: dict[tuple[str, str], DependencyMetadata],
    ) -> list[Finding]:
        findings: list[Finding] = []
        for issue in find_compatibility_issues(candidates, metadata_by_key):
            findings.append(_finding_from_dependency_issue(unit, self.name, issue))
        return findings

    def _fallback_model_finding(
        self,
        unit: ContentUnit,
        candidate: DependencyCandidate,
        context: CheckContext,
    ) -> Finding | None:
        """Использует Perplexity как запасной источник, если официальный реестр не дал ответ."""

        if context.fact_model_client is None:
            return _finding(
                unit,
                self.name,
                Criterion.TECHNOLOGY_FRESHNESS,
                Severity.INFO,
                Verdict.UNKNOWN,
                0.45,
                _dependency_quote(candidate),
                candidate.location,
                [Evidence(title="Официальный реестр", detail="Не удалось проверить зависимость через официальный источник.")],
                "Повторить проверку позже или включить модельный контур для запасной проверки.",
                True,
                support_status="не проверялось",
            )

        prompt = json.dumps(
            {
                "ecosystem": candidate.ecosystem,
                "name": candidate.name,
                "declared_version": candidate.spec,
                "file_path": candidate.location.file_path,
                "line_start": candidate.location.line_start,
            },
            ensure_ascii=False,
            indent=2,
        )
        try:
            record, cache_hit = _cached_model_json(
                context,
                "dependency_fallback",
                _hash_cache_key("dependency_fallback", prompt),
                context.fact_model_client,
                self.SYSTEM_PROMPT,
                prompt,
                self.prompt_version,
            )
        except OpenRouterError as exc:
            return _external_check_error(unit, self.name, Criterion.TECHNOLOGY_FRESHNESS, exc)

        item = _first_result_item(record.get("response"))
        if item is None:
            return None
        finding = _finding_from_dependency_model_item(unit, self.name, candidate, item, record, cache_hit, self.prompt_version)
        return finding if finding.verdict != Verdict.PASS else None

    def _network_required_finding(self, unit: ContentUnit, candidates: list[DependencyCandidate]) -> Finding:
        """Фиксирует, что зависимости найдены, но внешняя сверка не выполнялась."""

        preview = ", ".join(f"{item.name}{item.spec}" for item in candidates[:12])
        return _finding(
            unit,
            self.name,
            Criterion.TECHNOLOGY_FRESHNESS,
            Severity.INFO,
            Verdict.UNKNOWN,
            0.55,
            None,
            None,
            [Evidence(title="Зависимости", detail=f"Найдено зависимостей: {len(candidates)}. Пример: {preview}")],
            "Включить сеть или модельный контур, чтобы сверить версии и совместимость зависимостей.",
            True,
            extra={"candidate_count": len(candidates)},
            support_status="не проверялось",
        )
