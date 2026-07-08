"""Deterministic CI gate helpers for golden-set evaluation runs."""

from __future__ import annotations

from .models import EvalCaseResult, EvalRunSummary

MISSING_OUTPUT_REASON = "missing generated output"
EVALUATION_FAILED_REASON_PREFIX = "evaluation failed:"


def evaluation_gate_failures(
    summary: EvalRunSummary,
    *,
    pass_rate_floor: float | None = None,
    require_cases: int | None = None,
    fail_on_missing: bool = False,
    fail_on_errors: bool = False,
) -> list[str]:
    """Return human-readable gate failures for an evaluated golden-set run."""
    failures: list[str] = []

    if require_cases is not None and summary.total_cases < require_cases:
        failures.append(f"total_cases {summary.total_cases} below required {require_cases}")

    if pass_rate_floor is not None and summary.pass_rate < pass_rate_floor:
        failures.append(f"pass_rate {summary.pass_rate:.3f} below required {pass_rate_floor:.3f}")

    if fail_on_missing:
        missing_case_ids = _case_ids_with_missing_outputs(summary.results)
        if missing_case_ids:
            failures.append(f"missing outputs: {', '.join(missing_case_ids)}")

    if fail_on_errors:
        error_case_ids = _case_ids_with_evaluation_errors(summary.results)
        if error_case_ids:
            failures.append(f"evaluation errors: {', '.join(error_case_ids)}")

    return failures


def _case_ids_with_missing_outputs(results: list[EvalCaseResult]) -> list[str]:
    """Collect stable case IDs where the generation artifact is absent."""
    return sorted(result.case_id for result in results if MISSING_OUTPUT_REASON in result.reasons)


def _case_ids_with_evaluation_errors(results: list[EvalCaseResult]) -> list[str]:
    """Collect stable case IDs where the evaluator itself failed."""
    return sorted(
        result.case_id
        for result in results
        if result.error or any(reason.startswith(EVALUATION_FAILED_REASON_PREFIX) for reason in result.reasons)
    )
