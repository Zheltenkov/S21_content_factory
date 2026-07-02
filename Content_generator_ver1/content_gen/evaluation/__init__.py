"""Offline evaluation harness for generated educational projects."""

from .dataset import load_generated_outputs, load_golden_dataset
from .models import (
    EvalCaseResult,
    EvalMetricBreakdown,
    EvalRunSummary,
    EvalThresholds,
    GeneratedProjectOutput,
    GoldenDataset,
    GoldenProjectCase,
    GoldenProjectExpectations,
)
from .regeneration import (
    RegenerationEvalCase,
    RegenerationEvalCaseResult,
    RegenerationEvalDataset,
    RegenerationEvalMetrics,
    RegenerationEvalOutput,
    RegenerationEvalRunSummary,
    RegenerationEvalThresholds,
    RegenerationEvaluationHarness,
    load_regeneration_eval_dataset,
    load_regeneration_eval_outputs,
)
from .runner import EvaluationHarness

__all__ = [
    "EvalCaseResult",
    "EvalMetricBreakdown",
    "EvalRunSummary",
    "EvalThresholds",
    "EvaluationHarness",
    "GeneratedProjectOutput",
    "GoldenDataset",
    "GoldenProjectCase",
    "GoldenProjectExpectations",
    "RegenerationEvalCase",
    "RegenerationEvalCaseResult",
    "RegenerationEvalDataset",
    "RegenerationEvalMetrics",
    "RegenerationEvalOutput",
    "RegenerationEvalRunSummary",
    "RegenerationEvalThresholds",
    "RegenerationEvaluationHarness",
    "load_generated_outputs",
    "load_golden_dataset",
    "load_regeneration_eval_dataset",
    "load_regeneration_eval_outputs",
]
