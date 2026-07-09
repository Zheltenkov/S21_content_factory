"""Model-assisted audit checks.

Вынесено из ``checks.py``: здесь остаются проверки, которые используют
модельный контекст или парсят модельные findings. ``checks`` реэкспортирует
публичные классы и helper для совместимости старых импортов.
"""

from __future__ import annotations

import json
from typing import Any

from content_factory.audit.checker_base import (
    BaseChecker,
    CheckContext,
    _cached_model_json,
    _checked_at_from_record,
    _enum_or_default,
    _finding,
    _first_result_item,
    _hash_cache_key,
    _model_context_priority,
    _optional_model_text,
    _parse_confidence,
    _parse_optional_int,
    _source_summary,
    _sources_from_item,
    _verdict_from_model_value,
)
from content_factory.audit.domain import (
    ContentUnit,
    Criterion,
    Evidence,
    ExtractedEntity,
    Finding,
    Severity,
    TextLocation,
    Verdict,
)
from content_factory.audit.market_fit_signals import (
    _first_market_location,
    _market_fit_evidence,
    _market_fit_recommendation,
    _market_fit_signal_count,
    _market_fit_signals,
    _market_fit_verdict,
    _merge_market_signals,
)
from content_factory.audit.openrouter import OpenRouterError

MODEL_RUBRIC_ALLOWED_CRITERIA = {Criterion.WORKLOAD}


class MarketFitChecker(BaseChecker):
    """Проверяет наличие прикладного бизнес-контекста в учебном проекте."""

    name = "market_fit_checker"
    prompt_version = "market_fit_checker:v1"
    signal_labels = {
        "real_data": "Работа с реальными данными",
        "business_context": "Бизнес-контекст",
        "success_metrics": "Бизнес-метрики или требования",
    }
    signal_patterns: dict[str, tuple[str, ...]] = {
        "real_data": (
            r"\b(dataset|datasets|real data|production data|historical data|customer data|sales data|transaction data|"
            r"kaggle|open data|huggingface datasets|uci repository|data source)\b",
            r"(датасет\w*|выборк\w*|реальн\w*\s+данн\w*|историческ\w*\s+данн\w*|открыт\w*\s+данн\w*|"
            r"обезличенн\w*\s+данн\w*|данн\w*\s+(?:клиент\w*|пользовател\w*|продаж\w*|транзакц\w*|заказ\w*|заявк\w*)|"
            r"набор\s+данн\w*)",
        ),
        "business_context": (
            r"\b(business problem|business case|customer problem|stakeholder|user persona|target audience|use case|client need|"
            r"business process|market segment|customer base|online booking|manual labour|manual labor|employee labour costs|"
            r"employee labor costs|barbershop|barbershops|booking system)\b",
            r"(бизнес[-\s]?задач\w*|бизнес[-\s]?контекст\w*|проблем\w*\s+бизнес\w*|заказчик\w*|"
            r"целев\w*\s+аудитори\w*|пользовательск\w*\s+сценари\w*|потребност\w*\s+(?:клиент\w*|пользовател\w*)|"
            r"бизнес[-\s]?процесс\w*|сегмент\w*\s+рынк\w*|клиентск\w*\s+баз\w*|онлайн[-\s]?запис\w*|"
            r"ручн\w*\s+труд\w*|трудозатрат\w*|барбершоп\w*)",
        ),
        "success_metrics": (
            r"\b(kpi|conversion|revenue|retention|churn|nps|ltv|cac|arpu|roi|gmv|mau|dau|sla|"
            r"business metric|business requirement|quality target|service level|time to resolution)\b",
            r"(бизнес[-\s]?метрик\w*|метрик\w*\s+успех\w*|kpi|конверси\w*|выручк\w*|удержан\w*|отток\w*|"
            r"средн\w*\s+чек\w*|стоимост\w*\s+(?:привлечени\w*|обработк\w*)|врем\w*\s+обработк\w*|\bsla\b|"
            r"бизнес[-\s]?требован\w*|требован\w*\s+бизнес\w*|целев\w*\s+показател\w*)",
        ),
    }
    SYSTEM_PROMPT = """Ты проверяешь соответствие учебного проекта прикладной рыночной задаче.
На входе есть результаты правил: наличие реальных данных, бизнес-контекста, бизнес-метрик или требований.
Проверь, не пропустили ли правила перефразированный бизнес-контекст.
Верни только JSON: {"verdict":"pass|warning|unknown","severity":"info|minor|major","confidence":0.0,
"evidence":"","recommendation":"","real_data":true,"business_context":true,"success_metrics":true}.
real_data=true ставь только при реальном, внешнем, публичном, историческом или production-like датасете; тестовые фикстуры, мок-данные и технические отчёты не считаются.
business_context=true ставь только если есть бизнес-проблема, целевая аудитория, заказчик, пользовательский сценарий или бизнес-процесс.
success_metrics=true ставь только если есть бизнес-метрики, бизнес-требования, целевые показатели или ограничения результата.
Не ставь severity='critical'. Если данных мало, ставь verdict='unknown'.
Все пояснения и рекомендации пиши на русском языке."""

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        del entities
        signals = _market_fit_signals(unit, self.signal_patterns)
        if _market_fit_signal_count(signals) == 0:
            return []
        finding = self._finding_from_signals(unit, signals, model_item=None, record=None, cache_hit=False)
        if context.model_client is None or finding.verdict == Verdict.PASS:
            return [finding]

        model_result = self._model_refinement(unit, signals, context)
        if model_result is None:
            return [finding]
        item, record, cache_hit = model_result
        return [self._finding_from_signals(unit, signals, model_item=item, record=record, cache_hit=cache_hit)]

    def _model_refinement(
        self,
        unit: ContentUnit,
        signals: dict[str, dict[str, object]],
        context: CheckContext,
    ) -> tuple[dict[str, Any], dict[str, Any], bool] | None:
        """Уточняет слабые эвристические сигналы моделью."""

        if context.model_client is None:
            return None
        payload = {
            "unit": unit.name,
            "signals": signals,
            "context": _compact_unit_context(unit, limit=8000),
        }
        prompt = json.dumps(payload, ensure_ascii=False, indent=2)
        try:
            record, cache_hit = _cached_model_json(
                context,
                "market_fit",
                _hash_cache_key("market_fit", prompt),
                context.model_client,
                self.SYSTEM_PROMPT,
                prompt,
                self.prompt_version,
            )
        except OpenRouterError:
            return None
        item = _first_result_item(record.get("response"))
        return (item, record, cache_hit) if item is not None else None

    def _finding_from_signals(
        self,
        unit: ContentUnit,
        signals: dict[str, dict[str, object]],
        model_item: dict[str, Any] | None,
        record: dict[str, Any] | None,
        cache_hit: bool,
    ) -> Finding:
        """Собирает одну строку отчёта по трём под-оценкам."""

        merged = _merge_market_signals(signals, model_item)
        score = sum(1 for item in merged.values() if item["present"])
        verdict, severity = _market_fit_verdict(score)
        confidence = 0.65 + 0.1 * score
        if model_item is not None:
            verdict = _verdict_from_model_value(model_item.get("verdict"), verdict)
            severity = _enum_or_default(Severity, model_item.get("severity"), severity)
            if severity == Severity.CRITICAL:
                severity = Severity.MAJOR
            confidence = _parse_confidence(model_item.get("confidence"))

        evidence_text = _market_fit_evidence(merged, self.signal_labels)
        if model_item is not None:
            model_evidence = _optional_model_text(model_item.get("evidence"))
            if model_evidence:
                evidence_text = f"{evidence_text} Модель: {model_evidence}"
        recommendation = _market_fit_recommendation(merged, model_item)
        return _finding(
            unit,
            self.name,
            Criterion.MARKET_FIT,
            severity,
            verdict,
            confidence,
            None,
            _first_market_location(merged),
            [Evidence(title="Проверка соответствия рынку", detail=evidence_text)],
            recommendation,
            verdict != Verdict.PASS,
            extra={
                "market_fit_score": score,
                "sub_checks": merged,
                "model_refined": model_item is not None,
                "cache_hit": cache_hit,
            },
            checked_at=_checked_at_from_record(record) if record is not None else None,
            prompt_version=self.prompt_version if model_item is not None else None,
        )


class ModelRubricChecker(BaseChecker):
    """Модельная проверка критериев, которые трудно закрыть правилами."""

    name = "model_rubric_checker"
    prompt_version = "model_rubric_checker:v1"

    SYSTEM_PROMPT = """Ты проверяешь учебный контент как инженер-методолог.
Верни только JSON: {"findings": [ ... ]}.
Каждый элемент: criterion, severity, verdict, confidence, quote, file_path, line_start, evidence, recommendation.
Критерий только один: workload.
Все текстовые поля ответа пиши на русском языке.
Не используй английский язык в рекомендации, если только цитируешь исходный термин из материала.
Не придумывай источники. Если доказательств мало, ставь verdict='unknown' и needs_human_review=true.
Для workload не ставь severity='critical': это консультационный критерий до калибровки на данных.
Для workload ставь verdict='unknown', если нет данных о реальном времени прохождения или трудозатратах.
Не проверяй фактологию, рынок, чек-лист, ссылки, права, язык, изображения и актуальность технологий: эти зоны закрывают отдельные специализированные модули."""

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        del entities
        if context.model_client is None:
            return []
        compact_context = _compact_unit_context(unit)
        if not compact_context.strip():
            return []
        try:
            response = context.model_client.complete_json(self.SYSTEM_PROMPT, compact_context)
        except OpenRouterError as exc:
            return [
                _finding(
                    unit,
                    self.name,
                    Criterion.CORRECTNESS,
                    Severity.INFO,
                    Verdict.UNKNOWN,
                    0.3,
                    None,
                    None,
                    [Evidence(title="Модельная проверка", detail=str(exc))],
                    "Повторить модельную проверку после устранения ошибки провайдера.",
                    True,
                )
            ]
        context.record_model_result(context.model_client, cache_hit=False, prompt_version=self.prompt_version)

        findings: list[Finding] = []
        for item in response.get("findings", []):
            if not isinstance(item, dict):
                continue
            finding = _finding_from_model_item(unit, self.name, item, self.prompt_version)
            if finding.criterion not in MODEL_RUBRIC_ALLOWED_CRITERIA:
                continue
            if not _is_actionable_model_rubric_finding(finding):
                continue
            findings.append(finding)
        return findings


def _compact_unit_context(unit: ContentUnit, limit: int = 12000) -> str:
    """Собираем компактный контекст для модельной проверки."""

    chunks: list[str] = []
    ordered_files = sorted(unit.files, key=lambda file: _model_context_priority(file.kind, file.relative_path))
    for file in ordered_files:
        if file.kind not in {"readme", "checklist", "material"}:
            continue
        fragment = file.text[:3000]
        chunks.append(f"Файл: {file.relative_path}\n{fragment}")
        if sum(len(chunk) for chunk in chunks) >= limit:
            break
    return "\n\n---\n\n".join(chunks)[:limit]


def _finding_from_model_item(
    unit: ContentUnit,
    checker_name: str,
    item: dict[str, object],
    prompt_version: str | None = None,
) -> Finding:
    """Преобразуем ответ модели в строгий доменный объект."""

    criterion = _enum_or_default(Criterion, item.get("criterion"), Criterion.CORRECTNESS)
    severity = _enum_or_default(Severity, item.get("severity"), Severity.INFO)
    verdict = _enum_or_default(Verdict, item.get("verdict"), Verdict.UNKNOWN)
    file_path = str(item.get("file_path") or "") or None
    line_start = _parse_optional_int(item.get("line_start"))
    location = TextLocation(file_path=file_path or "", line_start=line_start, line_end=line_start) if file_path and line_start else None
    evidence_text = str(item.get("evidence") or "Модельная проверка без отдельного источника.")
    sources = _sources_from_item(item)
    return _finding(
        unit,
        checker_name,
        criterion,
        severity,
        verdict,
        _parse_confidence(item.get("confidence")),
        str(item.get("quote") or "") or None,
        location,
        [Evidence(title="Модельная проверка", detail=evidence_text)],
        str(item.get("recommendation") or "Проверить случай вручную."),
        True,
        source=_source_summary(sources),
        prompt_version=prompt_version,
    )


def _is_actionable_model_rubric_finding(finding: Finding) -> bool:
    """Отсекает общие advisory-ответы модели без конкретного проверяемого места."""

    if finding.verdict == Verdict.UNKNOWN:
        return False
    if finding.confidence < 0.7:
        return False
    if finding.location is None and not finding.quote:
        return False
    return True
