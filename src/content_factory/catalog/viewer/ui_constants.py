"""Shared catalog-viewer UI constants.

These values are transport-independent: native FastAPI routers and the legacy
viewer compatibility facade both need the same templates, static assets, summary
artifact path, and option lists.
"""

from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
DEFAULT_DB = BASE_DIR.parent / "artifacts" / "skills_catalog.sqlite"
DEFAULT_SUMMARY = BASE_DIR.parent / "artifacts" / "catalog_summary.json"

ARTIFACT_FAMILY_OPTIONS = [
    ("analysis", "Аналитический вывод"),
    ("document", "Комплект документов"),
    ("configuration", "Рабочая настройка"),
    ("design", "Проектное решение"),
    ("production", "Созданный продуктовый результат"),
    ("practice", "Практический результат"),
]
ARTIFACT_SCOPE_TYPE_OPTIONS = [
    ("coverage_area", "Область покрытия"),
    ("skill_group", "Группа навыков"),
    ("taxonomy_node", "Узел таксономии"),
    ("any", "Любая область"),
]
INTAKE_PROGRESS_STEPS = [
    {"code": "queued", "label": "Очередь"},
    {"code": "decompose", "label": "Декомпозиция"},
    {"code": "draft", "label": "Черновик"},
    {"code": "normalize", "label": "Нормализация"},
    {"code": "resolve", "label": "Сопоставление"},
    {"code": "search", "label": "Поиск"},
    {"code": "council", "label": "Жюри"},
    {"code": "persist", "label": "Запись"},
    {"code": "ready_for_review", "label": "Проверка"},
    {"code": "completed", "label": "Готово"},
]
