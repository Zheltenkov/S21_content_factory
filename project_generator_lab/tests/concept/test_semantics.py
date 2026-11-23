"""Regressions for field meaning and contradictions in positive review labels."""

from pathlib import Path

import pytest
from project_generator_lab.concept.ingestion import parse_curriculum
from project_generator_lab.concept.input_semantics import input_semantics
from project_generator_lab.concept.models import Finding, GroundedOutcome, OutcomeCheck
from project_generator_lab.concept.validation import apply_demo_policy, reconcile_review
from project_generator_lab.tests.concept.test_validation import reviewed_run
from pydantic import ValidationError


def test_program_position_comes_from_actual_row_order_without_mutating_inputs():
    run = reviewed_run()
    path = Path(__file__).parents[2] / "concept/examples/curriculum.csv"
    run.request.curriculum = parse_curriculum(path.name, path.read_bytes())
    rows = run.request.curriculum.rows
    run.request.selected_row_id = rows[2].id
    before = run.request.model_dump()
    semantics = input_semantics(run.request)
    order = semantics["program_order"]
    assert isinstance(order, dict)
    assert order["before_ids"] == [r.id for r in rows[:2]]
    assert order["after_ids"] == [r.id for r in rows[3:]]
    assert order["selected_position"] == 3
    assert run.request.model_dump() == before


def test_new_model_contract_requires_action_comparison_but_old_results_still_load():
    legacy = {"outcome_number": 1, "task_numbers": [1], "status": "confirmed", "explanation": "Вывод", "evidence_ids": ["form.outcomes"]}
    assert OutcomeCheck.model_validate(legacy).required_action == ""
    with pytest.raises(ValidationError):
        GroundedOutcome.model_validate(legacy)


@pytest.mark.parametrize("defect", ["gap", "task", "source", "comparison", "duplicate"])
def test_positive_outcome_cannot_hide_gap_or_broken_mapping(defect):
    run = reviewed_run()
    review = run.review
    run.review_attempts.append(review.model_copy(deep=True))
    original = run.review_attempts[0].model_dump()
    if defect == "gap":
        review.outcomes[0].gap = "Выбор стратегии не показывает её применение"
    elif defect == "task":
        review.outcomes[0].task_numbers = [99]
    elif defect == "source":
        review.outcomes[0].evidence_ids = ["unknown"]
    elif defect == "comparison":
        review.outcomes[0].observable_evidence = ""
    else:
        review.outcomes.append(review.outcomes[0].model_copy(deep=True))
    reconcile_review(review, run)
    apply_demo_policy(run)
    assert review.outcomes[0].status == "needs_information"
    assert review.checks[0].status == "needs_information"
    assert "подтверждено ОР: 1 из 2" in review.summary
    assert all(f.severity == "warning" for f in review.findings)
    assert run.review_attempts[0].model_dump() == original


def test_critical_constraint_and_future_materials_are_distinguished():
    run = reviewed_run()
    review = run.review
    review.checks[-1].status = "deferred"
    review.findings.append(Finding(severity="blocker", rule_id="constraints", message="Превышено время", suggestion="Сократить объём"))
    reconcile_review(review, run)
    assert all(o.status == "confirmed" for o in review.outcomes)
    assert review.checks[3].status == "needs_information"
    assert review.checks[-1].status == "deferred"
    assert "будущих материалов" in review.summary
    apply_demo_policy(run)
    assert run.demo_mode and review.findings[0].severity == "warning"


def test_valid_action_comparison_preserves_positive_status():
    run = reviewed_run()
    reconcile_review(run.review, run)
    assert all(o.status == "confirmed" for o in run.review.outcomes)
    assert all(c.status == "confirmed" for c in run.review.checks)
    assert "подтверждено ОР: 2 из 2" in run.review.summary
    assert not run.review.findings
