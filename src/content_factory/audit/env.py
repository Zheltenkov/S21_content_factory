"""Безопасное чтение локального `.env` без внешних зависимостей."""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path) -> dict[str, str]:
    """Читает `.env` и возвращает пары ключ-значение, не печатая секреты."""

    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = _strip_value(value.strip())
        if key:
            values[key] = value
    return values


def get_env_value(names: tuple[str, ...], env_file_values: dict[str, str]) -> str | None:
    """Возвращает первое найденное значение из окружения или `.env`."""

    for name in names:
        value = os.getenv(name)
        if value:
            return value
        value = env_file_values.get(name)
        if value:
            return value
    return None


def _strip_value(value: str) -> str:
    """Удаляет кавычки и простой строковый комментарий после значения."""

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return value
