"""Движок калибровки: запись исходов + правило авто-промоушена/демоушена strictness.

Правило (параметры в `config`): критерий промотится SOFT→HARD при `samples≥N_MIN` и
`rate≥PROMOTE_RATE`, если не в denylist. Промотированный демотится HARD→SOFT при
`rate<DEMOTE_RATE`. Всё под аудит-логом. Идемпотентно и робастно (одна запись на прогон).
"""

from __future__ import annotations

import time
from typing import Any

from . import config, store
from .outcomes import didactic_outcomes, rubric_outcomes


def record_and_calibrate(
    rubric_json: Any = None,
    didactic_json: Any = None,
    *,
    run_id: str,
) -> dict[str, Any]:
    """Записать исходы прогона и пересчитать состояние strictness по правилу."""
    outcomes = rubric_outcomes(rubric_json) + didactic_outcomes(didactic_json)
    store.append_outcomes(outcomes, run_id)
    return recalibrate()


def recalibrate() -> dict[str, Any]:
    """Пересчитать strictness-состояние из накопленного лога (без новой записи)."""
    aggregates = store.window_aggregates()
    state = store.load_state()
    criteria: dict[str, Any] = state["criteria"]
    audit: list[Any] = state["audit"]

    n_min = config.n_min()
    promote_rate = config.promote_rate()
    demote_rate = config.demote_rate()
    denied = config.denylist()

    changed = False
    for criterion_id, (passes, total) in aggregates.items():
        if total <= 0:
            continue
        rate = passes / total
        entry = criteria.get(criterion_id, {"strictness": "soft"})
        current = str(entry.get("strictness", "soft"))
        new_strictness = current

        if (
            current == "soft"
            and total >= n_min
            and rate >= promote_rate
            and criterion_id not in denied
        ):
            new_strictness = "hard"
        elif current == "hard" and total >= n_min and rate < demote_rate:
            new_strictness = "soft"

        if new_strictness != current:
            audit.append({
                "action": "promote" if new_strictness == "hard" else "demote",
                "id": criterion_id,
                "rate": round(rate, 3),
                "samples": total,
                "ts": time.time(),
            })
            changed = True

        # Обновляем свежие метрики только у отслеживаемых критериев (транзиция или уже в state).
        if new_strictness != current or criterion_id in criteria:
            criteria[criterion_id] = {
                "strictness": new_strictness,
                "samples": total,
                "rate": round(rate, 3),
                "updated_at": time.time(),
            }
            changed = True

    if changed:
        store.save_state(state)
    return state
