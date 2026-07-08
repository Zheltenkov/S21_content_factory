"""Изоляция калибровки: temp-пути + сброс кэша strictness на каждый тест."""

import pytest

from content_factory.generation.calibration import strictness


@pytest.fixture(autouse=True)
def _isolated_calibration(tmp_path, monkeypatch):
    monkeypatch.setenv("CALIBRATION_LOG_PATH", str(tmp_path / "log.jsonl"))
    monkeypatch.setenv("CALIBRATION_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.delenv("CALIBRATION_ENFORCE", raising=False)
    monkeypatch.delenv("CALIBRATION_ENABLED", raising=False)
    strictness.reset_cache()
    yield
    strictness.reset_cache()
