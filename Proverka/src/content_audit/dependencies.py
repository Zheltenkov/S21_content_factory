"""Разбор зависимостей и проверка официальных реестров пакетов."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests

from content_audit.domain import ContentUnit, TextLocation


SUPPORTED_DEPENDENCY_ECOSYSTEMS = {"npm", "pypi", "docker"}


@dataclass(frozen=True)
class DependencyCandidate:
    """Одна зависимость, извлечённая из файлов проекта."""

    ecosystem: str
    name: str
    spec: str
    source: str
    location: TextLocation
    group: str = "dependencies"


@dataclass
class DependencyMetadata:
    """Сведения об актуальности из официального источника."""

    ecosystem: str
    name: str
    latest_version: str | None
    source_url: str
    checked_at: datetime
    license_spdx: str | None = None
    required_python: str | None = None
    peer_dependencies: dict[str, str] = field(default_factory=dict)
    engines: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CompatibilityIssue:
    """Конфликт между зависимостью и требованиями другой зависимости."""

    dependency: DependencyCandidate
    related_name: str
    declared_spec: str
    required_spec: str
    reason: str


class DependencyRegistryError(RuntimeError):
    """Ошибка обращения к официальному реестру зависимостей."""


class DependencyRegistryClient:
    """Клиент официальных реестров npm, PyPI и Docker Hub."""

    def __init__(self, timeout_seconds: float = 8.0) -> None:
        self.timeout_seconds = timeout_seconds

    def fetch(self, candidate: DependencyCandidate) -> DependencyMetadata:
        """Возвращает сведения о зависимости из официального реестра."""

        if candidate.ecosystem == "npm":
            return self._fetch_npm(candidate)
        if candidate.ecosystem == "pypi":
            return self._fetch_pypi(candidate)
        if candidate.ecosystem == "docker":
            return self._fetch_docker(candidate)
        raise DependencyRegistryError(f"Реестр для {candidate.ecosystem} не поддержан.")

    def _fetch_npm(self, candidate: DependencyCandidate) -> DependencyMetadata:
        encoded_name = quote(candidate.name, safe="")
        url = f"https://registry.npmjs.org/{encoded_name}"
        payload = _get_json(url, self.timeout_seconds)
        latest = payload.get("dist-tags", {}).get("latest")
        version_payload = _npm_version_payload(payload, candidate.spec, latest)
        return DependencyMetadata(
            ecosystem="npm",
            name=candidate.name,
            latest_version=str(latest) if latest else None,
            source_url=url,
            checked_at=datetime.now(timezone.utc),
            license_spdx=_license_value(version_payload.get("license") or payload.get("license")),
            peer_dependencies=_string_dict(version_payload.get("peerDependencies")),
            engines=_string_dict(version_payload.get("engines")),
        )

    def _fetch_pypi(self, candidate: DependencyCandidate) -> DependencyMetadata:
        encoded_name = quote(candidate.name, safe="")
        url = f"https://pypi.org/pypi/{encoded_name}/json"
        payload = _get_json(url, self.timeout_seconds)
        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        return DependencyMetadata(
            ecosystem="pypi",
            name=candidate.name,
            latest_version=str(info.get("version") or "") or None,
            source_url=url,
            checked_at=datetime.now(timezone.utc),
            license_spdx=_license_value(info.get("license_expression") or info.get("license")),
            required_python=str(info.get("requires_python") or "") or None,
        )

    def _fetch_docker(self, candidate: DependencyCandidate) -> DependencyMetadata:
        image, tag = _split_docker_image(candidate.name, candidate.spec)
        repository = image if "/" in image else f"library/{image}"
        encoded_repository = "/".join(quote(part, safe="") for part in repository.split("/"))
        encoded_tag = quote(tag or "latest", safe="")
        url = f"https://registry.hub.docker.com/v2/repositories/{encoded_repository}/tags/{encoded_tag}"
        _get_json(url, self.timeout_seconds)
        return DependencyMetadata(
            ecosystem="docker",
            name=image,
            latest_version=tag or "latest",
            source_url=url,
            checked_at=datetime.now(timezone.utc),
        )


def extract_dependency_candidates(unit: ContentUnit) -> list[DependencyCandidate]:
    """Достаёт зависимости из манифестов проекта."""

    candidates: list[DependencyCandidate] = []
    for file in unit.files:
        lower_name = file.relative_path.lower().rsplit("/", 1)[-1]
        if lower_name == "package.json":
            candidates.extend(_extract_package_json(file.text, file.relative_path))
        elif lower_name == "requirements.txt" or lower_name.startswith("requirements-"):
            candidates.extend(_extract_requirements(file.text, file.relative_path))
        elif lower_name == "pyproject.toml":
            candidates.extend(_extract_pyproject(file.text, file.relative_path))
        elif lower_name == "dockerfile":
            candidates.extend(_extract_dockerfile(file.text, file.relative_path))
    return _deduplicate_candidates(candidates)


def find_compatibility_issues(
    candidates: list[DependencyCandidate],
    metadata_by_key: dict[tuple[str, str], DependencyMetadata],
) -> list[CompatibilityIssue]:
    """Ищет явные конфликты между корневыми зависимостями проекта."""

    issues: list[CompatibilityIssue] = []
    npm_specs = _candidate_specs(candidates, "npm")
    pypi_specs = _candidate_specs(candidates, "pypi")
    project_node = _project_node_engine(candidates)
    project_python = _project_python_requirement(candidates)

    for candidate in candidates:
        metadata = metadata_by_key.get((candidate.ecosystem, _normalise_package_name(candidate.name)))
        if metadata is None:
            continue
        if candidate.ecosystem == "npm":
            issues.extend(_npm_compatibility_issues(candidate, metadata, npm_specs, project_node))
        elif candidate.ecosystem == "pypi":
            issue = _python_compatibility_issue(candidate, metadata, project_python, pypi_specs)
            if issue is not None:
                issues.append(issue)
    return issues


def dependency_cache_key(candidate: DependencyCandidate) -> str:
    """Создаёт стабильный ключ для кэша официальных реестров."""

    return f"v2:{candidate.ecosystem}:{_normalise_package_name(candidate.name)}:{candidate.spec}"


def dependency_identity(candidate: DependencyCandidate) -> tuple[str, str]:
    """Возвращает ключ зависимости для сопоставления метаданных и конфликтов."""

    return candidate.ecosystem, _normalise_package_name(candidate.name)


def metadata_from_record(record: dict[str, Any]) -> DependencyMetadata:
    """Восстанавливает метаданные реестра из кэша."""

    checked_at = datetime.fromisoformat(str(record["checked_at"]))
    return DependencyMetadata(
        ecosystem=str(record["ecosystem"]),
        name=str(record["name"]),
        latest_version=str(record["latest_version"]) if record.get("latest_version") else None,
        source_url=str(record["source_url"]),
        checked_at=checked_at,
        license_spdx=str(record["license_spdx"]) if record.get("license_spdx") else None,
        required_python=str(record["required_python"]) if record.get("required_python") else None,
        peer_dependencies=_string_dict(record.get("peer_dependencies")),
        engines=_string_dict(record.get("engines")),
    )


def metadata_to_record(metadata: DependencyMetadata) -> dict[str, Any]:
    """Сериализует метаданные реестра для кэша."""

    return {
        "ecosystem": metadata.ecosystem,
        "name": metadata.name,
        "latest_version": metadata.latest_version,
        "source_url": metadata.source_url,
        "checked_at": metadata.checked_at.isoformat(),
        "license_spdx": metadata.license_spdx,
        "required_python": metadata.required_python,
        "peer_dependencies": metadata.peer_dependencies,
        "engines": metadata.engines,
    }


def is_pinned_outdated(spec: str, latest_version: str | None) -> bool:
    """Проверяет, что точная закреплённая версия ниже последней известной."""

    exact = _exact_version(spec)
    latest = _version_tuple(latest_version or "")
    return exact is not None and latest is not None and exact < latest


def is_unbounded_spec(spec: str) -> bool:
    """Проверяет, что зависимость не имеет ограничений версии."""

    normalized = spec.strip().lower()
    return normalized in {"", "*", "latest"} or not re.search(r"\d", normalized)


def _extract_package_json(text: str, file_path: str) -> list[DependencyCandidate]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []

    candidates: list[DependencyCandidate] = []
    for group in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        deps = payload.get(group)
        if not isinstance(deps, dict):
            continue
        for name, spec in deps.items():
            candidates.append(_candidate("npm", str(name), str(spec), file_path, 1, group))

    engines = payload.get("engines")
    if isinstance(engines, dict) and engines.get("node"):
        candidates.append(_candidate("npm", "node", str(engines["node"]), file_path, 1, "engine"))
    return candidates


def _extract_requirements(text: str, file_path: str) -> list[DependencyCandidate]:
    candidates: list[DependencyCandidate] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(("-", ".")):
            continue
        name, spec = _split_python_requirement(line)
        if name:
            candidates.append(_candidate("pypi", name, spec, file_path, line_number, "requirements"))
    return candidates


def _extract_pyproject(text: str, file_path: str) -> list[DependencyCandidate]:
    try:
        payload = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    project = payload.get("project") if isinstance(payload.get("project"), dict) else {}
    candidates: list[DependencyCandidate] = []
    requires_python = project.get("requires-python")
    if isinstance(requires_python, str) and requires_python.strip():
        candidates.append(_candidate("pypi", "python", requires_python.strip(), file_path, 1, "runtime"))
    for item in project.get("dependencies") or []:
        name, spec = _split_python_requirement(str(item))
        if name:
            candidates.append(_candidate("pypi", name, spec, file_path, 1, "dependencies"))
    return candidates


def _extract_dockerfile(text: str, file_path: str) -> list[DependencyCandidate]:
    candidates: list[DependencyCandidate] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        match = re.match(r"\s*FROM\s+([^\s]+)", raw_line, flags=re.IGNORECASE)
        if not match:
            continue
        image, tag = _split_docker_image(match.group(1), "")
        candidates.append(_candidate("docker", image, tag or "latest", file_path, line_number, "base_image"))
    return candidates


def _candidate(
    ecosystem: str,
    name: str,
    spec: str,
    file_path: str,
    line: int,
    group: str,
) -> DependencyCandidate:
    return DependencyCandidate(
        ecosystem=ecosystem,
        name=name.strip(),
        spec=spec.strip(),
        source=file_path,
        location=TextLocation(file_path=file_path, line_start=line, line_end=line),
        group=group,
    )


def _deduplicate_candidates(candidates: list[DependencyCandidate]) -> list[DependencyCandidate]:
    result: list[DependencyCandidate] = []
    seen: set[tuple[str, str, str, str]] = set()
    for candidate in candidates:
        key = (candidate.ecosystem, _normalise_package_name(candidate.name), candidate.spec, candidate.group)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _candidate_specs(candidates: list[DependencyCandidate], ecosystem: str) -> dict[str, str]:
    return {
        _normalise_package_name(candidate.name): candidate.spec
        for candidate in candidates
        if candidate.ecosystem == ecosystem
    }


def _project_node_engine(candidates: list[DependencyCandidate]) -> str | None:
    for candidate in candidates:
        if candidate.ecosystem == "npm" and candidate.name.lower() == "node":
            return candidate.spec
    return None


def _project_python_requirement(candidates: list[DependencyCandidate]) -> str | None:
    for candidate in candidates:
        if candidate.ecosystem == "pypi" and candidate.name.lower() == "python":
            return candidate.spec
    return None


def _npm_compatibility_issues(
    candidate: DependencyCandidate,
    metadata: DependencyMetadata,
    npm_specs: dict[str, str],
    project_node: str | None,
) -> list[CompatibilityIssue]:
    issues: list[CompatibilityIssue] = []
    for peer_name, peer_spec in metadata.peer_dependencies.items():
        declared_spec = npm_specs.get(_normalise_package_name(peer_name))
        if declared_spec is None:
            issues.append(
                CompatibilityIssue(candidate, peer_name, "", peer_spec, "Не объявлена обязательная peer-зависимость.")
            )
        elif not version_specs_might_overlap(declared_spec, peer_spec):
            issues.append(
                CompatibilityIssue(candidate, peer_name, declared_spec, peer_spec, "Версионные ограничения не пересекаются.")
            )
    node_requirement = metadata.engines.get("node")
    if project_node and node_requirement and not version_specs_might_overlap(project_node, node_requirement):
        issues.append(
            CompatibilityIssue(candidate, "node", project_node, node_requirement, "Требования к Node.js не пересекаются.")
        )
    return issues


def _python_compatibility_issue(
    candidate: DependencyCandidate,
    metadata: DependencyMetadata,
    project_python: str | None,
    pypi_specs: dict[str, str],
) -> CompatibilityIssue | None:
    if candidate.name.lower() == "python":
        return None
    if project_python and metadata.required_python and not version_specs_might_overlap(project_python, metadata.required_python):
        return CompatibilityIssue(
            candidate,
            "python",
            project_python,
            metadata.required_python,
            "Требования пакета к Python не пересекаются с проектом.",
        )
    declared_spec = pypi_specs.get(_normalise_package_name(candidate.name))
    if declared_spec and candidate.spec and not version_specs_might_overlap(declared_spec, candidate.spec):
        return CompatibilityIssue(candidate, candidate.name, declared_spec, candidate.spec, "Дублирующиеся ограничения не пересекаются.")
    return None


def version_specs_might_overlap(left: str, right: str) -> bool:
    """Осторожно проверяет пересечение простых ограничений версий."""

    left_range = _version_range(left)
    right_range = _version_range(right)
    if left_range is None or right_range is None:
        return True
    left_min, left_max = left_range
    right_min, right_max = right_range
    if left_max is not None and right_min is not None and left_max <= right_min:
        return False
    if right_max is not None and left_min is not None and right_max <= left_min:
        return False
    return True


def _version_range(spec: str) -> tuple[tuple[int, ...] | None, tuple[int, ...] | None] | None:
    normalized = spec.strip()
    if not normalized or normalized in {"*", "latest"} or "||" in normalized:
        return None
    minimum: tuple[int, ...] | None = None
    maximum: tuple[int, ...] | None = None
    for part in re.split(r"[, ]+", normalized):
        if not part:
            continue
        parsed = _constraint_range(part)
        if parsed is None:
            continue
        part_min, part_max = parsed
        minimum = _max_version(minimum, part_min)
        maximum = _min_version(maximum, part_max)
    return minimum, maximum


def _constraint_range(part: str) -> tuple[tuple[int, ...] | None, tuple[int, ...] | None] | None:
    exact_match = re.fullmatch(r"=?=?\s*v?(\d+(?:\.\d+){0,2})", part)
    if exact_match:
        version = _version_tuple(exact_match.group(1))
        return version, _bump_patch(version) if version else None
    match = re.match(r"(>=|>|<=|<|==|~=|\^|~)?\s*v?(\d+(?:\.\d+){0,2})", part)
    if not match:
        return None
    operator, raw_version = match.groups()
    version = _version_tuple(raw_version)
    if version is None:
        return None
    operator = operator or "=="
    if operator in {">=", ">"}:
        return version, None
    if operator in {"<", "<="}:
        return None, version
    if operator == "^":
        return version, _caret_upper_bound(version)
    if operator in {"~", "~="}:
        return version, _tilde_upper_bound(version)
    return version, _bump_patch(version)


def _exact_version(spec: str) -> tuple[int, ...] | None:
    normalized = spec.strip()
    match = re.fullmatch(r"(?:==)?\s*v?(\d+(?:\.\d+){0,2})", normalized)
    return _version_tuple(match.group(1)) if match else None


def _version_tuple(value: str) -> tuple[int, ...] | None:
    match = re.match(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", value.strip())
    if not match:
        return None
    parts = [int(part) if part is not None else 0 for part in match.groups()]
    return tuple(parts)


def _bump_patch(version: tuple[int, ...] | None) -> tuple[int, ...] | None:
    if version is None:
        return None
    major, minor, patch = _pad_version(version)
    return major, minor, patch + 1


def _caret_upper_bound(version: tuple[int, ...]) -> tuple[int, ...]:
    major, minor, _patch = _pad_version(version)
    if major > 0:
        return major + 1, 0, 0
    return 0, minor + 1, 0


def _tilde_upper_bound(version: tuple[int, ...]) -> tuple[int, ...]:
    major, minor, _patch = _pad_version(version)
    return major, minor + 1, 0


def _pad_version(version: tuple[int, ...]) -> tuple[int, int, int]:
    padded = list(version[:3])
    while len(padded) < 3:
        padded.append(0)
    return padded[0], padded[1], padded[2]


def _max_version(left: tuple[int, ...] | None, right: tuple[int, ...] | None) -> tuple[int, ...] | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


def _min_version(left: tuple[int, ...] | None, right: tuple[int, ...] | None) -> tuple[int, ...] | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)


def _split_python_requirement(line: str) -> tuple[str | None, str]:
    line = line.split(";", 1)[0].strip()
    line = re.sub(r"\[[^\]]+\]", "", line)
    match = re.match(r"([A-Za-z0-9_.-]+)\s*(.*)", line)
    if not match:
        return None, ""
    name, spec = match.groups()
    return name, spec.strip()


def _split_docker_image(value: str, fallback_tag: str) -> tuple[str, str]:
    image = value.split("@", 1)[0]
    if ":" in image.rsplit("/", 1)[-1]:
        name, tag = image.rsplit(":", 1)
        return name, tag
    return image, fallback_tag


def _npm_version_payload(payload: dict[str, Any], spec: str, latest: object) -> dict[str, Any]:
    versions = payload.get("versions")
    if not isinstance(versions, dict):
        return {}
    exact = _exact_version(spec)
    if exact is not None:
        for version, version_payload in versions.items():
            if _version_tuple(version) == exact and isinstance(version_payload, dict):
                return version_payload
    latest_payload = versions.get(str(latest))
    return latest_payload if isinstance(latest_payload, dict) else {}


def _get_json(url: str, timeout_seconds: float) -> dict[str, Any]:
    try:
        response = requests.get(url, timeout=timeout_seconds, headers={"User-Agent": "ContentAudit/0.1"})
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - реестр может вернуть сетевую или JSON-ошибку.
        raise DependencyRegistryError(str(exc)) from exc
    if not isinstance(payload, dict):
        raise DependencyRegistryError("Реестр вернул не JSON-объект.")
    return payload


def _string_dict(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _license_value(value: object) -> str | None:
    """Нормализует лицензию из ответа реестра до короткой строки."""

    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    if isinstance(value, dict):
        for key in ("type", "name", "id"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
    return None


def _normalise_package_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value.strip().lower())
