"""Кураторская проверка доступности сервисов и технологий из РФ."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


RULE_FILE_NAMES = (
    "regional_availability_ru.yml",
    "regional_availability_ru.yaml",
    "regional_availability_ru.json",
)
RULE_STATUSES = {"unavailable", "limited", "manual_review"}


@dataclass(frozen=True)
class RegionalAvailabilityRule:
    """Одно проверяемое правило региональной доступности."""

    pattern: str
    target: str
    status: str
    reason: str
    source: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class RegionalAvailabilityMatch:
    """Результат сопоставления правила с найденной сущностью."""

    value: str
    rule: RegionalAvailabilityRule


def load_regional_availability_rules(input_path: Path) -> list[RegionalAvailabilityRule]:
    """Загружает кураторскую базу доступности из корня проверяемого проекта."""

    rule_path = _find_rule_file(input_path)
    if rule_path is None:
        return []
    payload = _read_rule_payload(rule_path)
    raw_rules = payload.get("rules") if isinstance(payload, dict) else payload
    if not isinstance(raw_rules, list):
        return []

    rules: list[RegionalAvailabilityRule] = []
    for item in raw_rules:
        if not isinstance(item, dict):
            continue
        pattern = str(item.get("pattern") or "").strip()
        target = str(item.get("target") or "service").strip().lower()
        status = str(item.get("status") or "").strip().lower()
        reason = str(item.get("reason") or "").strip()
        if not pattern or status not in RULE_STATUSES or not reason:
            continue
        rules.append(
            RegionalAvailabilityRule(
                pattern=pattern,
                target=target,
                status=status,
                reason=reason,
                source=_optional_text(item.get("source")),
                updated_at=_optional_text(item.get("updated_at")),
            )
        )
    return rules


def match_regional_availability(value: str, rules: list[RegionalAvailabilityRule]) -> RegionalAvailabilityMatch | None:
    """Сопоставляет URL, домен, технологию или пакет с кураторской базой."""

    normalized_value = _normalize_value(value)
    if not normalized_value:
        return None
    for rule in rules:
        if _rule_matches(normalized_value, rule.pattern):
            return RegionalAvailabilityMatch(value=value, rule=rule)
    return None


def _find_rule_file(input_path: Path) -> Path | None:
    """Ищет файл правил рядом с корнем проверки или на уровень выше."""

    roots = [input_path]
    if input_path.is_file():
        roots = [input_path.parent]
    roots.append(input_path.parent)
    for root in roots:
        for name in RULE_FILE_NAMES:
            path = root / name
            if path.exists() and path.is_file():
                return path
    return None


def _read_rule_payload(path: Path) -> Any:
    """Читает YAML или JSON без привязки к конкретному формату базы."""

    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    return yaml.safe_load(text) or {}


def _rule_matches(normalized_value: str, pattern: str) -> bool:
    """Поддерживает точное совпадение, доменный суффикс и простую маску `*`."""

    normalized_pattern = _normalize_value(pattern)
    if not normalized_pattern:
        return False
    if "*" in normalized_pattern:
        regex = "^" + re.escape(normalized_pattern).replace("\\*", ".*") + "$"
        return bool(re.fullmatch(regex, normalized_value))
    if normalized_value == normalized_pattern:
        return True
    return normalized_value.endswith(f".{normalized_pattern}") or normalized_pattern in _tokenize(normalized_value)


def _normalize_value(value: str) -> str:
    """Приводит URL и текстовые сущности к общей форме для сопоставления."""

    raw = value.strip().lower()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme in {"http", "https"} and parsed.hostname:
        return parsed.hostname.removeprefix("www.")
    return re.sub(r"\s+", " ", raw).removeprefix("www.")


def _tokenize(value: str) -> set[str]:
    """Делит строку на устойчивые токены для технологий и пакетов."""

    return {token for token in re.split(r"[^a-zа-я0-9.+#-]+", value) if token}


def _optional_text(value: object) -> str | None:
    text = "" if value is None else str(value).strip()
    return text or None
