"""Тесты effective_strictness + policy-override при enforce."""

from content_factory.generation.calibration import engine, strictness
from content_factory.generation.models.criteria_models import (
    CheckMethod,
    CriteriaItem,
    StrictnessLevel,
)
from content_factory.generation.validators.rubric.policy import apply_rubric_warning_policy


def _rubric(passed: bool, criterion_id: str = "2.4.7") -> dict:
    return {
        "items": [
            {
                "id": criterion_id,
                "check_method": "script",
                "score": 1 if passed else 0,
                "details": {} if passed else {"original_score": 0},
            }
        ]
    }


def _promote(criterion_id: str = "2.4.7") -> None:
    for i in range(50):
        engine.record_and_calibrate(_rubric(True, criterion_id), None, run_id=f"r{i}")


def test_effective_strictness_shadow_when_enforce_off() -> None:
    _promote()
    strictness.reset_cache()
    assert strictness.effective_strictness("2.4.7", StrictnessLevel.SOFT) == StrictnessLevel.SOFT
    assert strictness.is_promoted("2.4.7") is False


def test_effective_strictness_hard_when_enforced(monkeypatch) -> None:
    _promote()
    monkeypatch.setenv("CALIBRATION_ENFORCE", "true")
    strictness.reset_cache()
    assert strictness.effective_strictness("2.4.7", StrictnessLevel.SOFT) == StrictnessLevel.HARD
    assert strictness.is_promoted("2.4.7") is True


def _soft_failing_item() -> CriteriaItem:
    return CriteriaItem(
        id="2.4.7",
        title="Проверка читабельности текста",
        description="band",
        check_method=CheckMethod.SCRIPT,
        score=0,
        comments=["низкая читабельность"],
        strictness=StrictnessLevel.SOFT,
    )


def test_policy_masks_soft_failure_without_enforcement() -> None:
    _promote()
    strictness.reset_cache()
    normalized = apply_rubric_warning_policy([_soft_failing_item()])[0]
    # shadow: провал остаётся предупреждением (score→1)
    assert normalized.score == 1
    assert normalized.details["severity"] == "warning"


def test_policy_blocks_promoted_failure_when_enforced(monkeypatch) -> None:
    _promote()
    monkeypatch.setenv("CALIBRATION_ENFORCE", "true")
    strictness.reset_cache()
    normalized = apply_rubric_warning_policy([_soft_failing_item()])[0]
    # promoted+enforce: провал блокирует (не маскируется)
    assert normalized.score == 0
    assert normalized.strictness == StrictnessLevel.HARD
