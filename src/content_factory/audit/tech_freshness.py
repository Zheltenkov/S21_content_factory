"""Проверка актуальности технологий, версий и стандартов.

Отбирает из извлечённых сущностей похожие на технологии кандидаты, проверяет их
актуальность через поисковую модель и формирует строки отчёта. Вынесено из
``checks.py``; импортирует только листовой ``checker_base`` (никогда ``checks``),
что держит граф зависимостей ацикличным. ``checks`` реэкспортирует
``TechFreshnessChecker``/``TechnologyFreshnessChecker``, поэтому ``default_checkers``
и тесты не меняются.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from content_factory.audit.checker_base import (
    TECH_KEYWORDS,
    BaseChecker,
    CheckContext,
    _cached_model_json,
    _checked_at_from_record,
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
from content_factory.audit.domain import (
    ContentUnit,
    Criterion,
    EntityType,
    Evidence,
    ExtractedEntity,
    Finding,
    Severity,
    Verdict,
)
from content_factory.audit.openrouter import OpenRouterError

NON_TECH_VERSION_LABEL_RE = re.compile(
    r"(?i)\b(?:chapter|exercise|section|part|task|step|lesson|module|unit|turn-in|files?\s+to\s+turn\s+in)\b"
)
LOW_CONFIDENCE_UNKNOWN_THRESHOLD = 0.3


def _select_technology_candidates(entities: list[ExtractedEntity], limit: int) -> list[ExtractedEntity]:
    """Выбираем ограниченный набор сущностей, которые реально похожи на технологии."""

    candidates = [entity for entity in entities if entity.entity_type in {EntityType.VERSION, EntityType.TECHNOLOGY, EntityType.DATE}]
    seen_values: set[str] = set()
    seen_roots: set[str] = set()
    selected: list[ExtractedEntity] = []
    for entity in candidates:
        key = _normalise_technology_value(entity.value)
        root = _technology_root(entity.value)
        if key in seen_values:
            continue
        if entity.entity_type == EntityType.TECHNOLOGY and root and root in seen_roots:
            continue
        seen_values.add(key)
        if not _looks_like_actuality_candidate(entity):
            continue
        if root:
            seen_roots.add(root)
        selected.append(entity)
        if len(selected) >= limit:
            break
    return selected


def _looks_like_actuality_candidate(entity: ExtractedEntity) -> bool:
    """Отсекаем слишком общие слова и оставляем проверяемые версии/даты/технологии."""

    value = entity.value.strip()
    lowered = value.lower()
    context = f"{value} {entity.context or ''}".lower()
    if len(lowered) < 2:
        return False
    if _is_non_technology_version_label(value):
        return False
    if re.fullmatch(r"(19|20)\d{2}", lowered):
        return any(keyword in context for keyword in TECH_KEYWORDS)
    if any(keyword in lowered for keyword in TECH_KEYWORDS):
        return True
    return entity.entity_type == EntityType.VERSION and _has_nearby_technology_context(context)


def _is_non_technology_version_label(value: str) -> bool:
    """Отбрасывает номера упражнений и служебные подписи, похожие на версии."""

    normalized = value.strip().lower()
    if NON_TECH_VERSION_LABEL_RE.search(normalized):
        return True
    if re.fullmatch(r"ex\d{1,3}", normalized):
        return True
    return False


def _has_nearby_technology_context(context: str) -> bool:
    """Проверяет, что версия стоит рядом с настоящей технологической сущностью."""

    if not any(keyword in context for keyword in TECH_KEYWORDS):
        return False
    return any(
        marker in context
        for marker in (
            "version",
            "верси",
            "interpreter",
            "интерпретатор",
            "runtime",
            "image",
            "образ",
            "standard",
            "стандарт",
            "release",
            "lts",
            "support",
            "поддерж",
            "python",
            "java",
            "alpine",
            "ubuntu",
            "gcc",
            "node",
            "posix",
            "c11",
        )
    )


def _normalise_technology_value(value: str) -> str:
    """Нормализуем значение для дедупликации и кэша."""

    return re.sub(r"\s+", " ", value.strip().lower())


def _technology_root(value: str) -> str | None:
    """Определяем базовое имя технологии для подавления дублей вида Java 21 и Java."""

    lowered = value.lower()
    for keyword in sorted(TECH_KEYWORDS, key=len, reverse=True):
        if keyword in lowered:
            return keyword
    return None


def _technology_check_prompt(entity: ExtractedEntity) -> str:
    """Формируем входной контракт проверки актуальности технологии."""

    payload = {
        "check_date": datetime.now(UTC).date().isoformat(),
        "candidate": entity.value,
        "entity_type": entity.entity_type.value,
        "quote": entity.quote,
        "context": entity.context,
        "file_path": entity.location.file_path,
        "line_start": entity.location.line_start,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _finding_from_technology_item(
    unit: ContentUnit,
    checker_name: str,
    entity: ExtractedEntity,
    item: dict[str, Any],
    record: dict[str, Any],
    cache_hit: bool,
    prompt_version: str,
) -> Finding:
    """Преобразуем результат проверки технологии в строку отчёта."""

    verdict = _verdict_from_model_value(item.get("verdict"), Verdict.UNKNOWN)
    severity = _enum_or_default(Severity, item.get("severity"), _severity_from_verdict(verdict))
    evidence_text = _model_text(item, ("evidence", "reason", "explanation"), "Проверка актуальности без отдельного пояснения.")
    sources = _sources_from_item(item)
    support_status = _model_text(item, ("support_status", "status"), _support_status_from_verdict(verdict))
    return _finding(
        unit,
        checker_name,
        Criterion.TECHNOLOGY_FRESHNESS,
        severity,
        verdict,
        _parse_confidence(item.get("confidence")),
        entity.quote,
        entity.location,
        [Evidence(title="Актуальность технологии", detail=evidence_text, url=_first_source_url(sources))],
        _model_text(item, ("recommendation",), "Проверить версию технологии вручную и обновить материал при необходимости."),
        verdict != Verdict.PASS,
        extra={"cache_hit": cache_hit, "model": record.get("model"), "candidate": entity.value},
        source=_source_summary(sources),
        checked_at=_checked_at_from_record(record),
        support_status=support_status,
        latest_version=_optional_model_text(item.get("latest_version")),
        recommended_version=_optional_model_text(item.get("recommended_version")),
        prompt_version=prompt_version,
    )


def _is_uninformative_technology_item(item: dict[str, Any]) -> bool:
    """Отбрасываем пустой unknown от модели, чтобы не плодить строки без основания."""

    verdict = _verdict_from_model_value(item.get("verdict"), Verdict.UNKNOWN)
    if verdict != Verdict.UNKNOWN:
        return False
    if _sources_from_item(item):
        return False
    if _optional_model_text(item.get("latest_version")) or _optional_model_text(item.get("recommended_version")):
        return False
    confidence = _parse_confidence(item.get("confidence"))
    support_status = (_optional_model_text(item.get("support_status")) or _optional_model_text(item.get("status")) or "").lower()
    if confidence < LOW_CONFIDENCE_UNKNOWN_THRESHOLD and support_status in {"", "неизвестно", "unknown", "не проверялось"}:
        return True
    informative_keys = (
        "evidence",
        "reason",
        "explanation",
        "recommendation",
        "support_status",
        "status",
        "latest_version",
        "recommended_version",
    )
    return not any(_optional_model_text(item.get(key)) for key in informative_keys)


class TechFreshnessChecker(BaseChecker):
    """Проверяет актуальность технологий и версий с источниками."""

    name = "tech_freshness_checker"
    prompt_version = "tech_freshness_checker:v1"
    max_candidates = 12
    SYSTEM_PROMPT = """Ты проверяешь актуальность технологии, версии или стандарта в учебном контенте.
Верни только JSON: {"verdict":"pass|warning|fail|unknown","severity":"info|minor|major|critical","confidence":0.0,"support_status":"","latest_version":"","recommended_version":"","evidence":"","sources":[{"title":"","url":""}],"recommendation":""}.
support_status пиши коротко на русском: поддерживается, устарело, не поддерживается, окончание поддержки, неизвестно.
latest_version заполняй только когда источник позволяет назвать последнюю стабильную версию.
recommended_version заполняй только когда можно дать практическую рекомендацию по обновлению.
verdict='pass' ставь, если текущая версия поддерживается и подходит для учебного контента.
verdict='warning' ставь, если версия устарела, но ещё допустима.
verdict='fail' ставь, если версия не поддерживается или вводит студентов в заблуждение.
verdict='unknown' ставь, если источников недостаточно.
Не придумывай источники; если ссылки нет, оставь sources пустым списком.
Все пояснения и рекомендации пиши на русском языке."""

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        selected = _select_technology_candidates(entities, self.max_candidates)
        if not selected:
            return []

        if context.tech_model_client is None:
            return [self._fallback_candidate_finding(unit, selected)]

        findings: list[Finding] = []
        for entity in selected:
            cache_key = _hash_cache_key("technology", _normalise_technology_value(entity.value))
            prompt = _technology_check_prompt(entity)
            try:
                record, cache_hit = _cached_model_json(
                    context,
                    "technology",
                    cache_key,
                    context.tech_model_client,
                    self.SYSTEM_PROMPT,
                    prompt,
                    self.prompt_version,
                )
            except OpenRouterError as exc:
                findings.append(_external_check_error(unit, self.name, Criterion.TECHNOLOGY_FRESHNESS, exc))
                break

            item = _first_result_item(record.get("response"))
            if item is None:
                continue
            if _is_uninformative_technology_item(item):
                continue
            findings.append(_finding_from_technology_item(unit, self.name, entity, item, record, cache_hit, self.prompt_version))
        return findings

    def _fallback_candidate_finding(self, unit: ContentUnit, selected: list[ExtractedEntity]) -> Finding:
        """Сохраняем прежний режим: без модели показываем кандидатов на ручную проверку."""

        preview = ", ".join(entity.value for entity in selected[:20])
        if len(selected) > 20:
            preview = f"{preview}, ..."
        return _finding(
            unit,
            self.name,
            Criterion.TECHNOLOGY_FRESHNESS,
            Severity.INFO,
            Verdict.UNKNOWN,
            0.55,
            None,
            None,
            [Evidence(title="Кандидаты на проверку", detail=f"Найдено {len(selected)} сущностей: {preview}")],
            "Включить модельный контур, чтобы получить источник, статус поддержки и рекомендуемую версию.",
            True,
            extra={"candidate_count": len(selected), "sample_values": [entity.value for entity in selected[:20]]},
            support_status="не проверялось",
        )


TechnologyFreshnessChecker = TechFreshnessChecker
