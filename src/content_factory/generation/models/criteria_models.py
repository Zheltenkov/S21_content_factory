"""Модели для новых критериев оценки контента."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class CheckMethod(str, Enum):
    """Метод проверки критерия."""
    SCRIPT = "script"  # Скриптовая проверка (regex, подсчеты)
    AI_AGENT = "ai_agent"  # ИИ-агент (LLM)
    SBERT = "sbert"  # Семантическая проверка (SBERT/embeddings)
    HYBRID = "hybrid"  # Комбинация скрипта и ИИ


class StrictnessLevel(str, Enum):
    """Уровень строгости критерия."""
    HARD = "hard"  # Обязательный критерий, блокирует прохождение
    SOFT = "soft"  # Рекомендация, не блокирует прохождение


class CriteriaItem(BaseModel):
    """Элемент критерия оценки."""

    id: str = Field(description="Идентификатор критерия (например, '1.1', '2.1.1')")
    title: str = Field(description="Название критерия")
    description: str = Field(description="Описание критерия")
    check_method: CheckMethod = Field(description="Метод проверки")
    score: int = Field(default=0, description="Оценка: 0 или 1")
    comments: list[str] = Field(default_factory=list, description="Комментарии по результату проверки")
    details: dict[str, Any] | None = Field(default=None, description="Детальная информация для отображения при клике")
    parent_id: str | None = Field(default=None, description="ID родительского критерия (для группировки)")
    strictness: StrictnessLevel = Field(default=StrictnessLevel.HARD, description="Уровень строгости: HARD (блокер) или SOFT (рекомендация)")


class CriteriaReport(BaseModel):
    """Отчет по всем критериям."""

    items: list[CriteriaItem] = Field(description="Список всех критериев")
    total: int = Field(description="Общее количество баллов")
    max_score: int = Field(description="Максимально возможный балл")
    summary: dict[str, int] = Field(
        default_factory=dict,
        description="Сводка по разделам: {'1': 5, '2': 12, ...}"
    )

