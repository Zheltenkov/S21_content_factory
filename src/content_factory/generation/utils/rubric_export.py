"""Утилиты для экспорта новой рубрики с плоской таблицей и детализацией."""

from typing import Any

import numpy as np

from ..models.criteria_models import CriteriaReport
from ..validators.rubric.policy import rubric_item_status


def convert_numpy_types(obj: Any) -> Any:
    """
    Рекурсивно конвертирует numpy типы в стандартные Python типы для JSON сериализации.
    
    Args:
        obj: Объект, который может содержать numpy типы
    
    Returns:
        Объект с конвертированными типами
    """
    if isinstance(obj, (np.integer, np.floating)):
        return obj.item()  # Конвертирует numpy scalar в Python тип
    elif isinstance(obj, np.ndarray):
        return obj.tolist()  # Конвертирует numpy array в список
    elif isinstance(obj, dict):
        return {key: convert_numpy_types(value) for key, value in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [convert_numpy_types(item) for item in obj]
    else:
        return obj


def criteria_to_json(report: CriteriaReport) -> dict[str, Any]:
    """
    Преобразует рубрику в JSON для UI.
    
    Args:
        report: Отчёт по рубрике
    
    Returns:
        Словарь с данными рубрики
    """
    result = {
        "total": report.total,
        "max_score": report.max_score,
        "summary": report.summary,
        "items": [
            {
                "status": rubric_item_status(item),
                "id": item.id,
                "title": item.title,
                "description": item.description,
                "check_method": item.check_method.value,
                "score": item.score,
                "comments": item.comments,
                "details": item.details,
                "parent_id": item.parent_id,
                "strictness": item.strictness.value if hasattr(item, 'strictness') else "hard",
                "blocking": rubric_item_status(item) == "failed",
            }
            for item in report.items
        ],
    }

    # Конвертируем numpy типы в стандартные Python типы для JSON сериализации
    return convert_numpy_types(result)


def criteria_to_markdown(report: CriteriaReport) -> str:
    """
    Преобразует рубрику в Markdown таблицу (плоская структура).
    
    Args:
        report: Отчёт по рубрике
    
    Returns:
        Markdown таблица
    """
    lines = [
        "| № | Критерий | Описание | Метод | Оценка | Комментарии |",
        "|---:|---|---|:---:|:---:|---|",
    ]

    for item in report.items:
        method_emoji = {
            "script": "🔧",
            "ai_agent": "🤖",
            "sbert": "🔍",
            "hybrid": "🔀",
        }.get(item.check_method.value, "❓")

        status = rubric_item_status(item)
        score_emoji = "⚠️" if status == "warning" else ("✅" if status == "passed" else "❌")
        comments = " • ".join(item.comments) if item.comments else "—"

        lines.append(
            f"| {item.id} | {item.title} | {item.description} | {method_emoji} | {score_emoji} | {comments} |"
        )

    lines.append("")
    lines.append(f"**Итог:** {report.total} / {report.max_score}")
    lines.append("")
    lines.append("**Сводка по разделам:**")
    for section, score in report.summary.items():
        lines.append(f"- Раздел {section}: {score} баллов")

    return "\n".join(lines)


def criteria_to_html_table(report: CriteriaReport, with_details: bool = True) -> str:
    """
    Преобразует рубрику в HTML таблицу с возможностью детализации при клике.
    
    Args:
        report: Отчёт по рубрике
        with_details: Включать ли детализацию при клике
    
    Returns:
        HTML таблица
    """
    html = [
        "<table class='criteria-table'>",
        "<thead>",
        "<tr>",
        "<th>№</th>",
        "<th>Критерий</th>",
        "<th>Описание</th>",
        "<th>Метод</th>",
        "<th>Оценка</th>",
        "<th>Комментарии</th>",
        "</tr>",
        "</thead>",
        "<tbody>",
    ]

    method_emoji = {
        "script": "🔧",
        "ai_agent": "🤖",
        "sbert": "🔍",
        "hybrid": "🔀",
    }

    for item in report.items:
        method_icon = method_emoji.get(item.check_method.value, "❓")
        status = rubric_item_status(item)
        score_icon = "⚠️" if status == "warning" else ("✅" if status == "passed" else "❌")
        score_class = "score-warning" if status == "warning" else ("score-pass" if status == "passed" else "score-fail")

        comments = " • ".join(item.comments) if item.comments else "—"

        # Если есть детали, добавляем атрибут data-details
        details_attr = ""
        if with_details and item.details:
            import json
            details_attr = f' data-details=\'{json.dumps(item.details)}\''

        row_class = "clickable" if item.details else ""

        html.append(f"<tr class='{row_class}'{details_attr}>")
        html.append(f"<td>{item.id}</td>")
        html.append(f"<td><strong>{item.title}</strong></td>")
        html.append(f"<td>{item.description}</td>")
        html.append(f"<td>{method_icon}</td>")
        html.append(f"<td class='{score_class}'>{score_icon}</td>")
        html.append(f"<td>{comments}</td>")
        html.append("</tr>")

    html.extend([
        "</tbody>",
        "</table>",
        f"<p><strong>Итог:</strong> {report.total} / {report.max_score}</p>",
    ])

    return "\n".join(html)

