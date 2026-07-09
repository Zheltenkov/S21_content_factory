"""Извлечение фактологических утверждений из учебных материалов.

Отбирает из README и материалов короткие, внешне проверяемые утверждения (даты,
версии, стандарты, факты о технологиях) и готовит контракты для фактчека. Отсекает
учебные требования, дистракторы и локальные спецификации проекта, которые нельзя
проверять внешним поиском. Вынесено из ``checks.py`` и импортирует только листовой
``checker_base`` (никогда ``checks``), что держит граф зависимостей ацикличным.
``checks`` реэкспортирует эти имена, поэтому потребители не меняются.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from content_factory.audit.checker_base import (
    TECH_KEYWORDS,
    _model_context_priority,
    _parse_optional_int,
)
from content_factory.audit.domain import ContentUnit, TextLocation
from content_factory.audit.text_utils import normalize_for_match

FACT_MARKER_RE = re.compile(
    r"\b("
    r"deprecated|latest|lts|release|standard|style|support|supported|"
    r"актуаль|устар|поддерж|стандарт|релиз|верси|используется|является|входит|доступ"
    r")\b",
    re.IGNORECASE,
)
FACT_DATE_RE = re.compile(r"\b(?:19|20)\d{2}(?:[-./](?:0?[1-9]|1[0-2])(?:[-./](?:0?[1-9]|[12]\d|3[01]))?)?\b")
INTERNAL_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(\s*#[^)]+\)")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
README_FACT_FILE_RE = re.compile(r"^readme(?:_rus)?\.md$", re.IGNORECASE)
README_TASK_SECTION_RE = re.compile(
    r"(?i)^\s*#{2,6}\s*(?:chapter\s+iv|chapter\s+v|description\s+of\s+tasks|task\s+\d+|exercise\s+\d+|"
    r"задач[а-я]*\s+\d+|упражнен[а-я]*\s+\d+)"
)
README_THEORY_SECTION_RE = re.compile(
    r"(?i)^\s*#{2,6}\s*(?:chapter\s+iii|theory|general\s+concepts|documentation|mapping|error\s+handling|"
    r"теори[яи]|основные\s+понятия|документаци[яи]|маппинг|обработка\s+ошибок)"
)
README_DEFINITION_LINE_RE = re.compile(r"^\s*(?:\*\*)?[A-ZА-ЯЁ][^:\n]{1,90}(?:\*\*)?\s*[:—-]\s+\S")
README_EXERCISE_OPTION_RE = re.compile(r"^\s*(?:\d+[).]|[A-ZА-ЯЁ][).])\s+")
README_FEEDBACK_LINE_RE = re.compile(r"(?i)(feedback|опрос|educational experience|оставить отзыв|leave your feedback)")
MARKDOWN_TABLE_ROW_RE = re.compile(r"^\s*\|.+\|\s*$")
REFERENCE_POINTER_RE = re.compile(
    r"(?i)\[(?:here|there|link|source|documentation|docs|тут|здесь|сюда|ссылка|источник|документация)\]"
    r"\s*\(\s*https?://[^)]+\)"
)
LOCAL_PROJECT_SPEC_RE = re.compile(
    r"(?i)("
    r"\b(?:program|project|application|solution|utility|script|service)\b.{0,100}"
    r"\b(?:built|compiled|implemented|located|placed|stored|run|tested|uses?|contains?)\b"
    r"|"
    r"\b(?:программа|проект|приложение|решение|утилита|скрипт|сервис)\b.{0,100}"
    r"\b(?:собирается|компилируется|реализуется|располагается|лежит|запускается|тестируется|использует|содержит)\b"
    r"|"
    r"\b(?:target|makefile target|turn-in|files to turn in|src/|tests?/|artifacts?)\b"
    r"|"
    r"\b(?:цель\s+сборки|таргет|файлы\s+для\s+сдачи|артефакт[а-я]*)\b"
    r")"
)
_IMPERATIVE_VERB_PATTERN = (
    r"(?:clone|install|define|store|create|implement|build|run|follow|write|download|configure|use|set|"
    r"скопир|установ|создай|создайте|реализу|запуст|добав|склонир|настрой)"
)
LEAD_IMPERATIVE_RE = re.compile(
    r"^(?:before|when|if|to|перед|когда|если|чтобы)\b[^,]*,\s*" + _IMPERATIVE_VERB_PATTERN + r"\b",
    re.IGNORECASE,
)
SECOND_PERSON_RE = re.compile(
    r"\byou (?:must|need|have to|should|can)\b|\byour (?:code|project|repository|program|solution)\b",
    re.IGNORECASE,
)
REQUIREMENT_CLAIM_MARKERS = (
    " must ",
    " should ",
    " need to ",
    " needs to ",
    " required ",
    " requirement ",
    " have to ",
    " we recommend ",
    " it is necessary to ",
    " recommended to ",
    "должен",
    "должна",
    "должны",
    "нужно",
    "необходимо",
    "требуется",
    "следует",
    "рекоменду",
    "обязательно",
)


def _extract_fact_claims(unit: ContentUnit, limit: int) -> list[dict[str, Any]]:
    """Достаём короткие фактологические утверждения, которые есть смысл проверять внешним поиском."""

    claims: list[dict[str, Any]] = []
    seen: set[str] = set()
    ordered_files = sorted(unit.files, key=lambda file: _model_context_priority(file.kind, file.relative_path))
    for file in ordered_files:
        if file.kind not in {"readme", "material", "text"}:
            continue
        in_task_section = False
        for line_number, line in enumerate(file.text.splitlines(), start=1):
            stripped = line.strip()
            if file.kind == "readme":
                if README_THEORY_SECTION_RE.search(stripped):
                    in_task_section = False
                elif README_TASK_SECTION_RE.search(stripped):
                    in_task_section = True
            for candidate in _split_claim_line(line):
                if _is_factcheck_noise_line(candidate, in_task_section):
                    continue
                claim = _clean_claim_text(candidate)
                key = normalize_for_match(claim)
                if key in seen or not _looks_like_fact_claim(claim):
                    continue
                seen.add(key)
                claims.append(
                    {
                        "claim": claim,
                        "context": line.strip()[:700],
                        "location": TextLocation(file_path=file.relative_path, line_start=line_number, line_end=line_number),
                    }
                )
                if len(claims) >= limit:
                    return claims
    return claims


def _split_claim_line(line: str) -> list[str]:
    """Разделяем строку на короткие утверждения без тяжёлого лингвистического разбора."""

    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", line) if part.strip()]


def _clean_claim_text(value: str) -> str:
    """Убираем Markdown-маркеры, которые не относятся к смыслу утверждения."""

    cleaned = re.sub(r"^\s*(?:#{1,6}|[-*]|\d+[.)])\s*", "", value.strip())
    cleaned = MARKDOWN_LINK_RE.sub(r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _looks_like_fact_claim(value: str) -> bool:
    """Отбираем только утверждения с датами, версиями, стандартами или признаками внешней проверяемости."""

    lowered = value.lower()
    if len(value) < 35 or len(value) > 520:
        return False
    if lowered.startswith(("http://", "https://", "![", "[")):
        return False
    if _is_factcheck_noise_line(value, in_task_section=False):
        return False
    if len(re.findall(r"\w+", value, flags=re.UNICODE)) < 5:
        return False
    return bool(FACT_DATE_RE.search(value) or FACT_MARKER_RE.search(value) or any(keyword in lowered for keyword in TECH_KEYWORDS))


def _is_factcheck_noise_line(value: str, in_task_section: bool) -> bool:
    """Отсекает строки, которые не являются внешне проверяемым фактом."""

    stripped = value.strip()
    if not stripped or in_task_section:
        return True
    if stripped.startswith("#") or MARKDOWN_TABLE_ROW_RE.match(stripped):
        return True
    if README_FEEDBACK_LINE_RE.search(stripped) or README_EXERCISE_OPTION_RE.match(stripped):
        return True
    if _is_markdown_navigation_claim(stripped) or _is_reference_pointer_claim(stripped):
        return True
    claim = _clean_claim_text(stripped)
    return _is_requirement_claim(claim) or _is_local_project_spec_claim(claim)


def _is_markdown_navigation_claim(value: str) -> bool:
    """Отсекаем строки оглавления и внутренние якоря, которые не являются фактами."""

    if not INTERNAL_MARKDOWN_LINK_RE.search(value):
        return False
    without_links = INTERNAL_MARKDOWN_LINK_RE.sub("", value)
    return len(re.findall(r"\w+", without_links, flags=re.UNICODE)) <= 3


def _is_reference_pointer_claim(value: str) -> bool:
    """Отсекает короткие указатели на внешнюю ссылку без самостоятельного утверждения."""

    if not REFERENCE_POINTER_RE.search(value):
        return False
    without_links = REFERENCE_POINTER_RE.sub("", value)
    return len(re.findall(r"\w+", without_links, flags=re.UNICODE)) <= 6


def _is_requirement_claim(value: str) -> bool:
    """Отсекаем требования курса: их нужно оценивать рубрикой, а не внешним фактчеком."""

    lowered = f" {value.lower()} "
    return (
        any(marker in lowered for marker in REQUIREMENT_CLAIM_MARKERS)
        or bool(LEAD_IMPERATIVE_RE.search(value))
        or bool(SECOND_PERSON_RE.search(value))
    )


def _is_local_project_spec_claim(value: str) -> bool:
    """Отсекает локальные требования проекта: их нельзя проверять внешним фактчеком."""

    if FACT_DATE_RE.search(value):
        return False
    return bool(LOCAL_PROJECT_SPEC_RE.search(value))


def _fact_check_prompt(claim: dict[str, Any]) -> str:
    """Формируем входной контракт фактологической проверки."""

    location = claim.get("location")
    payload = {
        "check_date": datetime.now(UTC).date().isoformat(),
        "claim": claim.get("claim"),
        "context": claim.get("context"),
        "file_path": location.file_path if isinstance(location, TextLocation) else None,
        "line_start": location.line_start if isinstance(location, TextLocation) else None,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _extract_readme_fact_batches(unit: ContentUnit, max_lines_per_batch: int, max_batches: int) -> list[dict[str, Any]]:
    """Готовит README.md и README_RUS.md к проверке с сохранением номеров строк."""

    batches: list[dict[str, Any]] = []
    for file in sorted(unit.files, key=lambda item: item.relative_path.lower()):
        if not _is_fact_readme_file(file.relative_path):
            continue
        candidates = _readme_fact_candidate_lines(file.text.splitlines())
        for start_index in range(0, len(candidates), max_lines_per_batch):
            chunk = candidates[start_index : start_index + max_lines_per_batch]
            numbered_text = "\n".join(f"{line_number}: {line}" for line_number, line in chunk)
            if not numbered_text.strip():
                continue
            allowed_lines = [line_number for line_number, _line in chunk]
            batches.append(
                {
                    "file_path": file.relative_path,
                    "line_start": min(allowed_lines),
                    "line_end": max(allowed_lines),
                    "text": numbered_text,
                    "allowed_lines": allowed_lines,
                }
            )
            if len(batches) >= max_batches:
                return batches
    return batches


def _readme_fact_candidate_lines(lines: list[str]) -> list[tuple[int, str]]:
    """Оставляет для README-фактчека только определения и внешне проверяемые утверждения."""

    candidates: list[tuple[int, str]] = []
    in_task_section = False
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if README_THEORY_SECTION_RE.search(stripped):
            in_task_section = False
        elif README_TASK_SECTION_RE.search(stripped):
            in_task_section = True
        if not _is_readme_fact_candidate_line(stripped, in_task_section):
            continue
        candidates.append((line_number, line))
    return candidates


def _is_readme_fact_candidate_line(line: str, in_task_section: bool) -> bool:
    """Отсекает учебные инструкции, дистракторы и локальные требования проекта."""

    if _is_factcheck_noise_line(line, in_task_section):
        return False
    claim = _clean_claim_text(line)
    if _looks_like_fact_claim(claim):
        return True
    return _looks_like_definition_line(claim)


def _looks_like_definition_line(value: str) -> bool:
    """Разрешает проверку терминологических определений из теоретических разделов."""

    if len(value) < 20 or len(value) > 520:
        return False
    if not README_DEFINITION_LINE_RE.search(value):
        return False
    lowered = value.lower()
    return bool(FACT_MARKER_RE.search(value) or any(keyword in lowered for keyword in TECH_KEYWORDS) or ":" in value)


def _is_allowed_readme_fact_item(item: dict[str, Any], batch: dict[str, Any]) -> bool:
    """Не принимает от модели строку, которой не было во входе специального фактчека."""

    file_path = str(item.get("file_path") or batch["file_path"])
    if Path(file_path).name.lower() != Path(str(batch["file_path"])).name.lower():
        return False
    allowed_lines = set(batch.get("allowed_lines") or [])
    if not allowed_lines:
        return True
    line_start = _parse_optional_int(item.get("line_start"))
    if line_start is None:
        return False
    return line_start in allowed_lines


def _is_fact_readme_file(relative_path: str) -> bool:
    """Ограничивает специальную фактологическую проверку двумя README-файлами."""

    return bool(README_FACT_FILE_RE.fullmatch(Path(relative_path).name))


def _readme_fact_check_prompt(batch: dict[str, Any]) -> str:
    """Формирует контракт проверки README-фрагмента."""

    payload = {
        "check_date": datetime.now(UTC).date().isoformat(),
        "file_path": batch["file_path"],
        "line_start": batch["line_start"],
        "line_end": batch["line_end"],
        "numbered_text": batch["text"],
        "scope": [
            "проверяемые определения",
            "даты и временные утверждения",
            "версии и поддержка технологий",
            "стеки технологий, библиотеки, стандарты и инструменты",
            "прочие утверждения о внешнем мире, которые можно подтвердить источниками",
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
