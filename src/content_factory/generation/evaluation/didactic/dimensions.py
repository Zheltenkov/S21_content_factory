"""Дидактические дименшены для оценки контента жюри-моделью.

Набор конфигурируем: по умолчанию грузится из встроенного
``generation/config/didactic_dimensions.yaml``; методолог может подменить его,
задав путь к своему файлу через переменную окружения ``DIDACTIC_DIMENSIONS_PATH``.
Захардкоженный ``_DEFAULT_DIMENSIONS`` остаётся страховкой на случай отсутствия или
повреждения конфигурации, чтобы оценка никогда не падала из-за конфига.

Полный переход на «дименшены из версии УП» — отдельный шаг; здесь снят хардкод,
значения вынесены в конфиг и допускают внешнюю подмену без изменения кода.
"""

from __future__ import annotations

import os
from importlib import resources
from pathlib import Path
from typing import NamedTuple

import yaml

from content_factory.api.utils.logger import get_logger

logger = get_logger("evaluation.didactic.dimensions")

_CONFIG_PACKAGE = "content_factory.generation.config"
_CONFIG_RESOURCE = "didactic_dimensions.yaml"
_ENV_PATH = "DIDACTIC_DIMENSIONS_PATH"


class Dimension(NamedTuple):
    """Один дидактический дименшен: id, заголовок, вопрос для судьи."""

    id: str
    title: str
    question: str


# Страховочный набор-гипотеза из прототипа; используется только если конфиг недоступен.
_DEFAULT_DIMENSIONS: tuple[Dimension, ...] = (
    Dimension("coherence", "Связность", "Единый маршрут без разрывов, оборванных фраз и скачков?"),
    Dimension("scaffolding", "Scaffolding (теория готовит к практике)", "Теория главы 2 реально готовит к заданиям главы 3?"),
    Dimension("example_quality", "Качество примеров", "Примеры конкретны и раскрывают идею, а не заглушки?"),
    Dimension("cognitive_load", "Когнитивная нагрузка", "Нет повторов и перегруза, адекватная прогрессия?"),
    Dimension("school_tone", "Тон школы (p2p)", "Peer-тон: не директивно, решение не выдаётся?"),
    Dimension("naturalness", "Не-AI-водность", "Живой язык без шаблонных самоповторов?"),
)


def _parse_dimensions(raw: object) -> tuple[Dimension, ...]:
    """Разбирает YAML-содержимое в набор дименшенов; бросает при неверной структуре."""

    if not isinstance(raw, dict):
        raise ValueError("ожидался словарь верхнего уровня")
    items = raw.get("dimensions")
    if not isinstance(items, list) or not items:
        raise ValueError("нет непустого списка 'dimensions'")
    parsed: list[Dimension] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("элемент dimensions должен быть словарём")
        dimension_id = str(item.get("id") or "").strip()
        title = str(item.get("title") or "").strip()
        question = str(item.get("question") or "").strip()
        if not dimension_id or not title or not question:
            raise ValueError(f"неполный дименшен: {item!r}")
        parsed.append(Dimension(dimension_id, title, question))
    return tuple(parsed)


def load_dimensions(path: str | os.PathLike[str] | None = None) -> tuple[Dimension, ...]:
    """Грузит дименшены из конфига. Порядок источника: аргумент → env → встроенный YAML.

    Любая ошибка чтения/разбора логируется и откатывается к ``_DEFAULT_DIMENSIONS``,
    чтобы оценка никогда не падала из-за конфигурации.
    """

    override = path if path is not None else os.getenv(_ENV_PATH)
    try:
        if override:
            text = Path(override).read_text(encoding="utf-8")
        else:
            text = resources.files(_CONFIG_PACKAGE).joinpath(_CONFIG_RESOURCE).read_text(encoding="utf-8")
        return _parse_dimensions(yaml.safe_load(text))
    except FileNotFoundError:
        logger.warning("Файл дидактических дименшенов не найден (%s); используется встроенный набор.", override)
    except Exception as exc:  # noqa: BLE001 - любой сбой конфига не должен ронять оценку.
        logger.warning("Не удалось загрузить дидактические дименшены (%s): %s; используется встроенный набор.", override, exc)
    return _DEFAULT_DIMENSIONS


# Загружается один раз при импорте; потребители используют как прежде.
DIMENSIONS: tuple[Dimension, ...] = load_dimensions()
