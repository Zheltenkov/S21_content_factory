"""Авто-калибровка strictness критериев (SOFT→HARD по правилу).

Копит per-criterion исходы с реальных прогонов, промотирует стабильно проходящие критерии
в блокирующие (HARD), демотирует при просадке. Два флага: `CALIBRATION_ENABLED` (запись +
состояние) и `CALIBRATION_ENFORCE` (реальная блокировка), оба default off — сначала shadow.
"""

from .engine import recalibrate, record_and_calibrate
from .strictness import effective_strictness, is_promoted

__all__ = [
    "effective_strictness",
    "is_promoted",
    "recalibrate",
    "record_and_calibrate",
]
