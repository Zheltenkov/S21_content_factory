"""Кэш для результатов валидации критериев."""

import hashlib
import json
import time
from dataclasses import dataclass
from threading import Lock

from ..models.criteria_models import CriteriaReport


@dataclass
class CacheEntry:
    """Запись в кэше валидации."""
    report: CriteriaReport
    timestamp: float
    ttl: float


class ValidationCache:
    """LRU кэш для результатов валидации по хешу markdown."""

    def __init__(self, max_size: int = 100, ttl: float = 3600.0):
        """
        Инициализация кэша.
        
        Args:
            max_size: Максимальное количество записей в кэше
            ttl: Время жизни записи в секундах (по умолчанию 1 час)
        """
        self._cache: dict[str, CacheEntry] = {}
        self._lock = Lock()
        self._max_size = max_size
        self._ttl = ttl

    def _hash_md(self, md: str, context: object | None = None) -> str:
        """Создает хеш markdown документа."""
        payload = {"md": md, "context": context}
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    def get(self, md: str, context: object | None = None) -> CriteriaReport | None:
        """
        Получает результат валидации из кэша.
        
        Args:
            md: Markdown документ
        
        Returns:
            CriteriaReport или None, если не найдено или истек срок
        """
        cache_key = self._hash_md(md, context)

        with self._lock:
            entry = self._cache.get(cache_key)
            if entry:
                # Проверяем срок действия
                if time.time() - entry.timestamp < entry.ttl:
                    return entry.report
                else:
                    # Удаляем устаревшую запись
                    del self._cache[cache_key]

        return None

    def set(self, md: str, report: CriteriaReport, context: object | None = None):
        """
        Сохраняет результат валидации в кэш.
        
        Args:
            md: Markdown документ
            report: Результат валидации
        """
        cache_key = self._hash_md(md, context)

        with self._lock:
            # Очищаем старые записи, если кэш переполнен
            if len(self._cache) >= self._max_size:
                # Удаляем 10% самых старых записей
                sorted_entries = sorted(
                    self._cache.items(),
                    key=lambda x: x[1].timestamp
                )
                to_remove = max(1, len(sorted_entries) // 10)
                for key, _ in sorted_entries[:to_remove]:
                    del self._cache[key]

            # Сохраняем новую запись
            self._cache[cache_key] = CacheEntry(
                report=report,
                timestamp=time.time(),
                ttl=self._ttl
            )

    def clear(self):
        """Очищает весь кэш."""
        with self._lock:
            self._cache.clear()

    def size(self) -> int:
        """Возвращает текущий размер кэша."""
        with self._lock:
            return len(self._cache)


# Глобальный экземпляр кэша
_global_cache: ValidationCache | None = None


def get_cache() -> ValidationCache:
    """Получает глобальный экземпляр кэша."""
    global _global_cache
    if _global_cache is None:
        _global_cache = ValidationCache()
    return _global_cache
