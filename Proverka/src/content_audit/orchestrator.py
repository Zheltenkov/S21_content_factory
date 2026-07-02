"""Оркестратор запуска аудита."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from content_audit.cache import AuditCache
from content_audit.checks import CheckContext, default_checkers
from content_audit.code_similarity import build_code_similarity_index
from content_audit.domain import AuditReport, AuditSettings, ExtractedEntity, Finding, ModelUsageSummary, RunStep, RunSummary, Verdict
from content_audit.extraction import extract_entities
from content_audit.ingestion import discover_content_units, load_unit_files
from content_audit.openrouter import OpenRouterClient
from content_audit.postprocess import postprocess_findings
from content_audit.severity import SeverityCalibrator


DEFAULT_FACT_MODEL = "perplexity/sonar"
DEFAULT_TECH_MODEL = "qwen/qwen3-coder"


class AuditRunner:
    """Управляет полным прогоном: загрузка, извлечение, проверки, сводка."""

    def __init__(self, settings: AuditSettings) -> None:
        self.settings = settings

    def run(self) -> AuditReport:
        """Выполняем аудит и возвращаем полный отчёт."""

        started_at = datetime.now(timezone.utc)
        warnings: list[str] = []
        steps: list[RunStep] = []

        step_started = datetime.now(timezone.utc)
        units = discover_content_units(self.settings.input_path)
        units = [load_unit_files(unit, self.settings.max_file_bytes) for unit in units]
        _finish_step(steps, "Загрузка файлов", step_started, f"Единиц: {len(units)}")

        step_started = datetime.now(timezone.utc)
        model_client, fact_model_client, tech_model_client = self._build_model_clients(warnings)
        cache = AuditCache.load(self.settings.cache_path or self.settings.output_path / "audit_cache.json")
        context = CheckContext(
            settings=self.settings,
            model_client=model_client,
            fact_model_client=fact_model_client,
            tech_model_client=tech_model_client,
            cache=cache,
        )
        model_used = any(client is not None for client in (model_client, fact_model_client, tech_model_client))
        code_similarity_index = build_code_similarity_index(units)
        checkers = default_checkers(
            use_model=self.settings.use_model and model_used,
            code_similarity_index=code_similarity_index,
            lean=getattr(self.settings, "lean_checkers", False),
        )
        similarity_pairs = sum(len(matches) for matches in code_similarity_index.values())
        _finish_step(steps, "Подготовка проверок", step_started, f"Модулей: {len(checkers)}, совпадений кода: {similarity_pairs}")

        all_entities: list[ExtractedEntity] = []
        all_findings: list[Finding] = []
        step_started = datetime.now(timezone.utc)
        for unit in units:
            # Сначала извлекаем сущности, затем маршрутизируем их по проверяющим модулям.
            entities = extract_entities(unit)
            all_entities.extend(entities)
            for checker in checkers:
                all_findings.extend(checker.check(unit, entities, context))
        _finish_step(steps, "Извлечение и проверки", step_started, f"Сущностей: {len(all_entities)}")

        step_started = datetime.now(timezone.utc)
        cache.save()
        calibrated_findings = SeverityCalibrator().calibrate(all_findings)
        postprocessed_findings, postprocess_warnings = postprocess_findings(calibrated_findings)
        warnings.extend(postprocess_warnings)
        findings = self._filter_findings(postprocessed_findings)
        _finish_step(steps, "Сборка отчёта", step_started, f"Случаев: {len(findings)}")
        summary = self._build_summary(started_at, units, findings, warnings, model_used, context, steps)
        return AuditReport(summary=summary, units=units, entities=all_entities, findings=findings)

    def _build_model_clients(
        self,
        warnings: list[str],
    ) -> tuple[OpenRouterClient | None, OpenRouterClient | None, OpenRouterClient | None]:
        """Создаём независимые клиенты для общего, фактологического и технического контуров."""

        if not self.settings.use_model:
            return None, None, None
        if not self.settings.openrouter_api_key:
            warnings.append("Модельный контур запрошен, но OPENROUTER_API_KEY не задан.")
            return None, None, None

        model_client = self._build_named_client(self.settings.openrouter_model, DEFAULT_TECH_MODEL)
        fact_model_client = self._build_named_client(self.settings.openrouter_fact_model, DEFAULT_FACT_MODEL)
        tech_model_client = self._build_named_client(self.settings.openrouter_tech_model, self.settings.openrouter_model or DEFAULT_TECH_MODEL)
        return model_client, fact_model_client, tech_model_client

    def _build_named_client(self, model_name: str | None, fallback_model: str) -> OpenRouterClient:
        """Подставляем безопасную модель по умолчанию, если настройка не задана."""

        base_url = getattr(self.settings, "openrouter_base_url", None)
        kwargs = {"base_url": base_url} if base_url else {}
        return OpenRouterClient(api_key=self.settings.openrouter_api_key or "", model=model_name or fallback_model, **kwargs)

    def _filter_findings(self, findings: list[Finding]) -> list[Finding]:
        """Убираем успешные и, при необходимости, неизвестные случаи."""

        result = [finding for finding in findings if finding.verdict != Verdict.PASS]
        if self.settings.include_unknown:
            return result
        return [finding for finding in result if finding.verdict != Verdict.UNKNOWN]

    def _build_summary(
        self,
        started_at: datetime,
        units: list,
        findings: list[Finding],
        warnings: list[str],
        model_used: bool,
        context: CheckContext,
        steps: list[RunStep],
    ) -> RunSummary:
        """Собираем краткую сводку по прогону."""

        by_severity = Counter(finding.severity.value for finding in findings)
        by_criterion = Counter(finding.criterion.value for finding in findings)
        by_branch = Counter(finding.branch or "без ветки" for finding in findings)
        by_unit = Counter(finding.unit_id for finding in findings)
        return RunSummary(
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            input_path=str(self.settings.input_path),
            units_total=len(units),
            files_total=sum(len(unit.files) for unit in units),
            findings_total=len(findings),
            affected_units_total=len(by_unit),
            by_severity=dict(by_severity),
            by_criterion=dict(by_criterion),
            by_branch=dict(by_branch),
            by_unit=dict(by_unit),
            model_usage=ModelUsageSummary.model_validate(context.model_usage),
            prompt_versions=context.prompt_versions,
            steps=steps,
            model_used=model_used,
            network_used=self.settings.allow_network or model_used,
            warnings=warnings,
        )


def _finish_step(steps: list[RunStep], name: str, started_at: datetime, detail: str | None = None) -> None:
    """Добавляем в сводку завершённый шаг конвейера."""

    finished_at = datetime.now(timezone.utc)
    duration_ms = int((finished_at - started_at).total_seconds() * 1000)
    steps.append(RunStep(name=name, status="ok", started_at=started_at, finished_at=finished_at, duration_ms=duration_ms, detail=detail))
