"""Конфигурация авто-калибровки strictness (SOFT→HARD).

Два флага независимы: `CALIBRATION_ENABLED` включает запись исходов + пересчёт состояния
(безопасно, только данные), `CALIBRATION_ENFORCE` — применение промотированного HARD в
пайплайне (реальная блокировка). Оба default off: сначала shadow-накопление, потом enforce.
Пути — под runtime artifacts dir, переопределяются env.
"""

from __future__ import annotations

import os
from pathlib import Path

_TRUTHY = {"1", "true", "yes", "on"}


def runtime_artifacts_dir() -> Path:
    """Return the writable runtime directory for calibration artifacts."""

    override = os.getenv("CONTENT_FACTORY_RUNTIME_DIR", "").strip()
    base_dir = Path(override).expanduser() if override else Path.cwd() / "artifacts"
    return base_dir / "generation"


def _flag(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in _TRUTHY


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def calibration_enabled() -> bool:
    """Запись исходов + пересчёт состояния (без влияния на блокировку)."""
    return _flag("CALIBRATION_ENABLED")


def calibration_enforce() -> bool:
    """Применять промотированный HARD в пайплайне (реальная блокировка)."""
    return _flag("CALIBRATION_ENFORCE")


def n_min() -> int:
    """Минимум прогонов на критерий до промоушена."""
    return _int("CALIBRATION_N_MIN", 50)


def promote_rate() -> float:
    """Порог pass-rate для промоушена SOFT→HARD."""
    return _float("CALIBRATION_PROMOTE_RATE", 0.95)


def demote_rate() -> float:
    """Нижний порог pass-rate для авто-демоушена HARD→SOFT."""
    return _float("CALIBRATION_DEMOTE_RATE", 0.85)


def window() -> int:
    """Скользящее окно последних прогонов на критерий."""
    return _int("CALIBRATION_WINDOW", 200)


def denylist() -> frozenset[str]:
    """Критерии, которые НЕ авто-промотятся (субъективные)."""
    raw = os.getenv("CALIBRATION_DENYLIST", "").strip()
    if raw:
        return frozenset(item.strip() for item in raw.split(",") if item.strip())
    return frozenset({"didactic:school_tone", "didactic:naturalness"})


def log_path() -> Path:
    """Путь к append-логу исходов калибровки (jsonl)."""
    override = os.getenv("CALIBRATION_LOG_PATH", "").strip()
    return Path(override).expanduser() if override else runtime_artifacts_dir() / "calibration_log.jsonl"


def state_path() -> Path:
    """Путь к состоянию strictness (json, с аудит-логом промоушенов)."""
    override = os.getenv("CALIBRATION_STATE_PATH", "").strip()
    return Path(override).expanduser() if override else runtime_artifacts_dir() / "strictness_state.json"
