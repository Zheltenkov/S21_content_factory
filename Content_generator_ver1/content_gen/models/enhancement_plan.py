"""Модели для глобального планирования улучшений контента."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ImportanceLevel(str, Enum):
    """Уровень важности элемента улучшения."""
    MUST = "must"  # Обязательно (hard guarantee)
    NICE_TO_HAVE = "nice_to_have"  # Желательно (target)
    NO = "no"  # Не нужно


class PartEnhancementPlan(BaseModel):
    """План улучшений для одной части теории."""

    part_index: int = Field(description="Индекс части (1-based)")
    topic: str = Field(description="Тема части")
    formulas: ImportanceLevel = Field(default=ImportanceLevel.NO, description="Нужны ли формулы")
    tables: ImportanceLevel = Field(default=ImportanceLevel.NO, description="Нужны ли таблицы")
    diagrams: ImportanceLevel = Field(default=ImportanceLevel.NO, description="Нужны ли диаграммы")
    code_examples: ImportanceLevel = Field(default=ImportanceLevel.NO, description="Нужны ли примеры кода")
    reasoning: str = Field(description="Обоснование плана для этой части")
    anchor_hints: dict[str, str] | None = Field(
        default=None,
        description="Подсказки для якорей вставки: {'formula': 'после определения метрики', 'diagram': 'после описания процесса'}"
    )


class EnhancementBudget(BaseModel):
    """Бюджет элементов улучшения на весь проект."""

    formulas: dict[str, int] = Field(
        default_factory=lambda: {"min": 0, "max": 4},
        description="Минимум и максимум формул"
    )
    tables: dict[str, int] = Field(
        default_factory=lambda: {"min": 0, "max": 2},
        description="Минимум и максимум таблиц"
    )
    diagrams: dict[str, int] = Field(
        default_factory=lambda: {"min": 0, "max": 3},
        description="Минимум и максимум диаграмм"
    )
    code_examples: dict[str, int] = Field(
        default_factory=lambda: {"min": 0, "max": 3},
        description="Минимум и максимум примеров кода"
    )


class GlobalEnhancementTargets(BaseModel):
    """Глобальные целевые показатели для всего README."""

    formulas: int = Field(default=0, description="Целевое количество формул")
    diagrams: int = Field(default=0, description="Целевое количество диаграмм")
    tables: int = Field(default=1, description="Целевое количество таблиц (для структурирования данных)")
    code_examples: int = Field(default=0, description="Целевое количество примеров кода (0 для не-программирования)")


class EnhancementPlan(BaseModel):
    """Глобальный план улучшений для всего README."""

    global_targets: GlobalEnhancementTargets = Field(description="Глобальные целевые показатели")
    budget: EnhancementBudget = Field(description="Бюджет элементов")
    per_part: dict[int, PartEnhancementPlan] = Field(
        default_factory=dict,
        description="План для каждой части (ключ - индекс части 1-based)"
    )
    is_programming_project: bool = Field(description="Является ли проект программистским")
    reasoning: str = Field(description="Обоснование глобального плана")
    fallback_traces: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Machine-readable fallback events that affected this plan"
    )


class EnhancementExecutionLog(BaseModel):
    """Лог выполнения улучшений для одной части."""

    part_index: int = Field(description="Индекс части")
    topic: str = Field(description="Тема части")
    plan: PartEnhancementPlan = Field(description="План для этой части")
    generated: dict[str, int] = Field(
        default_factory=dict,
        description="Сгенерировано элементов: {'formulas': 2, 'tables': 1, 'diagrams': 0, 'code_examples': 1}"
    )
    embedded_positions: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Позиции встраивания: {'formulas': ['after определение метрики'], 'tables': ['before Пример']}"
    )
    errors: list[str] = Field(
        default_factory=list,
        description="Ошибки при генерации/встраивании"
    )


class QualityGateResult(BaseModel):
    """Результат проверки качества (quality gate)."""

    passed: bool = Field(description="Прошла ли проверка")
    violations: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Нарушения: [{'type': 'hard_guarantee', 'element': 'diagrams', 'expected': 1, 'actual': 0, 'part': 2, 'reason': '...'}]"
    )
    warnings: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Предупреждения: [{'type': 'target_missed', 'element': 'formulas', 'expected': 2, 'actual': 1, 'part': 4}]"
    )
    grade: float | None = Field(
        default=None,
        description="Оценка качества (0.0-1.0), если есть нарушения - понижается"
    )
