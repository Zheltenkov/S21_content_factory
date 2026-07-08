"""Тесты правила авто-промоушена/демоушена."""

from content_factory.generation.calibration import engine
from content_factory.generation.calibration.store import load_state


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


def _run(n: int, pass_count: int, criterion_id: str = "2.4.7") -> dict:
    """Прогнать n раз с ravномерно распределёнными провалами (не фронт-загрузка)."""
    fail_count = n - pass_count
    fail_indices = {round(k * n / fail_count) for k in range(fail_count)} if fail_count else set()
    state: dict = {}
    for i in range(n):
        passed = i not in fail_indices
        state = engine.record_and_calibrate(_rubric(passed, criterion_id), None, run_id=f"r{i}")
    return state


def _strictness(state: dict, criterion_id: str) -> str | None:
    entry = state["criteria"].get(criterion_id)
    return entry.get("strictness") if entry else None


def test_promotes_when_stable_above_threshold() -> None:
    state = _run(50, 48)  # 0.96 ≥ 0.95
    assert _strictness(state, "2.4.7") == "hard"
    assert state["audit"][-1]["action"] == "promote"


def test_no_promote_below_min_samples() -> None:
    state = _run(40, 40)  # 100% но < N_MIN=50
    assert _strictness(state, "2.4.7") in (None, "soft")


def test_no_promote_below_pass_rate() -> None:
    state = _run(60, 54)  # 0.90 < 0.95
    assert _strictness(state, "2.4.7") in (None, "soft")


def test_denylisted_criterion_not_promoted(monkeypatch) -> None:
    monkeypatch.setenv("CALIBRATION_DENYLIST", "2.4.7")
    state = _run(50, 50)
    assert _strictness(state, "2.4.7") in (None, "soft")


def test_auto_demote_after_regression() -> None:
    _run(50, 50)  # promote to hard
    # 30 провалов подряд роняют скользящий pass-rate ниже DEMOTE_RATE
    state: dict = {}
    for i in range(30):
        state = engine.record_and_calibrate(_rubric(False), None, run_id=f"f{i}")
    assert _strictness(state, "2.4.7") == "soft"
    assert any(entry["action"] == "demote" for entry in state["audit"])


def test_state_persists_between_calls() -> None:
    _run(50, 48)
    reloaded = load_state()
    assert reloaded["criteria"]["2.4.7"]["strictness"] == "hard"
