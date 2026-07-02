from __future__ import annotations

import re

from content_audit.domain import Finding


_TECH_PREFIXES = (
    "Проверка README",
    "Актуальность технологии",
    "Проверка актуальности",
    "Проверка рынка",
)


def format_finding_fragment(finding: Finding, *, limit: int = 260) -> str:
    """Возвращает короткий исходный фрагмент, а не техническую «цитату любой ценой»."""

    quote = _clean_text(finding.quote or "")
    markdown_image = re.match(r"!\[[^\]]*]\(([^)]+)\)", quote)
    if markdown_image:
        return _trim(f"Изображение: {markdown_image.group(1)}", limit)
    markdown_link = re.match(r"\[[^\]]+]\(([^)]+)\)", quote)
    if markdown_link:
        return _trim(f"Ссылка: {markdown_link.group(1)}", limit)
    if _is_useful_quote(quote):
        return _trim(quote, limit)
    if finding.location:
        location = finding.location.file_path
        if finding.location.line_start:
            location = f"{location}:{finding.location.line_start}"
        return location
    return ""


def format_finding_explanation(finding: Finding) -> str:
    """Собирает русское обоснование с рекомендацией в одном поле."""

    found = _humanize_evidence(finding)
    action = _clean_text(finding.recommendation or "")
    if found and action:
        return f"Что найдено: {found}\nЧто сделать: {action}"
    if action:
        return f"Что сделать: {action}"
    if found:
        return f"Что найдено: {found}"
    return "Нужно проверить случай вручную: подробное основание не сформировано."


def format_finding_explanation_html(finding: Finding, esc) -> str:
    """HTML-версия обоснования: рекомендация остаётся снизу внутри той же ячейки."""

    found = _humanize_evidence(finding)
    action = _clean_text(finding.recommendation or "")
    parts: list[str] = []
    if found:
        parts.append(f'<div><span class="reason-label">Что найдено:</span> {esc(found)}</div>')
    if action:
        parts.append(f'<div class="reason-action"><span class="reason-label">Что сделать:</span> {esc(action)}</div>')
    if not parts:
        parts.append('<div>Нужно проверить случай вручную: подробное основание не сформировано.</div>')
    return "".join(parts)


def _humanize_evidence(finding: Finding) -> str:
    """Убирает служебные префиксы и делает основание похожим на нормальную фразу."""

    details = [_clean_evidence_detail(item.detail) for item in finding.evidence if _clean_text(item.detail)]
    if details:
        return _join_unique(details)
    quote = _clean_text(finding.quote or "")
    if quote:
        return f"Система нашла спорный фрагмент: «{_trim(quote, 180)}»."
    return ""


def _clean_evidence_detail(value: str) -> str:
    """Нормализует текст доказательства, который пришёл из проверяющего модуля."""

    text = _clean_text(value)
    for prefix in _TECH_PREFIXES:
        marker = f"{prefix}:"
        if text.startswith(marker):
            text = text[len(marker) :].strip()
    text = re.sub(r"\s+:\s+", ": ", text)
    text = _rewrite_common_machine_phrases(text)
    if text and text[-1] not in ".!?…":
        text += "."
    return text


def _rewrite_common_machine_phrases(text: str) -> str:
    """Переводит частые служебные формулировки в язык отчёта для методолога."""

    text = re.sub(
        r"Найдено 1 сущностей: ([^.]+)\.?",
        r"Найдена технология для проверки: \1.",
        text,
    )
    text = re.sub(
        r"Найдено (\d+) сущностей: ([^.]+)\.?",
        r"Найдены технологии для проверки: \2.",
        text,
    )
    text = re.sub(
        r"Сильных совпадений: (\d+) из (\d+); слабых совпадений: (\d+) из \d+; не сопоставлено: (\d+) из \d+\.?",
        r"С чек-листом уверенно сопоставлено \1 из \2 пунктов, частично сопоставлено \3, не сопоставлено \4.",
        text,
    )
    text = re.sub(
        r"Развёрнутых описаний: (\d+) из (\d+)\.?",
        r"Развёрнутые описания есть у \1 из \2 пунктов.",
        text,
    )
    text = text.replace("Недостаточно описаны:", "Недостаточно описаны пункты:")
    return text


def _join_unique(items: list[str]) -> str:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.casefold()
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return " ".join(result)


def _is_useful_quote(quote: str) -> bool:
    if not quote:
        return False
    lowered = quote.lower()
    if len(quote) < 8:
        return False
    if lowered.startswith(("http://", "https://")):
        return False
    return True


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _trim(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: max(0, limit - 1)].rstrip()}…"
