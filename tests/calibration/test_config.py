from __future__ import annotations

from content_factory.generation.calibration import config


def test_calibration_default_paths_use_runtime_artifacts_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CONTENT_FACTORY_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("CALIBRATION_LOG_PATH", raising=False)
    monkeypatch.delenv("CALIBRATION_STATE_PATH", raising=False)

    assert config.log_path() == tmp_path / "artifacts" / "generation" / "calibration_log.jsonl"
    assert config.state_path() == tmp_path / "artifacts" / "generation" / "strictness_state.json"


def test_calibration_runtime_dir_can_be_configured(monkeypatch, tmp_path) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setenv("CONTENT_FACTORY_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.delenv("CALIBRATION_LOG_PATH", raising=False)
    monkeypatch.delenv("CALIBRATION_STATE_PATH", raising=False)

    assert config.log_path() == runtime_dir / "generation" / "calibration_log.jsonl"
    assert config.state_path() == runtime_dir / "generation" / "strictness_state.json"
