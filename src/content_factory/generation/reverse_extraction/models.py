"""Pydantic модели для обратного извлечения данных."""

from typing import Any

from pydantic import BaseModel, Field


class NormalizedReadme(BaseModel):
    """Нормализованный README с извлеченной структурой."""

    raw_text: str = Field(description="Очищенный текст README")
    structure: dict[str, Any] = Field(
        default_factory=dict,
        description="Структура документа: заголовки, списки, секции"
    )
    chapters: dict[int, str] = Field(
        default_factory=dict,
        description="Содержимое глав по номерам (1, 2, 3...)"
    )


class PartialProjectSeed(BaseModel):
    """Частично заполненный ProjectSeed из извлеченных данных."""

    title_seed: str | None = Field(
        default=None,
        description="Название проекта из первого h1/h2"
    )
    project_description: str | None = Field(
        default=None,
        description="Краткое описание проекта из введения"
    )
    learning_outcomes: list[str] = Field(
        default_factory=list,
        description="Образовательные результаты (переформулированные если неявно)"
    )
    required_tools: list[str] = Field(
        default_factory=list,
        description="Обязательные инструменты и технологии"
    )
    skills: list[str] = Field(
        default_factory=list,
        description="Навыки, которые развивает проект"
    )
    tasks_count: int | None = Field(
        default=None,
        ge=0,
        description="Количество практических задач"
    )
    theory_parts: list[str] = Field(
        default_factory=list,
        description="Части теории для контекста"
    )
    include_formulas: bool | None = Field(
        default=None,
        description="Разрешить формулы в теории"
    )
    include_tables: bool | None = Field(
        default=None,
        description="Разрешить таблицы в теории"
    )
    include_diagrams: bool | None = Field(
        default=None,
        description="Разрешить диаграммы в теории"
    )
    sjm: str | None = Field(
        default=None,
        description="Сторителлинг/кейс (из введения или блока про контекст/ситуацию)"
    )


class ClassificationResult(BaseModel):
    """Результат классификации метаданных проекта."""

    language: str = Field(description="Язык проекта (ru, en, kg, uz)")
    thematic_block: str | None = Field(
        default=None,
        description="Кодовое обозначение тематического блока (Cb, PjM, QA, BSA, DO) или None если не определен"
    )
    thematic_block_suggested: str | None = Field(
        default=None,
        description="Предложенное кодовое обозначение нового блока (если не найден существующий)"
    )
    thematic_block_name: str | None = Field(
        default=None,
        description="Название тематического блока (если предложен новый)"
    )
    audience_level: str | None = Field(
        default=None,
        description="Уровень аудитории (Начальный, Продвинутый, base, advanced)"
    )
    project_type: str | None = Field(
        default=None,
        description="Тип проекта (individual, group)"
    )


class ValidationResult(BaseModel):
    """Результат валидации данных перед записью в Excel."""

    is_valid: bool = Field(description="Прошла ли валидация")
    mapping: dict[str, Any] = Field(
        description="Исправленный mapping для Excel"
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Предупреждения (некритичные проблемы)"
    )
    errors: list[str] = Field(
        default_factory=list,
        description="Ошибки (критичные проблемы)"
    )

