"""Дидактические дименшены (стартовый набор-гипотеза из прототипа).

TODO: в проде список должен приходить из curriculum/методолога, а не быть зашитым.
"""

from __future__ import annotations

from typing import NamedTuple


class Dimension(NamedTuple):
    """Один дидактический дименшен: id, заголовок, вопрос для судьи."""

    id: str
    title: str
    question: str


DIMENSIONS: tuple[Dimension, ...] = (
    Dimension(
        "coherence",
        "Связность",
        "Единый маршрут без разрывов, оборванных фраз и скачков?",
    ),
    Dimension(
        "scaffolding",
        "Scaffolding (теория готовит к практике)",
        "Теория главы 2 реально готовит к заданиям главы 3?",
    ),
    Dimension(
        "example_quality",
        "Качество примеров",
        "Примеры конкретны и раскрывают идею, а не заглушки?",
    ),
    Dimension(
        "cognitive_load",
        "Когнитивная нагрузка",
        "Нет повторов и перегруза, адекватная прогрессия?",
    ),
    Dimension(
        "school_tone",
        "Тон школы (p2p)",
        "Peer-тон: не директивно, решение не выдаётся?",
    ),
    Dimension(
        "naturalness",
        "Не-AI-водность",
        "Живой язык без шаблонных самоповторов?",
    ),
)
