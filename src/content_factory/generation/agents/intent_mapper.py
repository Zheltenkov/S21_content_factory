"""
content_gen/agents/intent_mapper.py

Агент нормализации входных данных.

Преобразует сырые входные данные от методолога в валидированную структуру ProjectSeed.
Проверяет соответствие требованиям и генерирует предупреждения.
"""

from dataclasses import dataclass
from typing import Any

from ..config.thresholds import THRESHOLDS
from ..models.schemas import ProjectSeed


@dataclass
class IntentWarnings:
    """Предупреждения при нормализации входных данных."""

    messages: list[str]


class IntentMapper:
    """Нормализует и валидирует входные данные от методолога."""

    def map(self, raw: dict[str, Any]) -> tuple[ProjectSeed, IntentWarnings]:
        """
        Преобразует сырые данные в валидированный ProjectSeed.

        Args:
            raw: Словарь с входными данными

        Returns:
            Кортеж (ProjectSeed, IntentWarnings)

        Raises:
            ValueError: Если tasks_count вне допустимого диапазона или group_size не указан для группового проекта
        """
        # Обратная совместимость: если передан track, используем его как thematic_block
        if "track" in raw and "thematic_block" not in raw:
            raw["thematic_block"] = raw["track"]

        # Валидация group_size для групповых проектов
        if raw.get("project_type") == "group" and not raw.get("group_size"):
            raise ValueError("Для группового проекта необходимо указать group_size (количество человек в группе)")

        # Если проект индивидуальный, убираем group_size
        if raw.get("project_type") == "individual":
            raw.pop("group_size", None)

        seed = ProjectSeed(**raw)
        warns: list[str] = []

        if seed.tasks_count is not None:
            lo, hi = THRESHOLDS["practice_tasks_recommend"]
            if seed.tasks_count in (2, 10):
                warns.append(
                    f"tasks_count={seed.tasks_count}: допускается, "
                    f"но рекомендуемый диапазон {lo}–{hi} (поставлен warn)."
                )

            lo_all, hi_all = THRESHOLDS["practice_tasks_range"]
            if not (lo_all <= seed.tasks_count <= hi_all):
                raise ValueError(f"tasks_count должен быть в диапазоне {lo_all}–{hi_all}")

        return seed, IntentWarnings(warns)
