"""A made-up day of an intensive kept the way its authors keep it: the README as a Word document, drafts beside it.

Not a project of the school. The shape is that of the runs the benchmark was shown: heading styles, lists numbered by
the document, runs in a monospaced font for file names, «**Результат:**» lines instead of blocks, a bonus task named
«Задание 3. Бонусное. …», a check-list whose question is asked about two tasks at once.
"""

import io
import struct
import zlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn

W = nsdecls("w")
DECIMAL, BULLET, AGAIN = 90, 91, 92  # the lists of a built document: numbered, bulleted, numbered once more
NUMBERING = "".join(
    f'<w:abstractNum {W} w:abstractNumId="{ident}"><w:lvl w:ilvl="0"><w:numFmt w:val="{fmt}"/></w:lvl>'
    f'<w:lvl w:ilvl="1"><w:numFmt w:val="{inner}"/></w:lvl></w:abstractNum>'
    for ident, fmt, inner in ((DECIMAL, "decimal", "lowerLetter"), (BULLET, "bullet", "bullet")))
NUMBERS = "".join(f'<w:num {W} w:numId="{ident}"><w:abstractNumId w:val="{abstract}"/></w:num>'
                  for ident, abstract in ((DECIMAL, DECIMAL), (BULLET, BULLET), (AGAIN, DECIMAL)))

CHECKLIST = """sections:
  - name: Основная часть
    questions:
      - name: Задания 1—2. Разведка и счёт
        description: |
          - `bash src/count.sh` печатает число строк журнала.
          - Участник объясняет, как нашёл журнал.
        rating: "5"
  - name: Бонусная часть
    questions:
      - name: Задание 3. Реставратор
        description: "- Программа собирает сообщение из обрывков."
        rating: "6"
"""


def png() -> bytes:
    """The smallest picture there is: one white point."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff")) + chunk(b"IEND", b"")


class Built:
    """A Word document under construction. Every method adds one thing to its end and returns what it added."""

    def __init__(self) -> None:
        self.document = Document()
        numbering = self.document.part.numbering_part.element
        for xml in (NUMBERING, NUMBERS):
            for element in parse_xml(f"<w:numbering {W}>{xml}</w:numbering>"):
                numbering.append(element)

    def heading(self, text: str, level: int) -> Any:
        """Level 0 is the style «Title»."""
        return self.document.add_heading(text, level)

    def text(self, *runs: str | tuple[str, str], style: str | None = None) -> Any:
        """A paragraph of runs: plain words, or (words, marks) with «b» for bold and «m» for a monospaced font."""
        paragraph = self.document.add_paragraph(style=style)
        for run in runs:
            words, marks = (run, "") if isinstance(run, str) else run
            made = paragraph.add_run(words)
            made.bold = True if "b" in marks else None
            if "m" in marks:
                made.font.name = "Roboto Mono"
        return paragraph

    def item(self, *runs: str | tuple[str, str], of: int = DECIMAL, level: int = 0) -> Any:
        """An item of a list, numbered by the paragraph itself, as a document exported from Google Docs has it."""
        paragraph = self.text(*runs)
        paragraph._p.get_or_add_pPr().append(parse_xml(f'<w:numPr {W}><w:ilvl w:val="{level}"/><w:numId w:val="{of}"/></w:numPr>'))
        return paragraph

    def table(self, rows: list[list[str]]) -> Any:
        table = self.document.add_table(rows=len(rows), cols=max(len(row) for row in rows))
        for line, row in zip(table.rows, rows, strict=True):
            for cell, words in zip(line.cells, row, strict=False):
                cell.text = words
        return table

    def raw(self, paragraph: Any, xml: str) -> None:
        """XML put into a paragraph as it is — what the library has no method for."""
        paragraph._p.append(parse_xml(xml))

    def picture(self) -> None:
        self.document.add_picture(io.BytesIO(png()))

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.document.save(str(path))
        return path


def day() -> Built:
    """The README of the made-up day."""
    built = Built()
    built.heading("День 01. Журнал", 1)
    built.text("Сегодня ты найдёшь журнал станции и посчитаешь его строки.")
    built.heading("Материалы", 2)
    built.table([["Что нужно", "Где найти"], ["Команды терминала", "materials/terminal_rus.md"]])
    built.heading("Задание 1. Разведка", 2)
    built.item("Открой терминал.")
    built.item("Найди файл ", ("datasets/station.log", "m"), ".")
    built.text(("Результат:", "b"), " путь к журналу записан в ", ("src/path.txt", "m"), ".\n", ("Зачем:", "b"), " без журнала считать нечего.")
    built.heading("Задание 2. Счёт", 2)
    built.text("Создай ", ("src/count.sh", "m"), ": скрипт печатает число строк журнала.")
    built.text("# первая строка журнала начинается с решётки")
    built.table([["Что сдаёшь", "Как проверяется"], ["src/count.sh", "cat datasets/station.log | wc -l даёт то же число"]])
    built.heading("Задание 3. Бонусное. Реставратор", 2)
    built.text("Собери сообщение из обрывков в ", ("datasets/pieces/", "m"), ".")
    built.heading("Что сдаётся за день", 2)
    built.item("src/path.txt", of=BULLET)
    built.item("src/count.sh", of=BULLET)
    return built


def kept(folder: Path, files: Mapping[str, str | bytes] | None = None, meta: str | None = None) -> Path:
    """The working folder of the day: the README in Word, the check-list under the day's name, a draft and what is
    kept from the learners beside them — and the meta.yaml that says which is which."""
    day().save(folder / "README.md.docx")
    (folder / "D1_check-list_RUS.yml").write_text(CHECKLIST, encoding="utf-8")
    for path, content in {"datasets/station.log": "# start\nok\n", "materials/terminal_rus.md": "ls — список файлов\n", **(files or {})}.items():
        target = folder / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    if meta is not None:
        (folder / "meta.yaml").write_text(meta, encoding="utf-8")
    return folder


META = 'readme: "README.md.docx"\nchecklist: "D1_check-list_RUS.yml"\n'
__all__ = ["AGAIN", "BULLET", "CHECKLIST", "DECIMAL", "META", "Built", "day", "kept", "png", "qn"]
