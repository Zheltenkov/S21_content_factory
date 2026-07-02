"""Pydantic модели для агентов улучшения контента."""

from typing import Literal

from pydantic import BaseModel, Field


class CodeExample(BaseModel):
    """Пример кода для встраивания в теорию или практику."""

    label: str = Field(description="Краткое название примера (например, 'Пример использования функции')")
    code: str = Field(description="Код на соответствующем языке программирования")
    language: str = Field(default="python", description="Язык программирования (python, javascript, bash, etc.)")
    explanation: str | None = Field(default=None, description="Краткое объяснение примера (1-2 предложения)")


class CodeTask(BaseModel):
    """Задание на программирование для практики."""

    title: str = Field(description="Название задания")
    difficulty: Literal["beginner", "intermediate", "advanced"] = Field(description="Уровень сложности")
    code_stub: str = Field(description="Заготовка кода (шаблон с TODO комментариями)")
    language: str = Field(default="python", description="Язык программирования")
    hint: str | None = Field(default=None, description="Подсказка для студента")


class CodeGenerationResult(BaseModel):
    """Результат генерации примеров кода и заданий."""

    examples: list[CodeExample] = Field(default_factory=list, description="Примеры кода для теории")
    tasks: list[CodeTask] = Field(default_factory=list, description="Задания на программирование для практики")


class FormulaItem(BaseModel):
    """Математическая формула в LaTeX."""

    label: str = Field(description="Название формулы (например, 'Формула расчета метрики')")
    latex: str = Field(description="Формула в LaTeX формате (без $$, только содержимое)")
    parameters: list[dict[str, str]] = Field(default_factory=list, description="Список параметров: [{'symbol': 'E', 'description': 'энергия'}]")


class TableItem(BaseModel):
    """Таблица в Markdown формате."""

    label: str = Field(description="Название таблицы (например, 'Сравнение подходов')")
    md_table: str = Field(description="Таблица в Markdown формате (полный блок с заголовками)")
    description: str | None = Field(default=None, description="Краткое описание таблицы")


class VisualItem(BaseModel):
    """Визуализация (Mermaid диаграмма)."""

    label: str = Field(description="Название диаграммы")
    mermaid: str = Field(description="Код Mermaid диаграммы")
    description: str | None = Field(default=None, description="Краткое описание диаграммы")


class FormulaTableResult(BaseModel):
    """Результат анализа необходимости формул и таблиц."""

    needs_formulas: bool = Field(description="Нужны ли формулы для этой темы")
    needs_tables: bool = Field(description="Нужны ли таблицы для этой темы")
    needs_visuals: bool = Field(description="Нужны ли визуализации (Mermaid) для этой темы")
    formulas: list[FormulaItem] = Field(default_factory=list, description="Сгенерированные формулы")
    tables: list[TableItem] = Field(default_factory=list, description="Сгенерированные таблицы")
    visuals: list[VisualItem] = Field(default_factory=list, description="Сгенерированные визуализации")
    reasoning: str = Field(description="Обоснование решения (почему нужны/не нужны формулы/таблицы)")
    # Новые поля для работы с планом
    importance_formulas: str | None = Field(default=None, description="Уровень важности формул: must/nice_to_have/no")
    importance_tables: str | None = Field(default=None, description="Уровень важности таблиц: must/nice_to_have/no")
    importance_visuals: str | None = Field(default=None, description="Уровень важности визуализаций: must/nice_to_have/no")


class EnhancementDecision(BaseModel):
    """Решение менеджера о том, какие улучшения применять."""

    use_code_examples: bool = Field(description="Использовать ли примеры кода")
    use_formulas_tables: bool = Field(description="Использовать ли формулы и таблицы")
    reasoning: str = Field(description="Обоснование решения")


class GenerationResponse(BaseModel):
    """Валидируемая модель ответа генерации формул, таблиц и визуализаций."""

    formulas: list[FormulaItem] = Field(default_factory=list, description="Список формул")
    tables: list[TableItem] = Field(default_factory=list, description="Список таблиц")
    visuals: list[VisualItem] = Field(default_factory=list, description="Список визуализаций")
