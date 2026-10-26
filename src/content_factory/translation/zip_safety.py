"""Открытие сжатых документов без риска развернуть их в память целиком.

DOCX, XLSX и PPTX — это zip, пришедший от пользователя, а в переводе архива — и вовсе zip внутри
zip. Ограничение на загрузку считает **сжатый** размер, и двадцати пяти мегабайтов с запасом
хватает, чтобы развернуться в сотни гигабайт: мегабайт нулей сжимается до килобайта.

Проверяются три вещи, и каждая закрывает свою лазейку:

* суммарный распакованный размер — иначе тысяча мелких частей обойдёт ограничение на одну;
* размер каждой части — чтобы одна часть не съела весь предел;
* фактически прочитанное — объявленный в заголовке размер это слова архива о себе, и доверять
  им нельзя. Чтение ограничено сверху, поэтому соврать можно, а переполнить память нет.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO

from content_factory.translation.errors import DocumentFormatError

MAX_TOTAL_BYTES = 300 * 1024 * 1024
MAX_MEMBER_BYTES = 100 * 1024 * 1024
# Для частей, которые потом разбираются как XML, предел отдельный и куда строже: дерево объектов
# занимает кратно больше самого текста, поэтому сто мегабайт разметки — это гигабайты в памяти.
# Для картинки внутри документа сто мегабайт при этом нормальны.
MAX_XML_BYTES = 16 * 1024 * 1024

_TOO_BIG = "архив разворачивается слишком сильно: {detail}"


@contextmanager
def open_checked(content: bytes) -> Iterator[zipfile.ZipFile]:
    """Открывает архив, отказывая тому, что разворачивается сверх предела."""
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            _check_sizes(archive)
            yield archive
    except zipfile.BadZipFile as exc:
        raise DocumentFormatError("Файл повреждён или не является архивом") from exc


def read_member(archive: zipfile.ZipFile, name: str, *, limit: int | None = None) -> bytes:
    """Читает часть архива с ограничением сверху, не доверяя заголовку.

    ``limit`` позволяет читать разбираемый XML строже, чем вложенную картинку.
    """
    ceiling = MAX_MEMBER_BYTES if limit is None else limit
    try:
        with archive.open(name) as stream:
            payload = stream.read(ceiling + 1)
    except zipfile.BadZipFile as exc:
        # Сюда приходит и соврaвший заголовок: объявленный размер не сошёлся с содержимым.
        raise DocumentFormatError(f"Часть {name} повреждена или объявлена неверно") from exc
    if len(payload) > ceiling:
        raise DocumentFormatError(_TOO_BIG.format(detail=f"часть {name} больше {ceiling} байт"))
    return payload


def read_xml_member(archive: zipfile.ZipFile, name: str) -> bytes:
    """Читает часть, которую собираются разбирать как XML, со строгим пределом."""
    return read_member(archive, name, limit=MAX_XML_BYTES)


def _check_sizes(archive: zipfile.ZipFile) -> None:
    total = 0
    for info in archive.infolist():
        if info.file_size > MAX_MEMBER_BYTES:
            raise DocumentFormatError(
                _TOO_BIG.format(detail=f"часть {info.filename} объявлена как {info.file_size} байт")
            )
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            raise DocumentFormatError(
                _TOO_BIG.format(detail=f"в сумме больше {MAX_TOTAL_BYTES} байт")
            )
