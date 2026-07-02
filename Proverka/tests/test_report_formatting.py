from content_audit.domain import Criterion, Evidence, Finding, Severity, Verdict
from content_audit.report_formatting import format_finding_explanation, format_finding_fragment


def test_format_finding_fragment_rewrites_markdown_image() -> None:
    finding = Finding(
        finding_id="image",
        unit_id="unit",
        branch=None,
        criterion=Criterion.RIGHTS,
        severity=Severity.MINOR,
        verdict=Verdict.WARNING,
        confidence=0.7,
        quote="![Illustration](misc/images/IMG_1815.jpg)",
        recommendation="Добавить источник изображения.",
        checker_name="rights_originality_checker",
    )

    assert format_finding_fragment(finding) == "Изображение: misc/images/IMG_1815.jpg"


def test_format_finding_explanation_rewrites_common_machine_phrases() -> None:
    finding = Finding(
        finding_id="checklist",
        unit_id="unit",
        branch=None,
        criterion=Criterion.CHECKLIST_ALIGNMENT,
        severity=Severity.MAJOR,
        verdict=Verdict.WARNING,
        confidence=0.8,
        evidence=[
            Evidence(
                title="Проверка",
                detail=(
                    "Сильных совпадений: 10 из 10; слабых совпадений: 0 из 10; "
                    "не сопоставлено: 0 из 10. Развёрнутых описаний: 3 из 10."
                ),
            )
        ],
        recommendation="Добавить критерии приёмки.",
        checker_name="checklist_checker",
    )

    explanation = format_finding_explanation(finding)

    assert "С чек-листом уверенно сопоставлено 10 из 10 пунктов" in explanation
    assert "Развёрнутые описания есть у 3 из 10 пунктов" in explanation
    assert "Что сделать: Добавить критерии приёмки." in explanation
