"""Корпусная стадия похожести кода.

Это отдельная операция уровня набора проектов, а не проверка одной единицы.
Результат передаётся в проверку прав как индекс:
`{unit_id: [CodeMatch(...), ...]}`.
"""

from __future__ import annotations

import re
import zlib
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from content_audit.domain import ContentUnit
from content_audit.rights import ATTRIBUTION_RE, CodeMatch


CODE_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".go": "go",
    ".rb": "ruby",
    ".rs": "rust",
    ".cs": "csharp",
}
IGNORED_CODE_DIRS = {".git", ".idea", ".vscode", ".venv", "__pycache__", "node_modules", "dist", "build"}


@dataclass
class CodeDoc:
    """Код одной единицы на одном языке."""

    unit_id: str
    language: str
    text: str
    attributed: bool


class SimilarityBackend(Protocol):
    """Интерфейс бэкенда похожести."""

    def pairwise(self, docs: list[CodeDoc]) -> list[tuple[str, str, float]]:
        """Возвращает пары `(unit_id_a, unit_id_b, similarity)`."""
        ...


class WinnowingBackend:
    """Отпечатки кода в стиле MOSS: k-граммы и выбор минимальных хэшей окна."""

    def __init__(self, k: int = 30, window: int = 4) -> None:
        self.k = k
        self.window = window

    def pairwise(self, docs: list[CodeDoc]) -> list[tuple[str, str, float]]:
        fingerprints = {doc.unit_id: self._fingerprints(doc.text) for doc in docs}
        unit_ids = [doc.unit_id for doc in docs]
        results: list[tuple[str, str, float]] = []
        for left_index in range(len(unit_ids)):
            for right_index in range(left_index + 1, len(unit_ids)):
                left = fingerprints[unit_ids[left_index]]
                right = fingerprints[unit_ids[right_index]]
                if not left or not right:
                    continue
                similarity = len(left & right) / min(len(left), len(right))
                if similarity > 0.0:
                    results.append((unit_ids[left_index], unit_ids[right_index], round(similarity, 4)))
        return results

    def _fingerprints(self, text: str) -> set[int]:
        if len(text) < self.k:
            return set()
        hashes = [zlib.crc32(text[index : index + self.k].encode("utf-8")) for index in range(len(text) - self.k + 1)]
        fingerprints: set[int] = set()
        for index in range(len(hashes) - self.window + 1):
            fingerprints.add(min(hashes[index : index + self.window]))
        return fingerprints


class JPlagBackend:
    """Точка расширения под JPlag CLI."""

    def pairwise(self, docs: list[CodeDoc]) -> list[tuple[str, str, float]]:
        raise NotImplementedError("Подключить JPlag CLI и парсер отчёта.")


class DolosBackend:
    """Точка расширения под самостоятельный Dolos."""

    def pairwise(self, docs: list[CodeDoc]) -> list[tuple[str, str, float]]:
        raise NotImplementedError("Подключить Dolos CLI или API.")


def build_code_similarity_index(
    units: list[ContentUnit],
    backend: SimilarityBackend | None = None,
    threshold: float = 0.8,
) -> dict[str, list[CodeMatch]]:
    """Строит индекс заимствований кода по корпусу единиц."""

    backend = backend or WinnowingBackend()
    docs_by_language = _collect_docs(units)
    attribution = {doc.unit_id: doc.attributed for docs in docs_by_language.values() for doc in docs}
    index: dict[str, list[CodeMatch]] = defaultdict(list)

    for docs in docs_by_language.values():
        if len(docs) < 2:
            continue
        for unit_a, unit_b, similarity in backend.pairwise(docs):
            if similarity < threshold:
                continue
            index[unit_a].append(CodeMatch(unit_b, similarity, attribution.get(unit_a, False)))
            index[unit_b].append(CodeMatch(unit_a, similarity, attribution.get(unit_b, False)))
    return dict(index)


def _collect_docs(units: list[ContentUnit]) -> dict[str, list[CodeDoc]]:
    docs_by_language: dict[str, list[CodeDoc]] = defaultdict(list)
    for unit in units:
        chunks_by_language: dict[str, list[str]] = defaultdict(list)
        attributed = False
        for _relative_path, text, language in _iter_code_sources(unit):
            attributed = attributed or bool(ATTRIBUTION_RE.search(text))
            chunks_by_language[language].append(_normalize(text, language))
        for language, chunks in chunks_by_language.items():
            docs_by_language[language].append(CodeDoc(unit.unit_id, language, "".join(chunks), attributed))
    return docs_by_language


def _iter_code_sources(unit: ContentUnit) -> list[tuple[str, str, str]]:
    """Читает код из `unit.files` и с диска, не вмешиваясь в обычную загрузку аудита."""

    sources: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for file in unit.files:
        language = CODE_EXTENSIONS.get(Path(file.relative_path).suffix.lower())
        if language is None:
            continue
        sources.append((file.relative_path, file.text, language))
        seen.add(file.relative_path)

    if not unit.root_path.exists():
        return sources
    for path in sorted(unit.root_path.rglob("*")):
        if not path.is_file() or _is_ignored_code_path(path, unit.root_path):
            continue
        language = CODE_EXTENSIONS.get(path.suffix.lower())
        if language is None:
            continue
        relative_path = path.relative_to(unit.root_path).as_posix()
        if relative_path in seen or path.stat().st_size > 1_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        sources.append((relative_path, text, language))
    return sources


def _is_ignored_code_path(path: Path, root: Path) -> bool:
    return any(part in IGNORED_CODE_DIRS for part in path.relative_to(root).parts)


def _normalize(text: str, language: str) -> str:
    """Убирает комментарии, строки и пробелы, чтобы сравнение было устойчивее."""

    if language in {"python", "ruby"}:
        text = re.sub(r"#.*", "", text)
    text = re.sub(r"//.*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)
    text = re.sub(r"'(?:\\.|[^'\\])*'", "''", text)
    return re.sub(r"\s+", "", text).lower()
