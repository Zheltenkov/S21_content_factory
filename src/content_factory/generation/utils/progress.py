"""
Механизм трекинга прогресса генерации контента.
"""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class PhaseProgress:
    """Информация о прогрессе выполнения фазы."""
    phase: str
    current_item: int
    total_items: int
    message: str
    percent: float  # 0.0 - 1.0

    def __post_init__(self):
        """Вычисляет процент после инициализации."""
        if self.total_items > 0:
            self.percent = min(1.0, max(0.0, self.current_item / self.total_items))
        else:
            self.percent = 0.0


class ProgressTracker:
    """
    Трекер прогресса генерации.
    
    Использование:
        tracker = ProgressTracker(callback=lambda p: print(f"{p.phase}: {p.percent}%"))
        tracker.update("theory", 2, 5, "Обработка части 2")
    """

    def __init__(self, callback: Callable[[PhaseProgress], None] | None = None):
        """
        Инициализация трекера.
        
        Args:
            callback: Функция для обработки обновлений прогресса
        """
        self.callback = callback
        self._current_phase: str | None = None
        self._current_item: int = 0
        self._total_items: int = 0

    def update(
        self,
        phase: str,
        current: int,
        total: int,
        message: str = ""
    ):
        """
        Обновляет прогресс.
        
        Args:
            phase: Название фазы
            current: Текущий элемент (1-based)
            total: Всего элементов
            message: Дополнительное сообщение
        """
        self._current_phase = phase
        self._current_item = current
        self._total_items = total

        progress = PhaseProgress(
            phase=phase,
            current_item=current,
            total_items=total,
            message=message or f"{phase}: {current}/{total}",
            percent=0.0  # Вычислится в __post_init__
        )

        if self.callback:
            try:
                self.callback(progress)
            except Exception:
                # Игнорируем ошибки в callback, чтобы не сломать основной поток
                pass

    def get_current(self) -> PhaseProgress | None:
        """
        Возвращает текущий прогресс.
        
        Returns:
            PhaseProgress или None если нет данных
        """
        if self._current_phase is None:
            return None

        return PhaseProgress(
            phase=self._current_phase,
            current_item=self._current_item,
            total_items=self._total_items,
            message=f"{self._current_phase}: {self._current_item}/{self._total_items}",
            percent=0.0
        )

