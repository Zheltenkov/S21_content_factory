"""Профили генерации для разных типов программ."""


from pydantic import BaseModel, Field

from .enhancement_plan import EnhancementBudget, GlobalEnhancementTargets


class GenerationProfile(BaseModel):
    """Профиль генерации для определенного типа программы."""

    name: str = Field(description="Название профиля (например, 'programming', 'mathematics', 'management')")
    description: str = Field(description="Описание профиля")

    # Настройки теории
    theory_parts_range: tuple = Field(default=(3, 5), description="Диапазон количества частей теории (min, max)")

    # Бюджет элементов
    enhancement_budget: EnhancementBudget = Field(description="Бюджет элементов улучшения")

    # Глобальные цели
    global_targets: GlobalEnhancementTargets = Field(description="Глобальные целевые показатели")

    # Настройки для программирования
    require_code_for_programming_topics: bool = Field(default=True, description="Требовать код для программистских тем")

    # Настройки для визуализаций
    allow_visuals_for_simple_topics: bool = Field(default=False, description="Разрешить визуализации для простых тем")

    # Дополнительные настройки
    min_formulas_per_part: int = Field(default=0, description="Минимум формул на часть")
    max_formulas_per_part: int = Field(default=2, description="Максимум формул на часть")
    prefer_tables_over_diagrams: bool = Field(default=False, description="Предпочитать таблицы диаграммам")


# Предустановленные профили
PROGRAMMING_PROFILE = GenerationProfile(
    name="programming",
    description="Профиль для программистских проектов",
    theory_parts_range=(3, 5),
    enhancement_budget=EnhancementBudget(
        formulas={"min": 0, "max": 2},
        tables={"min": 0, "max": 2},
        diagrams={"min": 1, "max": 2},
        code_examples={"min": 1, "max": 3}
    ),
    global_targets=GlobalEnhancementTargets(
        formulas=1,
        diagrams=1,
        code_examples=1
    ),
    require_code_for_programming_topics=True,
    allow_visuals_for_simple_topics=True,
    min_formulas_per_part=0,
    max_formulas_per_part=1,
    prefer_tables_over_diagrams=False
)

MATHEMATICS_PROFILE = GenerationProfile(
    name="mathematics",
    description="Профиль для математических проектов",
    theory_parts_range=(3, 5),
    enhancement_budget=EnhancementBudget(
        formulas={"min": 2, "max": 5},
        tables={"min": 0, "max": 2},
        diagrams={"min": 1, "max": 2},
        code_examples={"min": 0, "max": 1}
    ),
    global_targets=GlobalEnhancementTargets(
        formulas=3,
        diagrams=1,
        code_examples=0
    ),
    require_code_for_programming_topics=False,
    allow_visuals_for_simple_topics=False,
    min_formulas_per_part=1,
    max_formulas_per_part=2,
    prefer_tables_over_diagrams=True
)

MANAGEMENT_PROFILE = GenerationProfile(
    name="management",
    description="Профиль для проектов по менеджменту",
    theory_parts_range=(3, 5),
    enhancement_budget=EnhancementBudget(
        formulas={"min": 0, "max": 2},
        tables={"min": 1, "max": 3},
        diagrams={"min": 1, "max": 3},
        code_examples={"min": 0, "max": 0}
    ),
    global_targets=GlobalEnhancementTargets(
        formulas=0,
        diagrams=1,
        code_examples=0
    ),
    require_code_for_programming_topics=False,
    allow_visuals_for_simple_topics=True,
    min_formulas_per_part=0,
    max_formulas_per_part=1,
    prefer_tables_over_diagrams=False
)

DEFAULT_PROFILE = GenerationProfile(
    name="default",
    description="Профиль по умолчанию",
    theory_parts_range=(3, 5),
    enhancement_budget=EnhancementBudget(
        formulas={"min": 1, "max": 4},
        tables={"min": 0, "max": 2},
        diagrams={"min": 1, "max": 3},
        code_examples={"min": 0, "max": 3}
    ),
    global_targets=GlobalEnhancementTargets(
        formulas=2,
        diagrams=1,
        code_examples=0
    ),
    require_code_for_programming_topics=True,
    allow_visuals_for_simple_topics=False,
    min_formulas_per_part=0,
    max_formulas_per_part=2,
    prefer_tables_over_diagrams=False
)


def get_profile_by_name(name: str) -> GenerationProfile:
    """Возвращает профиль по имени."""
    profiles = {
        "programming": PROGRAMMING_PROFILE,
        "mathematics": MATHEMATICS_PROFILE,
        "management": MANAGEMENT_PROFILE,
        "default": DEFAULT_PROFILE
    }
    return profiles.get(name.lower(), DEFAULT_PROFILE)

