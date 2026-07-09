"""Rights and originality audit checks.

Вынесено из ``checks.py``; импортирует только листовой ``checker_base``,
доменные типы и специализированные rights/dependency/image modules. ``checks``
реэкспортирует классы, поэтому ``default_checkers`` и тесты не меняются.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

from content_factory.audit.checker_base import (
    BaseChecker,
    CheckContext,
    _cached_model_json,
    _dependency_registry_metadata,
    _finding,
    _first_result_item,
    _first_source_url,
    _hash_cache_key,
    _model_text,
    _parse_confidence,
    _source_summary,
    _sources_from_item,
)
from content_factory.audit.dependencies import DependencyRegistryClient, extract_dependency_candidates
from content_factory.audit.domain import (
    ContentUnit,
    Criterion,
    Evidence,
    ExtractedEntity,
    Finding,
    TextLocation,
)
from content_factory.audit.image_rights import image_evidence_queries, image_rights_signals
from content_factory.audit.openrouter import OpenRouterError
from content_factory.audit.rights import (
    DATASET_RE,
    MANIFEST_NAMES,
    CodeMatch,
    RightsSignal,
    grade_rights_signal,
    license_policy,
    resolve_dependency_licenses,
    scan_project_licenses,
)


class RightsAndOriginalityChecker(BaseChecker):
    """Проверяет права на материалы и признаки заимствований."""

    name = "rights_originality_checker"
    prompt_version = "rights_originality_checker:v1"
    max_external_lookups = 6
    PROVENANCE_SYSTEM_PROMPT = """Ты собираешь доказательства о происхождении и правах на ресурс из учебного контента.
Верни только JSON: {"likely_source":"","license":"","confidence":0.0,"sources":[{"title":"","url":""}],"note":""}.
Не делай вывод о нарушении: укажи вероятный источник и лицензию, если нашёл.
Если источников нет, оставь sources пустым и confidence низким. Пиши пояснения на русском."""

    def __init__(self, code_similarity_index: dict[str, list[CodeMatch]] | None = None) -> None:
        self.code_similarity_index = code_similarity_index or {}

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        signals: list[RightsSignal] = []
        signals.extend(self._project_license_signals(unit))
        signals.extend(self._dependency_license_signals(unit, context))
        signals.extend(image_rights_signals(unit, entities))
        signals.extend(self._dataset_rights_signals(unit))
        signals.extend(self._code_similarity_signals(unit))
        signals.extend(self._external_evidence_signals(unit, entities, context))

        findings: list[Finding] = []
        for signal in signals:
            severity, verdict, needs_review = grade_rights_signal(signal)
            findings.append(
                _finding(
                    unit,
                    self.name,
                    Criterion.RIGHTS,
                    severity,
                    verdict,
                    signal.confidence,
                    signal.quote,
                    signal.location,
                    [Evidence(title=signal.title, detail=signal.detail, url=signal.url)],
                    signal.recommendation,
                    needs_review,
                    extra={
                        "kind": signal.kind,
                        "risk": signal.risk,
                        "deterministic": signal.deterministic,
                    },
                    source=signal.source,
                )
            )
        return findings

    def _project_license_signals(self, unit: ContentUnit) -> list[RightsSignal]:
        has_license_file = any(
            Path(file.relative_path).name.lower().startswith(("license", "notice"))
            for file in unit.files
        )
        readme_mentions_license = any(
            ("license" in file.text.lower() or "лицензи" in file.text.lower())
            for file in unit.files
            if file.kind == "readme"
        )
        scan = scan_project_licenses(unit.root_path)
        if has_license_file or readme_mentions_license or (scan is not None and scan.spdx):
            return []
        return [
            RightsSignal(
                kind="project_license",
                risk="no_license_only",
                deterministic=True,
                title="Лицензия проекта",
                detail="Не найден LICENSE/NOTICE и нет упоминания лицензии в README.",
                recommendation="Проверить, нужна ли лицензия для материалов и кода этой единицы.",
                confidence=0.6,
            )
        ]

    def _dependency_license_signals(self, unit: ContentUnit, context: CheckContext) -> list[RightsSignal]:
        manifests = [file for file in unit.files if Path(file.relative_path).name.lower() in MANIFEST_NAMES]
        signals: list[RightsSignal] = []
        seen: set[tuple[str, str, str]] = set()
        if context.settings.allow_network:
            registry_client = DependencyRegistryClient(context.settings.link_timeout_seconds)
            for candidate in extract_dependency_candidates(unit):
                if candidate.group in {"engine", "runtime"}:
                    continue
                metadata = _dependency_registry_metadata(candidate, registry_client, context)
                if metadata is None or not metadata.license_spdx:
                    continue
                signal = _dependency_license_signal(candidate.name, metadata.license_spdx, metadata.source_url, candidate.location)
                if signal is None:
                    continue
                key = (candidate.ecosystem, candidate.name.lower(), metadata.license_spdx)
                if key in seen:
                    continue
                seen.add(key)
                signals.append(signal)

        for package, spdx in resolve_dependency_licenses(manifests):
            signal = _dependency_license_signal(package, spdx, None, None)
            if signal is not None:
                key = ("local", package.lower(), spdx or "")
                if key not in seen:
                    seen.add(key)
                    signals.append(signal)
        return signals

    def _dataset_rights_signals(self, unit: ContentUnit) -> list[RightsSignal]:
        signals: list[RightsSignal] = []
        for file in unit.files:
            if file.kind not in {"readme", "material", "text"}:
                continue
            for line_number, line in enumerate(file.text.splitlines(), start=1):
                if not DATASET_RE.search(line):
                    continue
                if self._has_license_terms_near(file.text, line.strip()):
                    continue
                signals.append(
                    RightsSignal(
                        kind="dataset_rights",
                        risk="no_source",
                        deterministic=True,
                        title="Датасет без условий использования",
                        detail=f"Упоминание датасета без источника или лицензии: {line.strip()[:240]}",
                        recommendation="Добавить ссылку на датасет, его лицензию и условия использования.",
                        quote=line.strip()[:500],
                        location=TextLocation(file_path=file.relative_path, line_start=line_number, line_end=line_number),
                        confidence=0.7,
                    )
                )
        return signals[:5]

    def _code_similarity_signals(self, unit: ContentUnit) -> list[RightsSignal]:
        signals: list[RightsSignal] = []
        for match in self.code_similarity_index.get(unit.unit_id, []):
            if match.similarity < 0.8 or match.attributed:
                continue
            signals.append(
                RightsSignal(
                    kind="code_similarity",
                    risk="no_source",
                    deterministic=True,
                    title="Похожий код без атрибуции",
                    detail=f"Совпадение {match.similarity:.0%} с единицей {match.other_unit_id} без ссылки на источник.",
                    recommendation="Проверить заимствование между сдачами и добавить атрибуцию либо переработать код.",
                    source=match.other_unit_id,
                    confidence=min(1.0, max(0.0, match.similarity)),
                )
            )
        return signals

    def _external_evidence_signals(
        self,
        unit: ContentUnit,
        entities: list[ExtractedEntity],
        context: CheckContext,
    ) -> list[RightsSignal]:
        if not context.settings.allow_network or context.fact_model_client is None:
            return []

        signals: list[RightsSignal] = []
        for query in self._evidence_queries(unit, entities)[: self.max_external_lookups]:
            prompt = json.dumps(query, ensure_ascii=False, indent=2)
            try:
                record, _cache_hit = _cached_model_json(
                    context,
                    "rights",
                    _hash_cache_key("rights", prompt),
                    context.fact_model_client,
                    self.PROVENANCE_SYSTEM_PROMPT,
                    prompt,
                    self.prompt_version,
                )
            except OpenRouterError:
                continue
            item = _first_result_item(record.get("response")) or {}
            sources = _sources_from_item(item)
            if not sources:
                continue
            note = _model_text(item, ("note", "likely_source", "license"), "Поиск нашёл возможный источник ресурса.")
            signals.append(
                RightsSignal(
                    kind=str(query["kind"]),
                    risk="no_source",
                    deterministic=False,
                    title=str(query["title"]),
                    detail=note,
                    recommendation="Передать методологу: подтвердить источник и права по найденным ссылкам.",
                    quote=cast("str | None", query.get("quote")),
                    location=cast("TextLocation | None", query.get("location")),
                    source=_source_summary(sources),
                    url=_first_source_url(sources),
                    confidence=_parse_confidence(item.get("confidence")),
                )
            )
        return signals

    def _evidence_queries(self, unit: ContentUnit, entities: list[ExtractedEntity]) -> list[dict[str, object]]:
        queries: list[dict[str, object]] = []
        for file in unit.files:
            if file.kind not in {"readme", "material", "text"}:
                continue
            for line_number, line in enumerate(file.text.splitlines(), start=1):
                if DATASET_RE.search(line) and not self._has_license_terms_near(file.text, line.strip()):
                    queries.append(
                        {
                            "kind": "dataset_rights",
                            "title": "Возможный источник датасета",
                            "text": f"Найди источник, лицензию и условия использования датасета из фрагмента: {line.strip()}",
                            "quote": line.strip()[:500],
                            "location": TextLocation(file_path=file.relative_path, line_start=line_number, line_end=line_number),
                        }
                    )
        queries.extend(image_evidence_queries(entities))
        return queries

    def _has_license_terms_near(self, text: str, needle: str) -> bool:
        position = text.lower().find(needle.lower())
        if position < 0:
            return False
        fragment = text[max(0, position - 300) : position + len(needle) + 300]
        return bool(re.search(r"license|licence|terms|rights|лицензи|услови|права|cc-by|mit|apache", fragment, flags=re.IGNORECASE))


RightsChecker = RightsAndOriginalityChecker


def _dependency_license_signal(
    package: str,
    spdx: str | None,
    source_url: str | None,
    location: TextLocation | None,
) -> RightsSignal | None:
    """Преобразует лицензию пакета в сигнал по правам."""

    policy = license_policy(spdx)
    if policy == "deny":
        return RightsSignal(
            kind="dependency_license",
            risk="violation",
            deterministic=True,
            title="Несовместимая лицензия зависимости",
            detail=f"Зависимость {package} указана с лицензией {spdx}, которая требует отдельного согласования.",
            recommendation=f"Заменить {package} на пермиссивный аналог или согласовать использование.",
            source=spdx,
            url=source_url,
            location=location,
            confidence=0.9,
        )
    if policy == "review" and spdx is not None:
        return RightsSignal(
            kind="dependency_license",
            risk="unverifiable",
            deterministic=True,
            title="Лицензия зависимости требует разбора",
            detail=f"{package}: {spdx}. Условия лицензии нужно проверить вручную.",
            recommendation=f"Проверить условия лицензии {package} и допустимость использования в учебном проекте.",
            source=spdx,
            url=source_url,
            location=location,
            confidence=0.55,
        )
    return None
