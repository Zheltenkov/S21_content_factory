"""
Механизм отмены операций генерации контента.
"""

import threading
from dataclasses import dataclass


class CancelledError(Exception):
    """Исключение при отмене операции."""

    def __init__(self, reason: str = "Operation cancelled"):
        self.reason = reason
        super().__init__(reason)


@dataclass
class CancellationToken:
    """
    Токен для отмены операций.
    
    Использование:
        token = CancellationToken()
        
        # В цикле обработки:
        token.check()  # Бросает CancelledError если отменено
        
        # Для отмены:
        token.cancel("User cancelled")
    """
    cancelled: bool = False
    reason: str = ""
    _lock: threading.Lock = threading.Lock()

    def cancel(self, reason: str = "Operation cancelled"):
        """
        Отменяет операцию.
        
        Args:
            reason: Причина отмены
        """
        with self._lock:
            self.cancelled = True
            self.reason = reason

    def check(self):
        """
        Проверяет, была ли отмена. Бросает CancelledError если да.
        
        Raises:
            CancelledError: Если операция была отменена
        """
        with self._lock:
            if self.cancelled:
                raise CancelledError(self.reason)

    def is_cancelled(self) -> bool:
        """
        Проверяет, была ли отмена, без исключения.
        
        Returns:
            True если отменено, False иначе
        """
        with self._lock:
            return self.cancelled

