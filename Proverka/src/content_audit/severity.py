"""Калибровка критичности найденных случаев."""

from __future__ import annotations

from content_audit.domain import Criterion, Finding, Severity, Verdict


class SeverityCalibrator:
    """Отделяет факт обнаружения проблемы от продуктовой оценки важности."""

    subjective_criteria = {Criterion.MARKET_FIT, Criterion.WORKLOAD}

    def calibrate(self, findings: list[Finding]) -> list[Finding]:
        """Возвращает находки с критичностью, приведённой к текущей политике."""

        return [self._calibrate_one(finding) for finding in findings]

    def _calibrate_one(self, finding: Finding) -> Finding:
        """Применяет точечные правила к одной находке."""

        if finding.criterion == Criterion.WORKLOAD:
            return self._as_advisory(finding, "Трудоёмкость пока не калибрована данными о реальном времени прохождения.")

        if finding.criterion in self.subjective_criteria and not self._has_specific_evidence(finding):
            return self._as_advisory(finding, "Субъективный критерий без конкретного обоснования оставлен как вопрос к разбору.")

        if finding.criterion == Criterion.MARKET_FIT and finding.severity == Severity.CRITICAL:
            return self._replace_severity(
                finding,
                Severity.MAJOR,
                "Соответствие рынку не повышается до Critical без отдельного подтверждения.",
            )

        return finding

    def _as_advisory(self, finding: Finding, reason: str) -> Finding:
        """Переводит слабокалиброванную находку в консультационный режим."""

        verdict = Verdict.UNKNOWN if finding.verdict in {Verdict.FAIL, Verdict.WARNING} else finding.verdict
        return self._replace(finding, severity=Severity.INFO, verdict=verdict, reason=reason)

    def _replace_severity(self, finding: Finding, severity: Severity, reason: str) -> Finding:
        """Меняет только критичность и фиксирует причину в extra."""

        return self._replace(finding, severity=severity, verdict=finding.verdict, reason=reason)

    def _replace(self, finding: Finding, severity: Severity, verdict: Verdict, reason: str) -> Finding:
        """Создаёт копию находки с отметкой о калибровке."""

        if finding.severity == severity and finding.verdict == verdict:
            return finding
        extra = dict(finding.extra)
        extra.setdefault("original_severity", finding.severity.value)
        extra.setdefault("original_verdict", finding.verdict.value)
        extra["severity_calibration"] = reason
        return finding.model_copy(update={"severity": severity, "verdict": verdict, "extra": extra})

    def _has_specific_evidence(self, finding: Finding) -> bool:
        """Проверяет, есть ли у модельной находки содержательное основание."""

        evidence_text = " ".join(evidence.detail.strip() for evidence in finding.evidence if evidence.detail.strip())
        return bool(finding.source or len(evidence_text) >= 80)
