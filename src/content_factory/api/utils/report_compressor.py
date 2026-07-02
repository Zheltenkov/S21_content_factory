"""Утилиты для сокращения JSON отчёта перед сохранением в БД."""

from __future__ import annotations

from typing import Any


def _shrink_similar_projects(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Оставляет только базовые поля для списка похожих проектов."""
    slim_projects: list[dict[str, Any]] = []
    for project in projects:
        if not isinstance(project, dict):
            continue
        slim_projects.append(
            {
                "code": project.get("code"),
                "code_name": project.get("code_name"),
                "title": project.get("title"),
                "order": project.get("order"),
            }
        )
    return slim_projects


def compress_report_json(report: dict[str, Any]) -> dict[str, Any]:
    """
    Удаляет тяжёлые поля из report.json перед сохранением:
    - assets (диаграммы и бинарные данные)
    - детализированные similar_projects (оставляем только основные поля)
    - сложные метрики context-analysis (оставляем только простые значения)
    """
    if not isinstance(report, dict):
        return {}

    compressed = dict(report)

    # 1. Убираем бинарные/assets данные
    compressed.pop("assets", None)

    # 2. Сокращаем блок context
    context = compressed.get("context")
    if isinstance(context, dict):
        context_copy = dict(context)
        projects = context_copy.get("similar_projects")
        if isinstance(projects, list):
            context_copy["similar_projects"] = _shrink_similar_projects(projects)
        compressed["context"] = context_copy

    # 3. Сокращаем context_analysis
    context_analysis = compressed.get("context_analysis")
    if isinstance(context_analysis, dict):
        context_analysis_copy = dict(context_analysis)

        projects = context_analysis_copy.get("similar_projects")
        if isinstance(projects, list):
            context_analysis_copy["similar_projects"] = _shrink_similar_projects(projects)

        metrics = context_analysis_copy.get("metrics")
        if isinstance(metrics, dict):
            context_analysis_copy["metrics"] = {
                key: value
                for key, value in metrics.items()
                if isinstance(value, (int, float, str, bool)) or value is None
            }

        compressed["context_analysis"] = context_analysis_copy

    return compressed
