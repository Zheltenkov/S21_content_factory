"""Проверки и валидация LaTeX-формул в Markdown."""

from __future__ import annotations

import re

BLOCK_DELIM_RE = re.compile(r"(?<!\\)\$\$")
BLOCK_FORMULA_RE = re.compile(r"(?<!\\)\$\$([\s\S]*?)(?<!\\)\$\$", re.MULTILINE)
PLACEHOLDER_RE = re.compile(r"FORMULA_(?:BLOCK|INLINE)_\d+")


def _validate_dollar_balance(text: str) -> tuple[bool, int]:
    """
    Проверяет баланс $$ в тексте с учётом экранирования.

    Returns:
        (is_balanced, total_count)
    """
    matches = list(BLOCK_DELIM_RE.finditer(text))
    total_count = len(matches)

    if total_count == 0:
        return True, 0

    # Нечётное количество — сразу ошибка
    if total_count % 2 != 0:
        return False, total_count

    # Дополнительно убеждаемся, что мы видим чётное число неэкранированных $$,
    # потому что регулярка может пропустить экранированные пары.
    unescaped = 0
    for m in matches:
        pos = m.start()
        if pos > 0 and text[pos - 1] == "\\":
            continue
        unescaped += 1

    return unescaped % 2 == 0, total_count


def collect_latex_issues(markdown: str) -> list[str]:
    """Возвращает список проблем, найденных в LaTeX-формулах Markdown."""
    if not markdown:
        return []

    issues: list[str] = []

    placeholders = PLACEHOLDER_RE.findall(markdown)
    if placeholders:
        issues.append(
            "Обнаружены незаменённые плейсхолдеры формул "
            f"({', '.join(sorted(set(placeholders))[:3])})."
        )

    # Баланс $$ (учитываем экранирование)
    is_balanced, total_delims = _validate_dollar_balance(markdown)
    matched_pairs = len(BLOCK_FORMULA_RE.findall(markdown)) * 2

    if (not is_balanced) or total_delims % 2 != 0 or matched_pairs != total_delims:
        issues.append(
            "Нечётное количество $$ или незакрытые блочные формулы. "
            "Проверьте последнее изменение LaTeX."
        )
        # При незакрытых формулах дальнейшая проверка тел не нужна
        if not is_balanced:
            return issues

    for idx, match in enumerate(BLOCK_FORMULA_RE.finditer(markdown), start=1):
        body = match.group(1).strip()
        if not body:
            issues.append(f"Формула #{idx} пуста между $$...$$.")
            continue

        # Проверяем вложенные $$ (неэкранированные)
        nested = [
            m for m in BLOCK_DELIM_RE.finditer(body)
            if not (m.start() > 0 and body[m.start() - 1] == "\\")
        ]
        if nested:
            preview = body[:40].replace("\n", " ")
            issues.append(
                f"Формула #{idx} содержит вложенные $$ внутри тела: «{preview}...»."
            )

    return issues


def build_latex_agent_hint(issues: list[str]) -> str:
    """Формирует подсказку для LLM-агента на основе списка проблем."""
    if not issues:
        return (
            "Проверь формулы: убедись, что каждая пара $$ ... $$ закрыта, "
            "а исходные выражения копируются без изменений."
        )

    hints: list[str] = []
    joined = " ".join(issues).lower()

    if "плейсхолдер" in joined:
        hints.append(
            "Не удаляй маркеры [[[BLOCK_N]]] и не изменяй содержимое защищённых формул."
        )
    if "нечёт" in joined or "незакры" in joined:
        hints.append("Следи, чтобы каждая формула имела парные $$ в начале и конце.")
    if "пуста" in joined:
        hints.append("Внутри $$ ... $$ должно быть выражение, оставь исходный текст формулы.")
    if "вложенные" in joined:
        hints.append("Не вставляй дополнительные $$ внутри формул, используй \\text{} или скобки.")

    if not hints:
        hints.append(
            "Скопируй формулы из оригинального README без изменений и проверь синтаксис LaTeX."
        )

    return " ".join(hints)

