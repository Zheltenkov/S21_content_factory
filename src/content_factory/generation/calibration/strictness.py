"""Единая точка effective-strictness: применяет промотированный HARD, если enforce включён.

Читает состояние с кэшем по mtime (дешёвый вызов на каждый критерий при построении отчёта).
Когда `CALIBRATION_ENFORCE` выключен — всегда возвращает default (промоушены копятся в
shadow-режиме, но не блокируют).
"""

from __future__ import annotations

from ..models.criteria_models import StrictnessLevel
from . import config, store

_cache: dict[str, object] = {"mtime": None, "promoted": frozenset()}


def _promoted_ids() -> frozenset[str]:
    path = config.state_path()
    try:
        mtime = path.stat().st_mtime if path.exists() else None
    except OSError:
        mtime = None
    if mtime != _cache["mtime"]:
        state = store.load_state()
        criteria = state.get("criteria", {})
        promoted = frozenset(
            cid
            for cid, entry in criteria.items()
            if isinstance(entry, dict) and entry.get("strictness") == "hard"
        )
        _cache["mtime"] = mtime
        _cache["promoted"] = promoted
    promoted_value = _cache["promoted"]
    return promoted_value if isinstance(promoted_value, frozenset) else frozenset()


def is_promoted(criterion_id: str) -> bool:
    """Промотирован ли критерий в HARD и включён ли enforce."""
    if not config.calibration_enforce():
        return False
    return criterion_id in _promoted_ids()


def effective_strictness(criterion_id: str, default: StrictnessLevel) -> StrictnessLevel:
    """HARD, если критерий промотирован и enforce включён; иначе default."""
    if is_promoted(criterion_id):
        return StrictnessLevel.HARD
    return default


def reset_cache() -> None:
    """Сбросить кэш промоушенов (для тестов)."""
    _cache["mtime"] = None
    _cache["promoted"] = frozenset()
