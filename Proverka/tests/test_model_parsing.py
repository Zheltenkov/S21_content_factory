from content_audit.checks import _finding_from_model_item
from content_audit.domain import ContentUnit, Criterion, Severity, Verdict


def test_model_item_accepts_text_confidence(workspace_tmp_path) -> None:
    unit = ContentUnit(unit_id="unit__1", name="unit", root_path=workspace_tmp_path, relative_path=".")
    item = {
        "criterion": "correctness",
        "severity": "Major",
        "verdict": "warning",
        "confidence": "low",
        "quote": "пример",
        "file_path": "README.md",
        "line_start": "12",
        "evidence": "нужна проверка",
        "recommendation": "проверить вручную",
    }

    finding = _finding_from_model_item(unit, "model", item)

    assert finding.criterion == Criterion.CORRECTNESS
    assert finding.severity == Severity.MAJOR
    assert finding.verdict == Verdict.WARNING
    assert finding.confidence == 0.35
    assert finding.location is not None
    assert finding.location.line_start == 12
