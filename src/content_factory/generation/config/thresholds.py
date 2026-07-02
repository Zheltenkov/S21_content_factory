"""Пороговые значения для валидации и автокоррекции."""

THRESHOLDS = {
    "annotation_chars": (220, 520),  # Короткий teaser: 2-4 предложения без раскрытия решения
    "intro_words": (70, 180),
    "instruction_words": (80, 250),  # Длина инструкции в словах
    "theory_parts": (3, 7),  # Изменено с (2, 5) на (3, 5) - минимум 3 части
    "theory_words_per_part": (110, 260),  # Не лекция, а компактный блок для README и перехода к практике
    "practice_tasks_range": (2, 8),  # 2-5 основных, до 8 всего (включая бонусные)
    "practice_tasks_recommend": (2, 5),  # Рекомендуется 2-5 основных задач
    "approach_words_max": 300,
    "approach_bullets_min": 2,  # Минимум пунктов в подходе
    "approach_bullets_max": 6,  # Максимум пунктов в подходе
    "coherence_sbert_threshold": 0.5,  # Порог для проверки когерентности между абзацами (0.5-0.7)
    "paragraph_min_length": 40,  # Минимальная длина абзаца для анализа
    "theory_practice_sbert_threshold": 0.40,  # Порог для проверки связи теории и практики (0.40-0.45)
    "theory_practice_overlap_threshold": 0.10,  # Порог пересечения терминов для fallback (10%)
}

# Настройки для CodeExampleAgent
CODE_EXAMPLE_CONFIG = {
    "enable_code_tasks_in_practice": True,  # Включить использование CodeTask в практике
    "enable_code_example_validation": True,  # Включить валидацию примеров кода
    "max_code_examples_per_part": 2,  # Максимум примеров кода на часть теории
    "max_code_tasks_per_practice": 3,  # Максимум заданий на программирование в практике
}

# Бюджет элементов улучшения (по умолчанию)
DEFAULT_ENHANCEMENT_BUDGET = {
    "formulas": {"min": 0, "max": 6},
    "tables": {"min": 0, "max": 2},
    "diagrams": {"min": 0, "max": 3},
    "code_examples": {"min": 0, "max": 3}
}

# Глобальные целевые показатели по умолчанию. Hard guarantees задаются content_type policy.
GLOBAL_ENHANCEMENT_TARGETS = {
    "formulas": 0,
    "diagrams": 0,
    "code_examples": 0  # Для не-программирования = 0, для программирования определяется отдельно
}

# === ПРОФИЛИ ПО ТИПУ КОНТЕНТА ===

# NO_CODE: PjM, BSA, UX, Cb (гуманитарные направления)
# Формулы и код ЗАПРЕЩЕНЫ, только таблицы и простые диаграммы
NO_CODE_ENHANCEMENT_BUDGET = {
    "formulas": {"min": 0, "max": 0},  # Запрещены
    "tables": {"min": 1, "max": 3},     # Разрешены
    "diagrams": {"min": 0, "max": 2},   # Только простые (mermaid flowchart, без кода)
    "code_examples": {"min": 0, "max": 0}  # Запрещены
}

NO_CODE_ENHANCEMENT_TARGETS = {
    "formulas": 0,      # Нет формул
    "diagrams": 0,      # Диаграммы опциональны
    "tables": 1,        # Хотя бы 1 таблица для сравнений
    "code_examples": 0  # Нет кода
}

# LOW_CODE: DS, DevOps, QA, Биоинформатика
# Формулы/диаграммы разрешены, но не навязываются каждому проекту.
LOW_CODE_ENHANCEMENT_BUDGET = {
    "formulas": {"min": 0, "max": 2},   # Максимум 1-2 простые
    "tables": {"min": 1, "max": 3},
    "diagrams": {"min": 0, "max": 3},
    "code_examples": {"min": 0, "max": 2}  # Максимум 1-2 коротких
}

LOW_CODE_ENHANCEMENT_TARGETS = {
    "formulas": 0,      # Формулы не обязательны
    "diagrams": 0,      # Диаграммы опциональны
    "tables": 1,        # 1 таблица обязательна
    "code_examples": 0  # Код не обязателен
}

# HARD_CODE: Разработчики (C, Java, Python backend и т.д.)
# Код и формулы разрешены и приветствуются
HARD_CODE_ENHANCEMENT_BUDGET = {
    "formulas": {"min": 1, "max": 4},
    "tables": {"min": 0, "max": 2},
    "diagrams": {"min": 1, "max": 3},
    "code_examples": {"min": 1, "max": 5}
}

HARD_CODE_ENHANCEMENT_TARGETS = {
    "formulas": 1,      # Минимум 1 формула
    "diagrams": 1,      # Минимум 1 диаграмма
    "code_examples": 2  # Минимум 2 примера кода
}


def get_enhancement_config_for_content_type(content_type: str) -> tuple:
    """
    Возвращает бюджет и цели для типа контента.
    
    Args:
        content_type: 'hard_code' | 'low_code' | 'no_code'
    
    Returns:
        (budget, targets)
    """
    if content_type == "no_code":
        return NO_CODE_ENHANCEMENT_BUDGET, NO_CODE_ENHANCEMENT_TARGETS
    elif content_type == "low_code":
        return LOW_CODE_ENHANCEMENT_BUDGET, LOW_CODE_ENHANCEMENT_TARGETS
    elif content_type == "hard_code":
        return HARD_CODE_ENHANCEMENT_BUDGET, HARD_CODE_ENHANCEMENT_TARGETS
    else:
        # По умолчанию - low_code (средний вариант)
        return LOW_CODE_ENHANCEMENT_BUDGET, LOW_CODE_ENHANCEMENT_TARGETS
