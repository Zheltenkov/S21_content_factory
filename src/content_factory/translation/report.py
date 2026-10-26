"""Отчёт о переводе архива — файл ``ПЕРЕВОД-ОТЧЁТ.md`` внутри результата.

Отчёт нужен потому, что перевод архива частично успешен почти всегда: один файл переведён,
другой пропущен как уже английский, третий упал, а четвёртый обработчик вернул нетронутым,
потому что перевод сломал бы код. Без отчёта всё это выглядит одинаково — архив скачался.

Поэтому строка про успех и строка про отказ в нём одинаково подробны, а итог считает все четыре
исхода отдельно: «переведено 18 из 19» без разбора остатка не говорит ничего.
"""

from __future__ import annotations

from dataclasses import dataclass

TRANSLATED = "переведён"
SKIPPED = "пропущен"
FAILED = "ошибка"
UNCHANGED = "без изменений"

_SUMMARY_LABELS = {
    TRANSLATED: "переведено файлов",
    SKIPPED: "пропущено",
    FAILED: "с ошибкой",
    UNCHANGED: "без изменений",
}


@dataclass(frozen=True)
class FileOutcome:
    """Что случилось с одним файлом архива."""

    path: str
    status: str
    units: int
    detail: str = ""


def build_report(outcomes: list[FileOutcome], *, target_language: str) -> str:
    """Собирает отчёт в Markdown: итог, затем по строке на каждый файл."""
    lines = [
        "# Отчёт о переводе",
        "",
        f"Язык перевода: {target_language}.",
        "",
        "## Итог",
        "",
    ]
    lines.extend(
        f"- {label}: {sum(1 for outcome in outcomes if outcome.status == status)}"
        for status, label in _SUMMARY_LABELS.items()
    )

    for status, label in _SUMMARY_LABELS.items():
        chosen = [outcome for outcome in outcomes if outcome.status == status]
        if not chosen:
            continue
        lines.extend(["", f"## {label.capitalize()}", ""])
        lines.extend(_line(outcome) for outcome in chosen)

    lines.append("")
    return "\n".join(lines)


def _line(outcome: FileOutcome) -> str:
    parts = [f"- `{outcome.path}`"]
    if outcome.units:
        parts.append(f"единиц: {outcome.units}")
    if outcome.detail:
        parts.append(outcome.detail)
    return " — ".join(parts)
