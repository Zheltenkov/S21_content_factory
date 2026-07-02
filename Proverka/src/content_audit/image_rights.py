"""Контракты проверки прав на изображения."""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urldefrag, urlparse

from content_audit.domain import ContentUnit, EntityType, ExtractedEntity, TextLocation
from content_audit.rights import (
    DECORATIVE_HINTS,
    DECORATIVE_MAX_SIDE,
    RightsSignal,
    has_attribution_near,
    read_image_provenance,
)
from content_audit.text_utils import normalize_for_match


@dataclass(frozen=True)
class ImageRightsAssessment:
    """Результат локальной оценки прав на одно изображение."""

    status: str
    detail: str
    entity: ExtractedEntity
    local_path: Path | None = None


def image_rights_signals(unit: ContentUnit, entities: list[ExtractedEntity]) -> list[RightsSignal]:
    """Строит сигналы прав для значимых изображений."""

    signals: list[RightsSignal] = []
    seen_resources: set[str] = set()
    for entity in _image_entities(entities):
        resource_key = normalize_for_match(entity.value)
        if resource_key in seen_resources:
            continue
        assessment = assess_image_rights(unit, entity)
        if assessment.status not in {"missing_local_metadata", "missing_external_attribution"}:
            continue
        signals.append(
            RightsSignal(
                kind="image_provenance",
                risk="no_source",
                deterministic=True,
                title="Изображение без подтверждённых прав",
                detail=assessment.detail,
                recommendation="Добавить источник, автора и лицензию изображения или подтвердить права вручную.",
                quote=entity.quote,
                location=entity.location,
                confidence=0.65,
            )
        )
        seen_resources.add(resource_key)
    return signals


def image_evidence_queries(entities: list[ExtractedEntity]) -> list[dict[str, object]]:
    """Формирует запросы внешнего поиска происхождения изображений."""

    queries: list[dict[str, object]] = []
    for entity in _image_entities(entities):
        target, _fragment = urldefrag(entity.value)
        if urlparse(target).scheme in {"http", "https"} and not is_decorative_reference(entity.value, entity.quote):
            queries.append(
                {
                    "kind": "image_provenance",
                    "title": "Возможный источник изображения",
                    "text": f"Найди источник и лицензию изображения по ссылке или имени: {entity.value}",
                    "quote": entity.quote,
                    "location": entity.location,
                }
            )
    return queries


def assess_image_rights(unit: ContentUnit, entity: ExtractedEntity) -> ImageRightsAssessment:
    """Классифицирует одно изображение по подтверждённости прав."""

    if is_decorative_reference(entity.value, entity.quote):
        return ImageRightsAssessment("ignored_decorative", "Декоративное изображение не требует отдельного сигнала.", entity)

    source_text = _source_file_text(unit, entity.location)
    if has_attribution_near(source_text, entity.value):
        return ImageRightsAssessment("confirmed_inline", "Источник или лицензия указаны рядом с изображением.", entity)

    target_path = resolve_local_image(unit, entity)
    if target_path is not None:
        if not is_significant_image(entity, target_path):
            return ImageRightsAssessment("ignored_decorative", "Малое декоративное изображение.", entity, target_path)
        provenance = read_image_provenance(target_path)
        if provenance.author or provenance.copyright or provenance.license or provenance.has_c2pa:
            return ImageRightsAssessment("confirmed_metadata", "В файле есть локальные признаки автора/лицензии.", entity, target_path)
        return ImageRightsAssessment(
            "missing_local_metadata",
            f"У значимого изображения нет локальных метаданных об авторе/лицензии: {entity.value}",
            entity,
            target_path,
        )

    target, _fragment = urldefrag(entity.value)
    if urlparse(target).scheme in {"http", "https"}:
        return ImageRightsAssessment(
            "missing_external_attribution",
            f"Внешнее изображение указано без явного источника/лицензии рядом со ссылкой: {entity.value}",
            entity,
        )
    return ImageRightsAssessment("unsupported_reference", "Локальное изображение не найдено или ссылка не поддержана.", entity)


def resolve_local_image(unit: ContentUnit, entity: ExtractedEntity) -> Path | None:
    """Разрешает относительный путь изображения внутри единицы контента."""

    target, _fragment = urldefrag(entity.value)
    if not target or urlparse(target).scheme in {"http", "https"}:
        return None
    source_file = unit.root_path / entity.location.file_path
    target_path = (source_file.parent / target).resolve()
    if target_path.exists() and _is_inside(target_path, unit.root_path):
        return target_path
    return None


def is_significant_image(entity: ExtractedEntity, path: Path) -> bool:
    """Отделяет содержательные изображения от мелкой декоративной графики."""

    dimensions = read_image_dimensions(path)
    if dimensions is None:
        return True
    width, height = dimensions
    if width < DECORATIVE_MAX_SIDE and height < DECORATIVE_MAX_SIDE:
        return False
    return not is_decorative_image(entity.value, entity.quote, width, height)


def is_decorative_reference(value: str, quote: str) -> bool:
    """Проверяет декоративные маркеры по имени файла и подписи."""

    marker_text = f"{value} {quote}".lower()
    return any(hint in marker_text for hint in DECORATIVE_HINTS)


def read_image_dimensions(path: Path) -> tuple[int, int] | None:
    """Читает размеры PNG/JPEG без внешних библиотек."""

    try:
        with path.open("rb") as handle:
            header = handle.read(24)
            if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
                width, height = struct.unpack(">II", header[16:24])
                return int(width), int(height)
            if header.startswith(b"\xff\xd8"):
                return _read_jpeg_dimensions(header + handle.read())
    except OSError:
        return None
    return None


def is_decorative_image(path: str, quote: str, width: int, height: int) -> bool:
    """Не ругает маленькие иконки, бейджи и логотипы как содержательные изображения."""

    marker_text = f"{path} {quote}".lower()
    decorative_markers = ("icon", "badge", "logo", "favicon", "avatar", "shield", "икон", "логотип")
    if any(marker in marker_text for marker in decorative_markers):
        return True
    return width <= 128 and height <= 128


def _image_entities(entities: list[ExtractedEntity]) -> list[ExtractedEntity]:
    return [entity for entity in entities if entity.entity_type == EntityType.IMAGE]


def _source_file_text(unit: ContentUnit, location: TextLocation) -> str:
    for file in unit.files:
        if file.relative_path == location.file_path:
            return file.text
    return ""


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _read_jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """Находит SOF-сегмент JPEG и достаёт ширину/высоту."""

    index = 2
    while index < len(data) - 9:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        block_length = int.from_bytes(data[index + 2 : index + 4], "big")
        if marker in {0xC0, 0xC1, 0xC2, 0xC3}:
            height = int.from_bytes(data[index + 5 : index + 7], "big")
            width = int.from_bytes(data[index + 7 : index + 9], "big")
            return width, height
        index += 2 + block_length
    return None
