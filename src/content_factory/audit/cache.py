"""Файловый кэш дорогих внешних проверок."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class AuditCache:
    """Простой JSON-кэш с явным пространством ключей для разных проверок."""

    def __init__(self, path: Path, payload: dict[str, Any] | None = None) -> None:
        self.path = path
        self._payload = payload or {"version": 1, "items": {}}

    @classmethod
    def load(cls, path: Path) -> AuditCache:
        """Загружаем кэш; повреждённый файл не должен ломать аудит."""

        if not path.exists():
            return cls(path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls(path)
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), dict):
            return cls(path)
        return cls(path, payload)

    def get(self, namespace: str, key: str) -> dict[str, Any] | None:
        """Возвращаем запись кэша, если она есть и имеет ожидаемый формат."""

        item = self._payload.setdefault("items", {}).get(self._cache_key(namespace, key))
        return item if isinstance(item, dict) else None

    def set(self, namespace: str, key: str, value: dict[str, Any]) -> None:
        """Сохраняем результат проверки под стабильным ключом."""

        self._payload.setdefault("items", {})[self._cache_key(namespace, key)] = value

    def save(self) -> None:
        """Записываем кэш рядом с отчётами."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload_text = json.dumps(self._payload, ensure_ascii=False, indent=2)
        temp_path = self.path.with_name(f"{self.path.name}.tmp")
        try:
            temp_path.write_text(payload_text, encoding="utf-8")
            temp_path.replace(self.path)
        except OSError:
            try:
                self.path.write_text(payload_text, encoding="utf-8")
            except OSError:
                # Кэш ускоряет и удешевляет повторные прогоны, но не должен останавливать аудит.
                return

    @staticmethod
    def _cache_key(namespace: str, key: str) -> str:
        """Разделяем одинаковые хэши из разных проверяющих модулей."""

        return f"{namespace}:{key}"
