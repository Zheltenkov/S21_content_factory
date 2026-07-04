"""Лёгкий анализ приложенных артефактов проекта."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

PCAP_EXTENSIONS = {
    ".pcapng",
    ".pcap",
}
ARTIFACT_EXTENSIONS = {
    ".pcapng",
    ".pcap",
    ".dump",
    ".sql",
    ".log",
    ".txt",
    ".csv",
    ".tsv",
    ".json",
    ".xml",
    ".yml",
    ".yaml",
}
TEXT_EXTENSIONS = {
    ".sql",
    ".log",
    ".txt",
    ".csv",
    ".tsv",
    ".json",
    ".xml",
    ".yml",
    ".yaml",
}
PRINTABLE_BYTES_RE = re.compile(rb"[\x20-\x7e]{4,}")
HEX_PAYLOAD_RE = re.compile(r"(?<![0-9a-fA-F])(?:[0-9a-fA-F]{2}:){3,}[0-9a-fA-F]{2}(?![0-9a-fA-F])")
TSHARK_TIMEOUT_SECONDS = 10.0
TSHARK_MAX_STREAMS = 12


@dataclass(frozen=True)
class ArtifactTextIndex:
    """Текстовый индекс артефактов для дешёвой проверки ожидаемых маркеров."""

    texts_by_ref: dict[str, str]

    def contains(self, artifact_ref: str, marker: str) -> bool:
        """Проверяет маркер по точному пути и базовому имени файла."""

        normalized_marker = _normalize_marker(marker)
        if not normalized_marker:
            return False
        for ref in self._matching_refs(artifact_ref):
            text = self.texts_by_ref.get(ref)
            if text and normalized_marker in text:
                return True
        return False

    def has_text_for(self, artifact_ref: str) -> bool:
        """Проверяет, удалось ли извлечь текстовый отпечаток артефакта."""

        return any(bool(self.texts_by_ref.get(ref)) for ref in self._matching_refs(artifact_ref))

    def texts_for(self, artifact_ref: str) -> tuple[str, ...]:
        """Возвращает все текстовые отпечатки для пути и базового имени."""

        texts: list[str] = []
        for ref in self._matching_refs(artifact_ref):
            text = self.texts_by_ref.get(ref)
            if text and text not in texts:
                texts.append(text)
        return tuple(texts)

    def _matching_refs(self, artifact_ref: str) -> tuple[str, ...]:
        """Возвращает варианты ключей: относительный путь и базовое имя."""

        normalized = _normalize_ref(artifact_ref)
        basename = Path(normalized).name
        refs = [normalized]
        if basename != normalized:
            refs.append(basename)
        return tuple(dict.fromkeys(refs))


def build_artifact_text_index(root_path: Path, *, max_file_bytes: int = 2_000_000) -> ArtifactTextIndex:
    """Строит индекс строк по приложенным артефактам проекта."""

    texts_by_ref: dict[str, str] = {}
    for path in root_path.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in ARTIFACT_EXTENSIONS:
            continue
        try:
            if path.stat().st_size > max_file_bytes:
                continue
            raw = path.read_bytes()
            relative_ref = path.relative_to(root_path).as_posix()
        except OSError:
            continue
        text = _extract_text(path, raw, path.suffix.lower())
        if not text:
            continue
        for ref in _ref_aliases(relative_ref):
            current = texts_by_ref.get(ref)
            texts_by_ref[ref] = text if current is None else f"{current}\n{text}"
    return ArtifactTextIndex(texts_by_ref=texts_by_ref)


def artifact_refs_with_extensions(refs: Iterable[str], extensions: Iterable[str]) -> tuple[str, ...]:
    """Фильтрует ссылки на файлы по расширениям."""

    normalized_extensions = {item.lower() if item.startswith(".") else f".{item.lower()}" for item in extensions}
    result: list[str] = []
    for ref in refs:
        normalized = _normalize_ref(ref)
        if Path(normalized).suffix.lower() in normalized_extensions and normalized not in result:
            result.append(normalized)
    return tuple(result)


def _extract_text(path: Path, raw: bytes, suffix: str) -> str:
    """Достаёт текст из обычного файла или печатные строки из бинарного артефакта."""

    if suffix in PCAP_EXTENSIONS:
        pcap_text = _extract_pcap_text_with_tshark(path)
        fallback_text = _extract_printable_text(raw)
        return _normalize_marker("\n".join(part for part in (pcap_text, fallback_text) if part))
    if suffix in TEXT_EXTENSIONS:
        return _normalize_marker(raw.decode("utf-8", errors="ignore"))
    return _extract_printable_text(raw)


def _extract_pcap_text_with_tshark(path: Path) -> str:
    """Извлекает человекочитаемые поля и потоки из pcap/pcapng через tshark."""

    tshark = _find_tshark()
    if tshark is None:
        return ""

    parts: list[str] = []
    field_output = _run_tshark(
        [
            tshark,
            "-r",
            str(path),
            "-T",
            "fields",
            "-E",
            "separator=\t",
            "-e",
            "frame.number",
            "-e",
            "frame.protocols",
            "-e",
            "ip.src",
            "-e",
            "ip.dst",
            "-e",
            "tcp.stream",
            "-e",
            "udp.stream",
            "-e",
            "data.text",
            "-e",
            "http.file_data",
            "-e",
            "tcp.payload",
            "-e",
            "udp.payload",
        ]
    )
    if field_output:
        parts.append(_decode_tshark_payloads(field_output))

    for stream_kind, field_name in (("tcp", "tcp.stream"), ("udp", "udp.stream")):
        stream_output = _run_tshark([tshark, "-r", str(path), "-T", "fields", "-e", field_name])
        for stream_id in _stream_ids(stream_output):
            followed = _run_tshark([tshark, "-r", str(path), "-q", "-z", f"follow,{stream_kind},ascii,{stream_id}"])
            if followed:
                parts.append(followed)

    return _normalize_marker("\n".join(parts))


def _find_tshark() -> str | None:
    """Находит tshark в PATH или в типовых каталогах Wireshark на Windows."""

    found = shutil.which("tshark")
    if found:
        return found
    candidates = (
        Path("C:/Program Files/Wireshark/tshark.exe"),
        Path("C:/Program Files (x86)/Wireshark/tshark.exe"),
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def _run_tshark(args: list[str]) -> str:
    """Запускает tshark без оболочки и возвращает только успешный вывод."""

    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=TSHARK_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout or ""


def _stream_ids(output: str) -> tuple[str, ...]:
    """Достаёт ограниченный набор номеров потоков для follow-анализа."""

    result: list[str] = []
    for item in re.findall(r"\b\d+\b", output or ""):
        if item not in result:
            result.append(item)
        if len(result) >= TSHARK_MAX_STREAMS:
            break
    return tuple(result)


def _decode_tshark_payloads(output: str) -> str:
    """Добавляет ASCII-представление hex-полей tcp.payload/udp.payload."""

    decoded_parts: list[str] = [output]
    for match in HEX_PAYLOAD_RE.finditer(output or ""):
        decoded = _decode_hex_payload(match.group(0))
        if decoded:
            decoded_parts.append(decoded)
    return "\n".join(decoded_parts)


def _decode_hex_payload(value: str) -> str:
    """Декодирует colon-separated hex-полезную нагрузку из tshark."""

    try:
        raw = bytes.fromhex(value.replace(":", ""))
    except ValueError:
        return ""
    return _extract_printable_text(raw)


def _extract_printable_text(raw: bytes) -> str:
    """Возвращает нормализованные печатные ASCII-фрагменты из байтов."""

    strings = [match.group(0).decode("ascii", errors="ignore") for match in PRINTABLE_BYTES_RE.finditer(raw)]
    return _normalize_marker("\n".join(strings))


def _ref_aliases(ref: str) -> tuple[str, ...]:
    """Создаёт ключи индекса для относительного пути и имени файла."""

    normalized = _normalize_ref(ref)
    basename = Path(normalized).name
    return tuple(dict.fromkeys([normalized, basename]))


def _normalize_ref(ref: str) -> str:
    """Нормализует путь артефакта для сравнения."""

    return str(ref or "").strip().replace("\\", "/").lower()


def _normalize_marker(value: str) -> str:
    """Нормализует текст маркера или артефакта для поиска."""

    return re.sub(r"\s+", " ", str(value or "").lower()).strip()
