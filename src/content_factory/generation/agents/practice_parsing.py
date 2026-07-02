"""Markdown parsing helpers for PracticeAgent outputs."""

from __future__ import annotations

import re


def extract_inline_from_public(text: str, label: str) -> str:
    """Extract `Label: ...` fragments from canonical public practice blocks."""
    if not text:
        return ""
    match = re.search(
        rf"(?:^|\n)\s*{re.escape(label)}:\s*(.+?)(?=\n\s*(?:Ситуация|Исходные данные|Цель|Подход):|\Z)",
        text,
        flags=re.S | re.I,
    )
    return match.group(1).strip() if match else ""


def extract_markdown_label(block: str, label: str) -> str:
    """Extract a bold markdown label body from one generated task block."""
    pattern = rf"\*\*{label}:?\*\*\s*(.+?)(?=\n\*\*|\Z)"
    match = re.search(pattern, block or "", flags=re.S)
    return match.group(1).strip() if match else ""


def extract_any_markdown_label(block: str, labels: list[str]) -> str:
    """Extract the first non-empty bold label body from a list of possible labels."""
    for label in labels:
        value = extract_markdown_label(block, label)
        if value:
            return value
    return ""


def extract_public_practice_fields(block: str) -> dict[str, str]:
    """Extract public/canonical fields from a generated practice task block."""
    public_action = extract_markdown_label(block, "Что нужно сделать")
    public_result = extract_markdown_label(block, "Что должно получиться")
    return {
        "public_action": public_action,
        "public_result": public_result,
        "situation": extract_markdown_label(block, "Ситуация")
        or extract_inline_from_public(public_action, "Ситуация"),
        "constraints_or_risk": extract_any_markdown_label(
            block,
            ["Ограничение / риск", "Ограничение/риск", "Ограничение", "Риск"],
        ),
        "input_data": extract_markdown_label(block, "Входные данные")
        or extract_inline_from_public(public_action, "Исходные данные"),
        "goal": extract_markdown_label(block, "Цель")
        or extract_inline_from_public(public_action, "Цель"),
        "approach": extract_markdown_label(block, "Подход")
        or extract_inline_from_public(public_action, "Подход")
        or public_action,
        "result": extract_markdown_label(block, "Ожидаемый результат") or public_result,
    }


def parse_p2p_criteria(block: str) -> list[str]:
    """Parse checklist criteria from generated task blocks."""
    criteria: list[str] = []
    criteria_match = re.search(
        r"\*\*(?:Что должно получиться|Критерии проверки.*?):?\*\*\s*\n(.*?)(?=\n\*\*|\n###|\n---|\Z)",
        block,
        flags=re.S | re.I,
    )

    if criteria_match:
        criteria_text = criteria_match.group(1)
        for line in criteria_text.split("\n"):
            line = line.strip()
            line = re.sub(r"^[-*]\s*\[[ x]?\]\s*", "", line)
            line = re.sub(r"^[-*]\s*", "", line)
            if line and len(line) > 5:
                criteria.append(line)

    return criteria[:7]


def parse_approach_bullets(approach: str) -> list[str]:
    """Parse markdown bullets while preserving multiline code fragments."""
    if not approach:
        return []

    lines = approach.splitlines()
    bullets: list[str] = []
    current: list[str] = []
    in_code = False
    fence = None

    def flush() -> None:
        text = "\n".join(current).strip()
        if text:
            bullets.append(text)
        current.clear()

    for raw in lines:
        line = raw.rstrip()
        stripped = line.lstrip()

        if in_code:
            current.append(line)
            if stripped.startswith(fence or "```"):
                in_code = False
                fence = None
            continue

        if not stripped:
            if current:
                current.append("")
            continue

        bullet_match = re.match(r"^((?:-|\*|•)|\d+\.)\s+(.*)", stripped)
        if bullet_match:
            flush()
            current.append(bullet_match.group(2).strip())
            continue

        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code = True
            fence = stripped[:3]
            current.append(stripped)
            continue

        current.append(stripped)

    flush()
    return bullets
