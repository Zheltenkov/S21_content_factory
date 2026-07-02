"""
Модуль rubric для оценки проектов по критериям.

Декомпозирован из монолитного файла rubric.py для улучшения поддерживаемости.
"""

# Импортируем из нового модульного scorer
from .scorer import RubricScorer

# TaskBlock теперь в utils
from .utils import TaskBlock
from .utils import readability_index as _readability_index

# Экспортируем функции из utils для использования в других модулях
from .utils import semantic_similarity as _semantic_similarity

# Экспортируем для обратной совместимости
__all__ = ["RubricScorer", "TaskBlock", "_semantic_similarity", "_readability_index"]

