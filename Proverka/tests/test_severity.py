from content_audit.domain import Criterion, Evidence, Finding, Severity, Verdict
from content_audit.severity import SeverityCalibrator


def _finding(criterion: Criterion, severity: Severity, verdict: Verdict, evidence: str = "") -> Finding:
    return Finding(
        finding_id="fnd_test",
        unit_id="unit",
        branch=None,
        criterion=criterion,
        severity=severity,
        verdict=verdict,
        confidence=0.7,
        evidence=[Evidence(title="Проверка", detail=evidence)] if evidence else [],
        recommendation="Проверить.",
        needs_human_review=True,
        checker_name="model_rubric_checker",
    )


def test_workload_is_advisory_until_calibrated_with_platform_data() -> None:
    finding = _finding(Criterion.WORKLOAD, Severity.CRITICAL, Verdict.FAIL, "Слишком много заданий.")

    calibrated = SeverityCalibrator().calibrate([finding])[0]

    assert calibrated.severity == Severity.INFO
    assert calibrated.verdict == Verdict.UNKNOWN
    assert calibrated.extra["original_severity"] == "critical"


def test_market_fit_without_specific_evidence_is_advisory() -> None:
    finding = _finding(Criterion.MARKET_FIT, Severity.MAJOR, Verdict.WARNING, "Устарело.")

    calibrated = SeverityCalibrator().calibrate([finding])[0]

    assert calibrated.severity == Severity.INFO
    assert calibrated.verdict == Verdict.UNKNOWN
    assert calibrated.extra["severity_calibration"]


def test_market_fit_with_specific_evidence_is_capped_below_critical() -> None:
    finding = _finding(
        Criterion.MARKET_FIT,
        Severity.CRITICAL,
        Verdict.FAIL,
        "Материал строится вокруг устаревшей практики без объяснения исторического контекста; "
        "студенту предлагается применять подход, который противоречит современному промышленному процессу.",
    )

    calibrated = SeverityCalibrator().calibrate([finding])[0]

    assert calibrated.severity == Severity.MAJOR
    assert calibrated.verdict == Verdict.FAIL
