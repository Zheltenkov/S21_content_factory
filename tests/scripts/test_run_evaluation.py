from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from content_factory.generation.evaluation.models import EvalCaseResult, EvalRunSummary

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "run_evaluation.py"
spec = importlib.util.spec_from_file_location("run_evaluation", MODULE_PATH)
run_evaluation = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = run_evaluation
spec.loader.exec_module(run_evaluation)


def _summary(*results: EvalCaseResult) -> EvalRunSummary:
    cases = list(results)
    passed_cases = sum(1 for result in cases if result.passed)
    return EvalRunSummary(
        dataset_name="script-dataset",
        dataset_version="v1",
        total_cases=len(cases),
        passed_cases=passed_cases,
        pass_rate=passed_cases / len(cases) if cases else 0.0,
        results=cases,
    )


def _run_with_summary(monkeypatch: Any, summary: EvalRunSummary, *args: str) -> int:
    class FakeHarness:
        def evaluate_dataset(self, _dataset: object, _outputs: object) -> EvalRunSummary:
            return summary

    monkeypatch.setattr(run_evaluation, "load_golden_dataset", lambda _path: object())
    monkeypatch.setattr(run_evaluation, "load_generated_outputs", lambda _path: {})
    monkeypatch.setattr(run_evaluation, "EvaluationHarness", lambda: FakeHarness())
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_evaluation.py", "--dataset", "golden.yaml", "--outputs", "outputs.json", *args],
    )
    return run_evaluation.main()


def test_run_evaluation_keeps_ad_hoc_mode_non_blocking(monkeypatch: Any, capsys: Any) -> None:
    code = _run_with_summary(
        monkeypatch,
        _summary(EvalCaseResult(case_id="missing", title="Missing", passed=False, reasons=["missing generated output"])),
    )

    captured = capsys.readouterr()

    assert code == 0
    assert '"pass_rate": 0.0' in captured.out
    assert captured.err == ""


def test_run_evaluation_fails_on_missing_when_enabled(monkeypatch: Any, capsys: Any) -> None:
    code = _run_with_summary(
        monkeypatch,
        _summary(EvalCaseResult(case_id="missing", title="Missing", passed=False, reasons=["missing generated output"])),
        "--fail-on-missing",
    )

    captured = capsys.readouterr()

    assert code == 1
    assert "EVAL_GATE missing outputs: missing" in captured.err


def test_run_evaluation_strict_requires_full_pass_rate(monkeypatch: Any, capsys: Any) -> None:
    code = _run_with_summary(
        monkeypatch,
        _summary(EvalCaseResult(case_id="failed", title="Failed", passed=False, reasons=["score_ratio 0.0 < 1.0"])),
        "--strict",
    )

    captured = capsys.readouterr()

    assert code == 1
    assert "EVAL_GATE pass_rate 0.000 below required 1.000" in captured.err


def test_run_evaluation_requires_minimum_case_count(monkeypatch: Any, capsys: Any) -> None:
    code = _run_with_summary(
        monkeypatch,
        _summary(EvalCaseResult(case_id="ok", title="OK", passed=True)),
        "--require-cases",
        "2",
    )

    captured = capsys.readouterr()

    assert code == 1
    assert "EVAL_GATE total_cases 1 below required 2" in captured.err
