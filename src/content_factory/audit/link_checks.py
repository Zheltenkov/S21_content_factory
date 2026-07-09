"""Проверки ссылок, локальных путей и изображений.

``LinkChecker`` проверяет доступность внешних ссылок (с учётом сетевой политики),
``LocalLinkChecker`` — существование локальных путей, ``ImageQualityChecker`` —
качество и права на изображения. Вынесено из ``checks.py``; импортирует только
листовой ``checker_base`` + доменные типы + утилиты ссылок/изображений (никогда
``checks``). ``checks`` реэкспортирует классы, поэтому ``default_checkers`` и
тесты не меняются.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urldefrag, urlparse

from content_factory.audit.checker_base import BaseChecker, CheckContext, _finding
from content_factory.audit.domain import (
    ContentUnit,
    Criterion,
    EntityType,
    Evidence,
    ExtractedEntity,
    Finding,
    Severity,
    Verdict,
)
from content_factory.audit.image_rights import is_decorative_image, read_image_dimensions
from content_factory.audit.url_helpers import (
    _check_url,
    _is_inside,
    _is_redirect_chain_error,
    _is_transient_http_status,
    _redirect_smells_like_rot,
    _url_policy_error,
)


def _entities_of_type(entities: Iterable[ExtractedEntity], entity_type: EntityType) -> Iterable[ExtractedEntity]:
    """Фильтруем сущности по типу."""

    return (entity for entity in entities if entity.entity_type == entity_type)


class LinkChecker(BaseChecker):
    """Проверяет ссылки: локальные сразу, внешние при разрешённой сети."""

    name = "link_checker"

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        findings: list[Finding] = []
        for entity in _entities_of_type(entities, EntityType.LINK):
            parsed = urlparse(entity.value)
            if parsed.scheme not in {"http", "https"}:
                continue
            policy_error = _url_policy_error(entity.value)
            if policy_error is not None:
                findings.append(
                    _finding(
                        unit,
                        self.name,
                        Criterion.LINKS,
                        Severity.INFO,
                        Verdict.UNKNOWN,
                        0.65,
                        entity.quote,
                        entity.location,
                        [Evidence(title="Политика проверки ссылок", detail=policy_error, url=entity.value)],
                        "Проверить ссылку вручную.",
                        True,
                    )
                )
                continue
            if not context.settings.allow_network:
                findings.append(
                    _finding(
                        unit,
                        self.name,
                        Criterion.LINKS,
                        Severity.INFO,
                        Verdict.UNKNOWN,
                        0.5,
                        entity.quote,
                        entity.location,
                        [Evidence(title="Сеть отключена", detail=f"Ссылка не проверялась: {entity.value}", url=entity.value)],
                        "Запустить проверку с доступом к сети, чтобы подтвердить доступность ссылки.",
                        True,
                    )
                )
                continue

            status_code, final_url, error = _check_url(entity.value, context.settings.link_timeout_seconds)
            if error is not None:
                severity = Severity.MINOR if _is_redirect_chain_error(error) else Severity.INFO
                verdict = Verdict.WARNING if _is_redirect_chain_error(error) else Verdict.UNKNOWN
                findings.append(
                    _finding(
                        unit,
                        self.name,
                        Criterion.LINKS,
                        severity,
                        verdict,
                        0.65,
                        entity.quote,
                        entity.location,
                        [Evidence(title="Ошибка запроса", detail=error, url=entity.value)],
                        "Перепроверить ссылку: ошибка может быть временной, сетевой или связанной с перенаправлениями.",
                        True,
                    )
                )
            elif _is_transient_http_status(status_code):
                findings.append(
                    _finding(
                        unit,
                        self.name,
                        Criterion.LINKS,
                        Severity.INFO,
                        Verdict.UNKNOWN,
                        0.65,
                        entity.quote,
                        entity.location,
                        [Evidence(title="Временный HTTP-статус", detail=f"Получен статус {status_code}.", url=final_url or entity.value)],
                        "Повторить проверку позже: статус похож на временную недоступность или ограничение запросов.",
                        True,
                    )
                )
            elif status_code >= 400:
                severity = Severity.MAJOR
                findings.append(
                    _finding(
                        unit,
                        self.name,
                        Criterion.LINKS,
                        severity,
                        Verdict.FAIL,
                        0.9,
                        entity.quote,
                        entity.location,
                        [Evidence(title="HTTP-статус", detail=f"Получен статус {status_code}.", url=final_url or entity.value)],
                        "Заменить ссылку на актуальную или удалить зависимость от недоступного ресурса.",
                        True,
                    )
                )
            elif _redirect_smells_like_rot(entity.value, final_url):
                findings.append(
                    _finding(
                        unit,
                        self.name,
                        Criterion.LINKS,
                        Severity.MINOR,
                        Verdict.WARNING,
                        0.7,
                        entity.quote,
                        entity.location,
                        [Evidence(title="Подозрительный редирект", detail=f"Финальный адрес: {final_url}.", url=final_url or entity.value)],
                        "Проверить, ведёт ли ссылка на нужный материал, а не на главную страницу или другой домен.",
                        True,
                    )
                )
        return findings


class LocalLinkChecker(BaseChecker):
    """Проверяет локальные Markdown-ссылки на файлы и изображения."""

    name = "local_link_checker"

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        del context
        findings: list[Finding] = []
        for entity in [*list(_entities_of_type(entities, EntityType.IMAGE))]:
            target, _fragment = urldefrag(entity.value)
            parsed = urlparse(target)
            if parsed.scheme in {"http", "https"} or not target:
                continue
            source_file = unit.root_path / entity.location.file_path
            target_path = (source_file.parent / target).resolve()
            if not _is_inside(target_path, unit.root_path) or not target_path.exists():
                findings.append(
                    _finding(
                        unit,
                        self.name,
                        Criterion.LINKS,
                        Severity.MAJOR,
                        Verdict.FAIL,
                        0.95,
                        entity.quote,
                        entity.location,
                        [Evidence(title="Локальный файл", detail=f"Файл не найден: {entity.value}")],
                        "Исправить путь к локальному ресурсу или добавить отсутствующий файл.",
                        True,
                    )
                )
        return findings


class ImageQualityChecker(BaseChecker):
    """Проверяет размеры локальных изображений, на которые ссылается Markdown."""

    name = "image_quality_checker"

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        findings: list[Finding] = []
        for entity in _entities_of_type(entities, EntityType.IMAGE):
            target, _fragment = urldefrag(entity.value)
            parsed = urlparse(target)
            if parsed.scheme in {"http", "https"} or not target:
                continue
            source_file = unit.root_path / entity.location.file_path
            target_path = (source_file.parent / target).resolve()
            if not target_path.exists() or not _is_inside(target_path, unit.root_path):
                continue
            dimensions = read_image_dimensions(target_path)
            if dimensions is None:
                findings.append(
                    _finding(
                        unit,
                        self.name,
                        Criterion.IMAGE_QUALITY,
                        Severity.INFO,
                        Verdict.UNKNOWN,
                        0.45,
                        entity.quote,
                        entity.location,
                        [Evidence(title="Изображение", detail=f"Не удалось определить размер: {entity.value}")],
                        "Проверить изображение вручную или добавить поддержку его формата.",
                        True,
                    )
                )
                continue
            width, height = dimensions
            if width < context.settings.min_image_width or height < context.settings.min_image_height:
                if is_decorative_image(entity.value, entity.quote, width, height):
                    continue
                findings.append(
                    _finding(
                        unit,
                        self.name,
                        Criterion.IMAGE_QUALITY,
                        Severity.MINOR,
                        Verdict.WARNING,
                        0.85,
                        entity.quote,
                        entity.location,
                        [Evidence(title="Размер изображения", detail=f"{width}x{height}, минимум {context.settings.min_image_width}x{context.settings.min_image_height}.")],
                        "Заменить изображение на более качественное или подтвердить, что малый размер допустим.",
                        True,
                    )
                )
        return findings
