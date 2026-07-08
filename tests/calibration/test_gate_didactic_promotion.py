"""Промоушен дидактического дименшена → critical в гейте при enforce."""

from content_factory.generation.calibration import engine, strictness
from content_factory.generation.methodology import MethodologyGate

_RUBRIC = {"total": 1, "max_score": 1, "items": [{"id": "1.1", "status": "passed", "parent_id": None}]}


def _promote_coherence() -> None:
    passing = {"dimensions": [{"dimension": "coherence"}], "abstain_reasons": []}
    for i in range(50):
        engine.record_and_calibrate({"items": []}, passing, run_id=f"r{i}")


def _didactic_below() -> dict:
    return {
        "overall_raw": 2.0,
        "needs_human_review": False,
        "abstain_reasons": ["below_floor:coherence"],
        "jury": ["m1", "m2"],
        "n_jury": 2,
        "dimensions": [],
    }


def test_promoted_didactic_below_floor_blocks_when_enforced(monkeypatch) -> None:
    _promote_coherence()
    monkeypatch.setenv("CALIBRATION_ENFORCE", "true")
    strictness.reset_cache()

    review = MethodologyGate().review(
        "evaluation", {"rubric_json": _RUBRIC, "didactic_json": _didactic_below()}
    )

    by_code = {issue.code: issue for issue in review.issues}
    assert "evaluation.didactic_promoted_failed" in by_code
    assert by_code["evaluation.didactic_promoted_failed"].severity == "critical"
    assert review.human_review_required is True


def test_promoted_didactic_stays_advisory_in_shadow_mode() -> None:
    _promote_coherence()  # promoted in state, but enforce off
    strictness.reset_cache()

    review = MethodologyGate().review(
        "evaluation", {"rubric_json": _RUBRIC, "didactic_json": _didactic_below()}
    )

    by_code = {issue.code: issue for issue in review.issues}
    assert "evaluation.didactic_promoted_failed" not in by_code
    assert by_code["evaluation.didactic_below_floor"].severity == "major"
    assert review.human_review_required is False
