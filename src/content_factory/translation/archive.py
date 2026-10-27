"""Перевод архива проекта целиком: распаковка, опись, смета, упаковка.

Архив приходит снаружи, и это главное, что определяет устройство модуля. Имя внутри архива —
не путь, а строка, которой архив себя описывает, и верить ей нельзя: ``../`` выводит запись за
корень, буква диска подменяет том, обратная косая на Windows разделитель, а символическая ссылка
указывает куда захочет её автор. Поэтому имя проверяется до того, как что-либо прочитано, и
отказ называет файл: иначе отказ по не той причине выглядит точно так же, как правильный, и
прячет настоящую дыру.

Распакованное живёт в памяти, а не в каталоге. Так проще: обработчики принимают байты, и
промежуточного состояния на диске, которое надо убирать за собой, не возникает вовсе. Расплата —
предел на объём, и он здесь свой, строже общего для сжатых документов: архив курса весит
единицы мегабайт, а шестьдесят четыре мегабайта разом в памяти процесс выдержит.

Опись прогоняет ``extract`` вхолостую: единицы считаются, модель не зовётся. Побочная польза
важнее основной — битый файл всплывает до оплаты, а не посреди прогона.
"""

from __future__ import annotations

import math
import posixpath
import re
import stat
import zipfile
from dataclasses import dataclass
from io import BytesIO

from content_factory.translation.errors import DocumentFormatError
from content_factory.translation.language import dominant_language
from content_factory.translation.registry import handler_for

# Предел на число файлов: архив курса — это сотни файлов, не десятки тысяч.
MAX_FILES = 2_000
# Предел на распакованный объём. Сжатый размер о нём не говорит ничего: мегабайт нулей
# сжимается до килобайта, поэтому ограничение на загрузку здесь не помогает.
MAX_UNPACKED_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 16 * 1024 * 1024

REPORT_NAME = "ПЕРЕВОД-ОТЧЁТ.md"

# Буква диска в начале имени: ``C:/`` или ``C:\``.
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
# Знаки, которых в имени файла быть не должно ни при каких раскладах.
_CONTROL_RE = re.compile(r"[\x00-\x1f]")

# Сколько знаков приходится на один токен. Для русского текста это примерно 2.5; оценка
# нарочно снизу, чтобы смета не выходила меньше счёта.
_CHARS_PER_TOKEN = 2.5
# Накладные токены на один запрос: указания модели и разметка ответа.
_PROMPT_TOKENS_PER_BATCH = 400
# Сколько знаков уходит в один запрос — то же, что в движке перевода.
_BATCH_CHARS = 9_000
# Сколько в среднем длится один запрос. Замерено на gpt-6-sol: от двадцати до сорока секунд.
_SECONDS_PER_BATCH = 30


@dataclass(frozen=True)
class ArchiveMember:
    """Файл внутри архива: проверенный относительный путь и его байты."""

    path: str
    content: bytes


@dataclass(frozen=True)
class InventoryEntry:
    """Строка описи: что это за файл, сколько в нём работы и берём ли мы его."""

    path: str
    handler: str | None
    units: int
    chars: int
    language: str
    selected: bool
    skip_reason: str | None


@dataclass(frozen=True)
class Estimate:
    """Смета по отмеченным файлам. Оценка, а не счёт: и цена, и время приблизительны."""

    files: int
    units: int
    chars: int
    seconds: int
    usd: float
    model: str


def read_members(content: bytes) -> list[ArchiveMember]:
    """Распаковывает архив в память, отвергая всё, чему там не место."""
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            infos = [info for info in archive.infolist() if not _is_directory(info)]
            _check_shape(infos)
            return [
                ArchiveMember(path=safe_archive_path(info.filename), content=_read(archive, info))
                for info in infos
            ]
    except zipfile.BadZipFile as exc:
        raise DocumentFormatError("Файл повреждён или не является архивом") from exc


def take_inventory(members: list[ArchiveMember]) -> list[InventoryEntry]:
    """Считает работу по каждому файлу, не обращаясь к модели."""
    return [_describe(member) for member in members]


def estimate_for(entries: list[InventoryEntry]) -> Estimate:
    """Смета по отмеченным строкам описи — по прайсу модели перевода из реестра."""
    selected = [entry for entry in entries if entry.selected]
    chars = sum(entry.chars for entry in selected)
    units = sum(entry.units for entry in selected)
    batches = math.ceil(chars / _BATCH_CHARS) if chars else 0
    model, input_cost, output_cost = _translator_price()

    text_tokens = chars / _CHARS_PER_TOKEN
    input_tokens = text_tokens + batches * _PROMPT_TOKENS_PER_BATCH
    # Перевод примерно той же длины, что и исходник, поэтому выход считаем по нему же.
    usd = (input_tokens * input_cost + text_tokens * output_cost) / 1_000_000

    return Estimate(
        files=len(selected),
        units=units,
        chars=chars,
        seconds=batches * _SECONDS_PER_BATCH,
        usd=round(usd, 4),
        model=model,
    )


def repack(members: list[ArchiveMember], replacements: dict[str, bytes], *, report: str) -> bytes:
    """Собирает результат: те же имена и та же раскладка, непереведённое — побайтно."""
    known = {member.path for member in members}
    unknown = sorted(set(replacements) - known)
    if unknown:
        raise KeyError(f"перевода ждут файлы, которых нет в архиве: {', '.join(unknown)}")

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for member in members:
            archive.writestr(member.path, replacements.get(member.path, member.content))
        if report:
            archive.writestr(REPORT_NAME, report.encode("utf-8"))
    return buffer.getvalue()


def _describe(member: ArchiveMember) -> InventoryEntry:
    extension = posixpath.splitext(member.path)[1].lower()
    handler = handler_for(extension)
    if handler is None:
        return InventoryEntry(
            path=member.path,
            handler=None,
            units=0,
            chars=0,
            language="unknown",
            selected=False,
            skip_reason="формат не переводится",
        )

    kind = extension.lstrip(".")
    try:
        units = handler.extract(member.content)
    except DocumentFormatError as exc:
        return InventoryEntry(
            path=member.path,
            handler=kind,
            units=0,
            chars=0,
            language="unknown",
            selected=False,
            skip_reason=str(exc),
        )

    chars = sum(len(unit.text) for unit in units)
    language = dominant_language([unit.text for unit in units])
    if not units:
        reason: str | None = "нечего переводить"
    elif language == "other":
        reason = "уже не на русском"
    elif language == "unknown":
        # Букв в файле нет вовсе — номера, разделители, коды. Боевой прогон такой файл
        # оплатил и получил обратно ровно то же самое. Галочку методист поставит сам.
        reason = "язык не определён"
    else:
        reason = None

    return InventoryEntry(
        path=member.path,
        handler=kind,
        units=len(units),
        chars=chars,
        language=language,
        selected=reason is None,
        skip_reason=reason,
    )


def _is_directory(info: zipfile.ZipInfo) -> bool:
    return info.is_dir() or info.filename.endswith("/")


def _check_shape(infos: list[zipfile.ZipInfo]) -> None:
    if len(infos) > MAX_FILES:
        raise DocumentFormatError(f"В архиве больше {MAX_FILES} файлов — такой не разбираем")
    total = 0
    for info in infos:
        if info.file_size > MAX_MEMBER_BYTES:
            raise DocumentFormatError(
                f"Архив разворачивается слишком сильно: {info.filename} "
                f"объявлен как {info.file_size} байт"
            )
        total += info.file_size
        if total > MAX_UNPACKED_BYTES:
            raise DocumentFormatError(
                f"Архив разворачивается слишком сильно: в сумме больше {MAX_UNPACKED_BYTES} байт"
            )


def safe_archive_path(name: str) -> str:
    """Проверяет имя внутри архива и возвращает его же, если оно безопасно.

    Проверки идут до чтения: имя — это слова архива о себе, и каждая закрывает свой способ
    выйти за корень.

    Проверка обратной косой сегодня недостижима через ``zipfile``: тот сам меняет её на прямую
    и при записи, и при чтении. Она здесь потому, что без неё защита зависела бы от этой
    нормализации: ``..\\x`` разбором по ``/`` на части не распадается, части ``..`` в нём нет,
    и такой путь прошёл бы насквозь. Поэтому проверяется прямым вызовом.
    """
    if not name or _CONTROL_RE.search(name):
        raise DocumentFormatError(f"Недопустимое имя файла в архиве: {name!r}")
    if "\\" in name:
        raise DocumentFormatError(
            f"Имя файла в архиве содержит обратную косую, на Windows это выход за корень: {name}"
        )
    if name.startswith("/") or _DRIVE_RE.match(name):
        raise DocumentFormatError(f"Абсолютный путь в архиве: {name}")
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise DocumentFormatError(f"Путь в архиве ведёт за его пределы: {name}")
    return name


def _read(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """Читает запись, не доверяя ни заголовку, ни правам доступа."""
    if info.create_system == 3 and stat.S_ISLNK(info.external_attr >> 16):
        raise DocumentFormatError(
            f"В архиве символическая ссылка {info.filename} — она указывает за его пределы"
        )
    with archive.open(info) as stream:
        payload = stream.read(MAX_MEMBER_BYTES + 1)
    if len(payload) > MAX_MEMBER_BYTES:
        raise DocumentFormatError(
            f"Архив разворачивается слишком сильно: {info.filename} больше "
            f"{MAX_MEMBER_BYTES} байт"
        )
    return payload


def _translator_price() -> tuple[str, float, float]:
    """Модель перевода и её прайс из реестра. Без цены смета считается нулевой."""
    from content_factory.platform.llm.model_registry import ModelRegistry

    registry = ModelRegistry.load()
    for route in registry.role_config("translation").fallback_chain:
        if route.input_cost_per_1m is not None and route.output_cost_per_1m is not None:
            return route.model or route.provider, route.input_cost_per_1m, route.output_cost_per_1m
    return "неизвестна", 0.0, 0.0
