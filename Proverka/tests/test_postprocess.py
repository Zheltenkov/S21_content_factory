from datetime import datetime, timezone

from content_audit.domain import Criterion, Evidence, Finding, Severity, TextLocation, Verdict
from content_audit.postprocess import postprocess_findings


def _finding(
    *,
    finding_id: str = "f1",
    checker_name: str = "link_checker",
    criterion: Criterion = Criterion.ACTUALITY,
    severity: Severity = Severity.INFO,
    verdict: Verdict = Verdict.WARNING,
    confidence: float = 0.9,
    file_path: str = "README.md",
    evidence: list[Evidence] | None = None,
    needs_human_review: bool = True,
    prompt_version: str | None = None,
):
    return Finding(
        finding_id=finding_id,
        unit_id="unit",
        branch=None,
        criterion=criterion,
        severity=severity,
        verdict=verdict,
        confidence=confidence,
        quote="quote",
        location=TextLocation(file_path=file_path, line_start=1, line_end=1),
        evidence=evidence or [Evidence(title="Основание", detail="Подробное основание найденного случая.")],
        checked_at=datetime.now(timezone.utc),
        prompt_version=prompt_version,
        recommendation="Исправить найденный случай.",
        needs_human_review=needs_human_review,
        checker_name=checker_name,
    )


def test_postprocess_moves_external_tool_errors_to_warnings() -> None:
    noisy = _finding(
        checker_name="fact_checker_perplexity",
        verdict=Verdict.UNKNOWN,
        confidence=0.3,
        evidence=[Evidence(title="Внешняя проверка", detail="Не удалось получить JSON от OpenRouter: HTTP 400")],
    )
    useful = _finding(finding_id="useful")

    findings, warnings = postprocess_findings([noisy, useful])

    assert findings == [useful.model_copy(update={"needs_human_review": False, "extra": {"original_needs_human_review": True, "postprocess_review": "Флаг ручного разбора пересчитан по вердикту, уверенности и типу модуля."}})]
    assert "служебных ошибок" in warnings[0]


def test_postprocess_drops_empty_model_unknown_and_rights_rubric_duplicate() -> None:
    empty = _finding(
        checker_name="tech_freshness_checker",
        verdict=Verdict.UNKNOWN,
        confidence=0.0,
        evidence=[Evidence(title="Актуальность технологии", detail="Проверка актуальности без отдельного пояснения.")],
        prompt_version="tech_freshness_checker:v1",
    )
    rights = _finding(
        checker_name="model_rubric_checker",
        criterion=Criterion.RIGHTS,
        prompt_version="model_rubric_checker:v1",
    )

    findings, warnings = postprocess_findings([empty, rights])

    assert findings == []
    assert any("пустых модельных результатов" in warning for warning in warnings)
    assert any("дублей по правам" in warning for warning in warnings)


def test_postprocess_drops_low_evidence_actuality_unknown() -> None:
    finding = _finding(
        checker_name="tech_freshness_checker",
        criterion=Criterion.TECHNOLOGY_FRESHNESS,
        verdict=Verdict.UNKNOWN,
        confidence=0.1,
        evidence=[Evidence(title="Актуальность технологии", detail="Недостаточно контекста для определения актуальности.")],
        prompt_version="tech_freshness_checker:v1",
    ).model_copy(update={"support_status": "неизвестно"})

    findings, warnings = postprocess_findings([finding])

    assert findings == []
    assert any("низкоуверенных проверок актуальности" in warning for warning in warnings)


def test_postprocess_aligns_fail_info_and_recalculates_review_flag() -> None:
    finding = _finding(
        checker_name="model_rubric_checker",
        severity=Severity.INFO,
        verdict=Verdict.FAIL,
        confidence=0.8,
        prompt_version="model_rubric_checker:v1",
    )

    findings, warnings = postprocess_findings([finding])

    assert warnings == []
    assert findings[0].severity == Severity.MINOR
    assert findings[0].needs_human_review is True
    assert findings[0].extra["original_severity"] == "info"


def test_postprocess_clears_review_for_deterministic_high_confidence() -> None:
    finding = _finding(checker_name="local_link_checker", severity=Severity.MAJOR, verdict=Verdict.FAIL, confidence=0.95)

    findings, _warnings = postprocess_findings([finding])

    assert findings[0].needs_human_review is False


def test_postprocess_collapses_language_duplicate_findings() -> None:
    common_evidence = [Evidence(title="Основание", detail="Одинаковое основание для языковых вариантов.")]
    findings, warnings = postprocess_findings(
        [
            _finding(finding_id="f1", file_path="README.md", evidence=common_evidence),
            _finding(finding_id="f2", file_path="README_RUS.md", evidence=common_evidence),
            _finding(finding_id="f3", file_path="README_UZB.md", evidence=common_evidence),
        ]
    )

    assert len(findings) == 1
    assert findings[0].extra["merged_language_duplicates"] == 2
    assert findings[0].extra["affected_files"] == ["README.md", "README_RUS.md", "README_UZB.md"]
    assert findings[0].evidence[-1].title == "Языковые дубли"
    assert any("языковых дублей" in warning for warning in warnings)
