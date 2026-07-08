from __future__ import annotations

from content_factory.generation.evaluation.gate import evaluation_gate_failures
from content_factory.generation.evaluation.models import EvalCaseResult, EvalRunSummary


def _summary(*results: EvalCaseResult) -> EvalRunSummary:
    cases = list(results)
    passed_cases = sum(1 for result in cases if result.passed)
    return EvalRunSummary(
        dataset_name="gate-dataset",
        dataset_version="v1",
        total_cases=len(cases),
        passed_cases=passed_cases,
        pass_rate=passed_cases / len(cases) if cases else 0.0,
        results=cases,
    )


def test_evaluation_gate_reports_configured_failures() -> None:
    summary = _summary(
        EvalCaseResult(case_id="ok", title="OK", passed=True),
        EvalCaseResult(case_id="missing", title="Missing", passed=False, reasons=["missing generated output"]),
        EvalCaseResult(case_id="error", title="Error", passed=False, reasons=["evaluation failed: boom"]),
    )

    failures = evaluation_gate_failures(
        summary,
        pass_rate_floor=0.9,
        require_cases=4,
        fail_on_missing=True,
        fail_on_errors=True,
    )

    assert failures == [
        "total_cases 3 below required 4",
        "pass_rate 0.333 below required 0.900",
        "missing outputs: missing",
        "evaluation errors: error",
    ]


def test_evaluation_gate_keeps_missing_and_errors_opt_in() -> None:
    summary = _summary(
        EvalCaseResult(case_id="missing", title="Missing", passed=False, reasons=["missing generated output"]),
        EvalCaseResult(case_id="error", title="Error", passed=False, error="boom"),
    )

    failures = evaluation_gate_failures(summary)

    assert failures == []
