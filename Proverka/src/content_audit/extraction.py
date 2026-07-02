"""Детерминированное извлечение ссылок, изображений, дат и технических сущностей."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse

from content_audit.domain import ContentUnit, EntityType, ExtractedEntity, TextLocation
from content_audit.text_utils import context_around, line_end_for_match, line_for_offset, quote_around


URL_RE = re.compile(r"https?://[^\s\])>\"']+", re.IGNORECASE)
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*]\((?P<target>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
VERSION_RE = re.compile(
    r"\b("
    r"(?:C|Java|Python|Node\.?js|Alpine|Ubuntu(?:\s+Server)?|POSIX|GCC|Bash|Docker|"
    r"Docker\s+Compose|PostgreSQL|MySQL|MongoDB|Redis|Django|React|Vue|Angular|Next\.?js|"
    r"FastAPI|Flask|Spring|Kotlin|Go|Rust|TypeScript|JavaScript|PHP|Ruby|PCRE2|GNU|BusyBox)"
    r"\s*(?:version|версии|версия)?\s*[vV]?\d+(?:\.\d+){0,3}"
    r"|POSIX\.1-\d{4}"
    r"|C\d{2}"
    r")\b",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"\b(?:19|20)\d{2}(?:[-./](?:0?[1-9]|1[0-2])(?:[-./](?:0?[1-9]|[12]\d|3[01]))?)?\b")
TECH_RE = re.compile(
    r"\b("
    r"Java|Python|C11|POSIX|Alpine|Ubuntu|GCC|Makefile|PCRE2|regex|Docker|GitLab|GitHub|"
    r"Rocket\.Chat|GigaChat|GNU|BusyBox|Bash|PostgreSQL|MySQL|MongoDB|Redis|Django|React|"
    r"Vue|Angular|Next\.?js|FastAPI|Flask|Spring|Kotlin|Go|Rust|TypeScript|JavaScript|PHP|Ruby"
    r")\b",
    re.IGNORECASE,
)


def extract_entities(unit: ContentUnit) -> list[ExtractedEntity]:
    """Извлекаем проверяемые сущности из всех текстовых файлов единицы."""

    entities: list[ExtractedEntity] = []
    for file in unit.files:
        entities.extend(_extract_by_regex(unit.unit_id, file.relative_path, file.text, URL_RE, EntityType.LINK))
        entities.extend(_extract_images(unit.unit_id, file.relative_path, file.text))
        entities.extend(_extract_by_regex(unit.unit_id, file.relative_path, file.text, VERSION_RE, EntityType.VERSION))
        entities.extend(_extract_by_regex(unit.unit_id, file.relative_path, file.text, DATE_RE, EntityType.DATE))
        entities.extend(_extract_by_regex(unit.unit_id, file.relative_path, file.text, TECH_RE, EntityType.TECHNOLOGY))
    return _deduplicate_entities(entities)


def _extract_by_regex(
    unit_id: str,
    relative_path: str,
    text: str,
    pattern: re.Pattern[str],
    entity_type: EntityType,
) -> list[ExtractedEntity]:
    """Общий извлекатель для регулярных выражений."""

    result: list[ExtractedEntity] = []
    for match in pattern.finditer(text):
        value = match.group(0).rstrip(".,;:*!?")
        if entity_type == EntityType.LINK and not urlparse(value).hostname:
            continue
        result.append(
            ExtractedEntity(
                entity_id=_entity_id(unit_id, relative_path, entity_type, value, match.start()),
                entity_type=entity_type,
                value=value,
                quote=quote_around(text, match.start(), match.end()),
                location=TextLocation(
                    file_path=relative_path,
                    line_start=line_for_offset(text, match.start()),
                    line_end=line_end_for_match(text, match.start(), match.end()),
                ),
                context=context_around(text, match.start(), match.end()),
            )
        )
    return result


def _extract_images(unit_id: str, relative_path: str, text: str) -> list[ExtractedEntity]:
    """Извлекаем ссылки на изображения из Markdown."""

    result: list[ExtractedEntity] = []
    for match in MARKDOWN_IMAGE_RE.finditer(text):
        value = match.group("target").strip()
        result.append(
            ExtractedEntity(
                entity_id=_entity_id(unit_id, relative_path, EntityType.IMAGE, value, match.start()),
                entity_type=EntityType.IMAGE,
                value=value,
                quote=quote_around(text, match.start(), match.end()),
                location=TextLocation(
                    file_path=relative_path,
                    line_start=line_for_offset(text, match.start()),
                    line_end=line_end_for_match(text, match.start(), match.end()),
                ),
                context=context_around(text, match.start(), match.end()),
            )
        )
    return result


def _deduplicate_entities(entities: list[ExtractedEntity]) -> list[ExtractedEntity]:
    """Удаляем дубли одного типа в одной строке."""

    seen: set[tuple[str, str, str, int | None]] = set()
    unique: list[ExtractedEntity] = []
    for entity in entities:
        key = (entity.entity_type.value, entity.value.lower(), entity.location.file_path, entity.location.line_start)
        if key in seen:
            continue
        seen.add(key)
        unique.append(entity)
    return unique


def _entity_id(unit_id: str, relative_path: str, entity_type: EntityType, value: str, offset: int) -> str:
    """Создаём стабильный идентификатор сущности."""

    raw = f"{unit_id}|{relative_path}|{entity_type.value}|{value}|{offset}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"ent_{digest}"
