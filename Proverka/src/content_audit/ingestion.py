"""Загрузка и нормализация локальной папки учебного проекта."""

from __future__ import annotations

import hashlib
from pathlib import Path

from content_audit.domain import ContentFile, ContentUnit


SUPPORTED_SUFFIXES = {
    ".md",
    ".txt",
    ".yml",
    ".yaml",
    ".json",
    ".csv",
    ".toml",
    ".lock",
    ".xml",
    ".gradle",
}

SUPPORTED_EXTENSIONLESS_NAMES = {
    "changelog",
    "dockerfile",
    "go.mod",
    "license",
    "pipfile",
}

IGNORED_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    ".venv",
    "__pycache__",
    "node_modules",
}

IGNORED_TOP_LEVEL_DIRS = {
    "build",
    "dist",
}

UNIT_MARKERS = {
    "readme.md",
    "readme_rus.md",
    "readme_uzb.md",
    "check-list.yml",
    "check-list.yaml",
}


def discover_content_units(root_path: Path) -> list[ContentUnit]:
    """Находим единицы контента по локальной папке.

    Если передана папка одного проекта, возвращаем одну единицу. Если передан
    каталог с несколькими проектами, единицами становятся дочерние папки с
    признаками учебного проекта.
    """

    root = root_path.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Папка не найдена: {root}")

    if _looks_like_unit(root):
        return [_build_unit(root, root)]

    unit_roots = [_resolve_unit_root(child) for child in sorted(root.iterdir()) if child.is_dir()]
    units = [_build_unit(unit_root, root) for unit_root in unit_roots if unit_root is not None]
    return units or [_build_unit(root, root)]


def load_unit_files(unit: ContentUnit, max_file_bytes: int) -> ContentUnit:
    """Загружаем текстовые файлы единицы и пропускаем тяжёлые или служебные файлы."""

    files: list[ContentFile] = []
    for path in sorted(unit.root_path.rglob("*")):
        if not path.is_file() or _is_ignored(path, unit.root_path):
            continue
        if not _is_supported_text_file(path):
            continue
        size_bytes = path.stat().st_size
        if size_bytes > max_file_bytes:
            continue
        text = _read_text(path)
        relative_path = path.relative_to(unit.root_path).as_posix()
        files.append(
            ContentFile(
                relative_path=relative_path,
                absolute_path=path,
                kind=_classify_file(path),
                text=text,
                size_bytes=size_bytes,
            )
        )

    return unit.model_copy(update={"files": files})


def _looks_like_unit(path: Path) -> bool:
    """Проверяем минимальные признаки учебного проекта."""

    names = {item.name.lower() for item in path.iterdir()}
    if names & UNIT_MARKERS:
        return True
    return any((path / dirname).is_dir() for dirname in ("materials", "tests", "src", "misc"))


def _resolve_unit_root(path: Path) -> Path | None:
    """Находит реальный корень проекта внутри возможной архивной обёртки."""

    if _looks_like_unit(path):
        return path
    child_dirs = [child for child in path.iterdir() if child.is_dir() and not child.name.startswith(".")]
    if len(child_dirs) == 1 and _looks_like_unit(child_dirs[0]):
        return child_dirs[0]
    return None


def _build_unit(unit_root: Path, discovery_root: Path) -> ContentUnit:
    """Создаём стабильный технический идентификатор из пути единицы."""

    relative = unit_root.relative_to(discovery_root).as_posix() if unit_root != discovery_root else "."
    slug = _slugify(unit_root.name or "content")
    digest = hashlib.sha1(relative.encode("utf-8")).hexdigest()[:8]
    unit_id = f"{slug}__{digest}"
    branch = _guess_branch(unit_root)
    return ContentUnit(unit_id=unit_id, name=unit_root.name, root_path=unit_root, relative_path=relative, branch=branch)


def _is_ignored(path: Path, root: Path) -> bool:
    """Отбрасываем служебные папки, чтобы не проверять артефакты сборки."""

    relative_parts = path.relative_to(root).parts
    if any(part in IGNORED_DIRS for part in relative_parts):
        return True
    return bool(relative_parts and relative_parts[0] in IGNORED_TOP_LEVEL_DIRS)


def _read_text(path: Path) -> str:
    """Читаем текст с устойчивостью к старым кодировкам."""

    for encoding in ("utf-8", "utf-8-sig", "cp1251"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _is_supported_text_file(path: Path) -> bool:
    """Принимаем текстовые файлы с расширением и стандартные файлы без расширения."""

    return path.suffix.lower() in SUPPORTED_SUFFIXES or path.name.lower() in SUPPORTED_EXTENSIONLESS_NAMES


def _classify_file(path: Path) -> str:
    """Назначаем тип файла для маршрутизации проверок."""

    lower_name = path.name.lower()
    if lower_name.startswith("readme"):
        return "readme"
    if lower_name.startswith("check-list"):
        return "checklist"
    if _is_dependency_manifest(path):
        return "dependency_manifest"
    if "material" in path.as_posix().lower():
        return "material"
    if "/tests/" in path.as_posix().lower():
        return "test"
    return "text"


def _is_dependency_manifest(path: Path) -> bool:
    """Отмечаем файлы зависимостей и окружения для отдельной проверки актуальности."""

    lower_name = path.name.lower()
    return lower_name in {
        "package.json",
        "package-lock.json",
        "requirements.txt",
        "requirements-dev.txt",
        "pyproject.toml",
        "poetry.lock",
        "pipfile",
        "pipfile.lock",
        "dockerfile",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "go.mod",
    }


def _slugify(value: str) -> str:
    """Делаем короткое имя, пригодное для отчётов и файлов."""

    cleaned = []
    for char in value.lower():
        if char.isalnum():
            cleaned.append(char)
        elif cleaned and cleaned[-1] != "_":
            cleaned.append("_")
    return "".join(cleaned).strip("_") or "content"


def _guess_branch(unit_root: Path) -> str | None:
    """Пытаемся грубо определить ветку по родительской структуре."""

    parts = [part for part in unit_root.parts if part]
    if len(parts) >= 2:
        return parts[-2]
    return None
