from content_gen.methodology.decision import MethodologyGatePolicy
from content_gen.methodology.models import StageReviewIssue, StageReviewResult


def test_gate_policy_observe_warns_but_allows_critical_review() -> None:
    review = StageReviewResult(
        stage="context",
        status="failed",
        issues=[
            StageReviewIssue(
                code="context.learning_outcomes_empty",
                message="Learning outcomes are empty.",
                severity="critical",
            )
        ],
        human_review_required=True,
    )

    decision = MethodologyGatePolicy(mode="observe").decide(review)

    assert decision.action == "warn"
    assert decision.can_continue is True
    assert decision.human_review_required is True


def test_gate_policy_approval_pauses_critical_review() -> None:
    review = StageReviewResult(
        stage="context",
        status="failed",
        issues=[
            StageReviewIssue(
                code="context.learning_outcomes_empty",
                message="Learning outcomes are empty.",
                severity="critical",
            )
        ],
        human_review_required=True,
    )

    decision = MethodologyGatePolicy(mode="approval").decide(review)

    assert decision.action == "pause"
    assert decision.blocking is True
    assert decision.can_continue is False


def test_gate_policy_strict_fails_critical_review() -> None:
    review = StageReviewResult(
        stage="evaluation",
        status="failed",
        issues=[
            StageReviewIssue(
                code="evaluation.rubric_missing",
                message="Rubric is missing.",
                severity="critical",
            )
        ],
    )

    decision = MethodologyGatePolicy(mode="strict").decide(review)

    assert decision.action == "fail"
    assert decision.blocking is True
