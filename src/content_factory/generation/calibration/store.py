"""Персистентность калибровки: append-лог исходов + состояние strictness.

Лог — jsonl (по строке на исход), состояние — json с per-criterion strictness и аудит-логом
промоушенов/демоушенов. Запись состояния атомарна (tmp + os.replace).
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections import defaultdict
from typing import Any

from . import config
from .outcomes import CriterionOutcome


def append_outcomes(outcomes: list[CriterionOutcome], run_id: str) -> None:
    """Дописать исходы прогона в jsonl-лог."""
    if not outcomes:
        return
    path = config.log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = time.time()
    with path.open("a", encoding="utf-8") as handle:
        for outcome in outcomes:
            record = {
                "run_id": run_id,
                "ts": ts,
                "id": outcome.id,
                "passed": outcome.passed,
                "kind": outcome.kind,
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def window_aggregates() -> dict[str, tuple[int, int]]:
    """Per-criterion (pass_count, total) за скользящее окно последних прогонов."""
    path = config.log_path()
    if not path.exists():
        return {}
    per_criterion: dict[str, list[bool]] = defaultdict(list)
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            per_criterion[str(record.get("id"))].append(bool(record.get("passed")))
    limit = config.window()
    aggregates: dict[str, tuple[int, int]] = {}
    for criterion_id, results in per_criterion.items():
        recent = results[-limit:]
        aggregates[criterion_id] = (sum(1 for r in recent if r), len(recent))
    return aggregates


def load_state() -> dict[str, Any]:
    """Прочитать состояние strictness; пустой каркас, если файла нет/битый."""
    path = config.state_path()
    if not path.exists():
        return {"criteria": {}, "audit": []}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return {"criteria": {}, "audit": []}
    if not isinstance(data, dict):
        return {"criteria": {}, "audit": []}
    data.setdefault("criteria", {})
    data.setdefault("audit", [])
    return data


def save_state(state: dict[str, Any]) -> None:
    """Атомарно записать состояние strictness (tmp + replace)."""
    path = config.state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".strictness_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
