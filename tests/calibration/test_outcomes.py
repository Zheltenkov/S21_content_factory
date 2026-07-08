"""Тесты экстракторов исходов из rubric/didactic json."""

from content_factory.generation.calibration.outcomes import (
    didactic_outcomes,
    rubric_outcomes,
)


def test_rubric_outcomes_only_script_and_true_pass() -> None:
    rubric = {
        "items": [
            {"id": "N.1", "check_method": "script", "score": 1, "details": {}},
            # masked SOFT fail: score стал 1, но original_score=0 → истинный fail
            {"id": "2.4.7", "check_method": "script", "score": 1, "details": {"original_score": 0}},
            # AI-критерий не калибруется
            {"id": "2.4.2", "check_method": "ai_agent", "score": 0, "details": {}},
        ]
    }
    outcomes = {o.id: o.passed for o in rubric_outcomes(rubric)}
    assert outcomes == {"N.1": True, "2.4.7": False}


def test_rubric_outcomes_hard_fail_without_original_score() -> None:
    rubric = {"items": [{"id": "N.2", "check_method": "script", "score": 0, "details": {}}]}
    outcomes = rubric_outcomes(rubric)
    assert outcomes[0].id == "N.2"
    assert outcomes[0].passed is False


def test_didactic_outcomes_below_floor_is_fail() -> None:
    didactic = {
        "dimensions": [{"dimension": "coherence"}, {"dimension": "naturalness"}],
        "abstain_reasons": ["below_floor:naturalness", "jury_split:coherence"],
    }
    outcomes = {o.id: o.passed for o in didactic_outcomes(didactic)}
    assert outcomes == {"didactic:coherence": True, "didactic:naturalness": False}


def test_outcomes_tolerate_malformed_input() -> None:
    assert rubric_outcomes(None) == []
    assert rubric_outcomes({"items": "nope"}) == []
    assert didactic_outcomes({}) == []
