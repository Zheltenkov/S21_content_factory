"""Правила и типы для проверки прав и оригинальности.

Модуль не зависит от проверяющего конвейера. Он хранит контракты сигналов,
политику лицензий и лёгкие локальные адаптеры, которые позже можно заменить
на ScanCode, exiftool, C2PA и реестры лицензий зависимостей.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from content_audit.domain import ContentFile, Severity, TextLocation, Verdict


LICENSE_DENY = {"GPL-3.0-only", "GPL-3.0-or-later", "AGPL-3.0-only", "AGPL-3.0-or-later"}
LICENSE_REVIEW = {"CC-BY-NC-4.0", "CC-BY-NC-SA-4.0", "CC-BY-SA-4.0", "LicenseRef-Custom"}
MANIFEST_NAMES = {
    "requirements.txt",
    "pyproject.toml",
    "package.json",
    "pom.xml",
    "build.gradle",
    "go.mod",
    "cargo.toml",
}
DECORATIVE_HINTS = ("icon", "badge", "logo", "avatar", "divider", "emoji", "shields.io", "икон", "логотип")
DECORATIVE_MAX_SIDE = 200
DATASET_RE = re.compile(
    r"\b(kaggle\.com/datasets|huggingface\.co/datasets|archive\.ics\.uci\.edu|датасет|dataset)\b",
    re.IGNORECASE,
)
ATTRIBUTION_RE = re.compile(
    r"https?://|источник|лицензи|автор|copyright|license|adapted from|based on|courtesy of|©",
    re.IGNORECASE,
)


@dataclass
class CodeMatch:
    """Совпадение кода между единицами из корпусной стадии."""

    other_unit_id: str
    similarity: float
    attributed: bool


@dataclass
class LicenseScan:
    """Результат локального сканирования лицензий."""

    spdx: str | None = None
    copyrights: list[str] = field(default_factory=list)


@dataclass
class ImageProvenance:
    """Локальные признаки происхождения изображения."""

    author: str | None = None
    copyright: str | None = None
    license: str | None = None
    has_c2pa: bool = False
    is_ai_generated: bool | None = None


@dataclass
class RightsSignal:
    """Один сигнал по правам/оригинальности до финального вердикта."""

    kind: str
    risk: str
    deterministic: bool
    title: str
    detail: str
    recommendation: str
    quote: str | None = None
    location: TextLocation | None = None
    source: str | None = None
    url: str | None = None
    confidence: float = 0.5


RISK_LADDER: dict[str, tuple[Severity, Verdict, bool]] = {
    "violation": (Severity.CRITICAL, Verdict.FAIL, True),
    "unlicensed": (Severity.MAJOR, Verdict.FAIL, True),
    "no_source": (Severity.MINOR, Verdict.WARNING, True),
    "no_license_only": (Severity.INFO, Verdict.WARNING, False),
    "unverifiable": (Severity.INFO, Verdict.UNKNOWN, True),
}
SEVERITY_ORDER = [Severity.INFO, Severity.MINOR, Severity.MAJOR, Severity.CRITICAL]


def grade_rights_signal(signal: RightsSignal) -> tuple[Severity, Verdict, bool]:
    """Присваивает серьёзность с инвариантом: модель одна не повышает риск."""

    severity, verdict, review = RISK_LADDER[signal.risk]
    if not signal.deterministic and SEVERITY_ORDER.index(severity) > SEVERITY_ORDER.index(Severity.MINOR):
        return Severity.MINOR, Verdict.UNKNOWN, True
    if not signal.deterministic:
        return severity, Verdict.UNKNOWN, True
    return severity, verdict, review


def license_policy(spdx: str | None) -> str:
    """Классифицирует SPDX-выражение по политике учебного проекта."""

    if spdx is None:
        return "review"
    normalized = spdx.strip()
    if normalized in LICENSE_DENY:
        return "deny"
    if normalized in LICENSE_REVIEW or normalized.startswith("LicenseRef-"):
        return "review"
    return "allow"


def scan_project_licenses(root: Path) -> LicenseScan | None:
    """Лёгкий локальный поиск лицензии проекта.

    Это не замена ScanCode: функция ловит явный `LICENSE`, поле `license` в
    `package.json` и `pyproject.toml`. Полный сканер можно подключить за этим
    же контрактом.
    """

    spdx = _license_from_package_json(root) or _license_from_pyproject(root)
    copyrights: list[str] = []
    for license_file in list(root.glob("LICENSE*")) + list(root.glob("NOTICE*")):
        text = _read_small_text(license_file)
        if text:
            copyrights.extend(re.findall(r"copyright\s+.+", text, flags=re.IGNORECASE))
            spdx = spdx or _guess_license_id(text)
    if spdx or copyrights:
        return LicenseScan(spdx=spdx, copyrights=copyrights[:10])
    return None


def resolve_dependency_licenses(manifests: list[ContentFile]) -> list[tuple[str, str | None]]:
    """Возвращает лицензии зависимостей, если они явно указаны локально.

    Для зависимостей из реестров нужен отдельный сетевой адаптер. Здесь не
    создаём шум из «неизвестных» лицензий, пока нет проверенного источника.
    """

    results: list[tuple[str, str | None]] = []
    for manifest in manifests:
        name = Path(manifest.relative_path).name.lower()
        if name == "package.json":
            results.extend(_dependency_licenses_from_package_json(manifest.text))
    return results


def read_image_provenance(path: Path) -> ImageProvenance:
    """Пытается извлечь простые текстовые признаки прав из файла изображения."""

    try:
        data = path.read_bytes()[:80_000]
    except OSError:
        return ImageProvenance()
    text = data.decode("utf-8", errors="ignore")
    return ImageProvenance(
        author=_first_metadata_value(text, ("author", "creator", "artist")),
        copyright=_first_metadata_value(text, ("copyright", "rights")),
        license=_first_metadata_value(text, ("license", "licence")),
        has_c2pa="c2pa" in text.lower() or "content credentials" in text.lower(),
    )


def has_attribution_near(text: str, needle: str) -> bool:
    """Проверяет, есть ли рядом с упоминанием ресурса источник или лицензия."""

    if not needle:
        return False
    position = text.lower().find(needle.lower())
    if position < 0:
        return False
    fragment = text[max(0, position - 300) : position + len(needle) + 300]
    return bool(ATTRIBUTION_RE.search(fragment))


def _read_small_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:50_000]
    except OSError:
        return ""


def _license_from_package_json(root: Path) -> str | None:
    path = root / "package.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = payload.get("license")
    return str(value).strip() if value else None


def _license_from_pyproject(root: Path) -> str | None:
    path = root / "pyproject.toml"
    if not path.exists():
        return None
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    project = payload.get("project")
    if not isinstance(project, dict):
        return None
    value = project.get("license")
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict) and value.get("text"):
        return str(value["text"]).strip()
    return None


def _dependency_licenses_from_package_json(text: str) -> list[tuple[str, str | None]]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    bundled = payload.get("bundledDependencies") or payload.get("bundleDependencies") or []
    if not isinstance(bundled, list):
        return []
    license_id = str(payload.get("license") or "").strip() or None
    return [(str(name), license_id) for name in bundled if isinstance(name, str)]


def _guess_license_id(text: str) -> str | None:
    lowered = text.lower()
    if "mit license" in lowered:
        return "MIT"
    if "apache license" in lowered and "version 2.0" in lowered:
        return "Apache-2.0"
    if "gnu affero general public license" in lowered:
        return "AGPL-3.0-or-later"
    if "gnu general public license" in lowered:
        return "GPL-3.0-or-later"
    return None


def _first_metadata_value(text: str, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        match = re.search(rf"{re.escape(key)}\s*[:=]\s*([^\n\r\x00]{{1,180}})", text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None
