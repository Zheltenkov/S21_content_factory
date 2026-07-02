"""Offline evaluation runner for generated README artifacts."""

from __future__ import annotations

from collections.abc import Callable

from ..models.criteria_models import CriteriaReport
from ..models.readme_document import ReadmeDocument
from ..validators.rubric.scorer import RubricScorer
from .metrics import build_eval_metrics, threshold_failures
from .models import (
    EvalCaseResult,
    EvalRunSummary,
    EvalThresholds,
    GeneratedProjectOutput,
    GoldenDataset,
    GoldenProjectCase,
)

ScorerFactory = Callable[[str], RubricScorer]


class EvaluationHarness:
    """Run golden-set evaluation over already generated README outputs."""

    def __init__(self, scorer_factory: ScorerFactory | None = None) -> None:
        self._scorer_factory = scorer_factory or (lambda language: RubricScorer(language=language, llm_client=None))

    def evaluate_case(self, case: GoldenProjectCase, output: GeneratedProjectOutput) -> EvalCaseResult:
        """Evaluate one generated output against one golden case."""
        return self._evaluate_case_with_thresholds(case, output, case.expectations.rubric_thresholds)

    def evaluate_dataset(
        self,
        dataset: GoldenDataset,
        outputs_by_case_id: dict[str, GeneratedProjectOutput],
    ) -> EvalRunSummary:
        """Evaluate all dataset cases that have a matching generated output."""
        results: list[EvalCaseResult] = []
        for case in dataset.cases:
            output = outputs_by_case_id.get(case.id)
            if output is None:
                results.append(
                    EvalCaseResult(
                        case_id=case.id,
                        title=case.title,
                        passed=False,
                        reasons=["missing generated output"],
                    )
                )
                continue
            thresholds = self._merge_thresholds(dataset.defaults, case.expectations.rubric_thresholds)
            results.append(self._evaluate_case_with_thresholds(case, output, thresholds))
        return self._build_summary(dataset, results)

    def _evaluate_case_with_thresholds(
        self,
        case: GoldenProjectCase,
        output: GeneratedProjectOutput,
        thresholds: EvalThresholds,
    ) -> EvalCaseResult:
        """Evaluate one case with explicit thresholds resolved from dataset defaults."""
        try:
            document = ReadmeDocument.from_markdown(output.markdown, fallback_title=case.title)
            report = output.rubric_report or self._score_document(case, document)
            metrics = build_eval_metrics(case=case, output=output, document=document, report=report)
            reasons = threshold_failures(metrics, thresholds)
            return EvalCaseResult(
                case_id=case.id,
                title=case.title,
                passed=not reasons,
                metrics=metrics,
                reasons=reasons,
                model=output.model_name,
                provider=output.provider,
                run_id=output.run_id,
            )
        except Exception as exc:
            return EvalCaseResult(
                case_id=case.id,
                title=case.title,
                passed=False,
                reasons=[f"evaluation failed: {exc}"],
                model=output.model_name,
                provider=output.provider,
                run_id=output.run_id,
                error=str(exc),
            )

    def _score_document(self, case: GoldenProjectCase, document: ReadmeDocument) -> CriteriaReport:
        """Run the production rubric scorer with golden-case learning outcomes."""
        language = str(case.seed.get("language") or "ru")
        learning_outcomes = case.seed.get("learning_outcomes")
        if not isinstance(learning_outcomes, list):
            learning_outcomes = []
        scorer = self._scorer_factory(language)
        return scorer.score_document(document, learning_outcomes=learning_outcomes, use_cache=False)

    @staticmethod
    def _merge_thresholds(defaults: EvalThresholds, overrides: EvalThresholds) -> EvalThresholds:
        """Apply dataset threshold defaults unless a case explicitly overrides a field."""
        merged = defaults.model_dump()
        for field_name in overrides.model_fields_set:
            merged[field_name] = getattr(overrides, field_name)
        return EvalThresholds.model_validate(merged)

    @staticmethod
    def _build_summary(dataset: GoldenDataset, results: list[EvalCaseResult]) -> EvalRunSummary:
        """Aggregate per-case metrics into a run-level report."""
        total_cases = len(results)
        passed_cases = sum(1 for result in results if result.passed)
        return EvalRunSummary(
            dataset_name=dataset.name,
            dataset_version=dataset.version,
            total_cases=total_cases,
            passed_cases=passed_cases,
            pass_rate=_avg([1.0 if result.passed else 0.0 for result in results]),
            average_score_ratio=_avg([result.metrics.score_ratio for result in results]),
            average_structure_pass_rate=_avg([result.metrics.structure_pass_rate for result in results]),
            average_practice_atomicity=_avg([result.metrics.practice_atomicity for result in results]),
            average_didactics_compliance=_avg([result.metrics.didactics_compliance for result in results]),
            total_cost_usd=sum(result.metrics.cost_usd for result in results),
            total_latency_ms=sum(result.metrics.latency_ms for result in results),
            total_retry_count=sum(result.metrics.retry_count for result in results),
            total_fallback_count=sum(result.metrics.fallback_count for result in results),
            results=results,
        )


def _avg(values: list[float]) -> float:
    """Return a stable average for optional metric collections."""
    if not values:
        return 0.0
    return sum(values) / len(values)
