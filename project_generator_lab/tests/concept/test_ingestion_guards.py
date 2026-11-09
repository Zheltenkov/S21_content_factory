"""Exact behaviour of reading the methodologist's files, written against mutants that survived.

Every material becomes numbered passages with a location a person can find in the source;
a limit is a readable refusal, never silent truncation.
"""

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from docx import Document
from openpyxl import Workbook
from project_generator_lab.concept.ingestion import (
    MAX_FILE_BYTES,
    InputError,
    check_file,
    decode_text,
    extract_material,
    normalize_header,
    parse_curriculum,
)
from pypdf import PdfWriter

FIXTURES = Path(__file__).parent / "fixtures"


def png() -> bytes:
    """A 2×2 white PNG built by hand: Pillow is not a dependency of the lab (local run on Windows 01.10.2026)."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    pixels = b"".join(b"\x00" + b"\xff\xff\xff" * 2 for _ in range(2))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


def refused(name, data, message):
    with pytest.raises(InputError) as error:
        extract_material(name, data)
    assert str(error.value) == message


def test_docx_paragraphs_and_tables_keep_their_places():
    document = Document()
    document.add_paragraph("Первое правило")
    document.add_paragraph("   ")
    document.add_paragraph("Второе правило")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Критерий", "Вес"
    table.cell(1, 0).text, table.cell(1, 1).text = "Метод", "40%"
    buffer = io.BytesIO()
    document.save(buffer)
    data = buffer.getvalue()
    material = extract_material("folder/rules.docx", data)
    digest = hashlib.sha256(data).hexdigest()
    assert (material.id, material.name, material.sha256, material.read_status) == (digest[:16], "rules.docx", digest, "text_extracted")
    assert [(p.id, p.location, p.text) for p in material.passages] == [
        (f"s-{digest[:10]}-1", "Абзац 1 · фрагмент 1", "Первое правило"),
        (f"s-{digest[:10]}-2", "Абзац 3 · фрагмент 1", "Второе правило"),
        (f"s-{digest[:10]}-3", "Таблица 1, строка 1 · фрагмент 1", "Критерий | Вес"),
        (f"s-{digest[:10]}-4", "Таблица 1, строка 2 · фрагмент 1", "Метод | 40%"),
    ]
    assert material.warnings == ["Извлечены абзацы и таблицы. Изображения, текстовые поля и комментарии не анализируются."]
    assert material.unread_locations == []


def test_docx_with_a_picture_is_read_partially():
    document = Document()
    document.add_paragraph("Схема ниже")
    document.add_picture(io.BytesIO(png()))
    buffer = io.BytesIO()
    document.save(buffer)
    material = extract_material("SCHEME.DOCX", buffer.getvalue())
    assert material.read_status == "partial"
    assert material.unread_locations == ["Изображения или текстовые поля DOCX без текстовой расшифровки"]


def test_xlsx_rows_are_located_by_sheet_and_number():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Критерии"
    sheet.append(["Метод", 1, None])
    sheet.append([None, None, None])
    sheet.append(["Вывод", None, "ручная"])
    sheet.append([0, 0.0, "0%"])
    other = workbook.create_sheet("Итог")
    other.append(["Да"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    material = extract_material("criteria.xlsx", buffer.getvalue())
    assert [(p.location, p.text) for p in material.passages] == [
        ("Критерии, строка 1 · фрагмент 1", "Метод | 1 |"),
        ("Критерии, строка 3 · фрагмент 1", "Вывод |  | ручная"),
        ("Критерии, строка 4 · фрагмент 1", "0 | 0 | 0%"),
        ("Итог, строка 1 · фрагмент 1", "Да"),
    ]
    assert material.warnings == [] and material.read_status == "text_extracted"


def test_xlsx_row_limit_is_a_refusal():
    workbook = Workbook()
    for i in range(1501):
        workbook.active.append([i])
    buffer = io.BytesIO()
    workbook.save(buffer)
    refused("big.xlsx", buffer.getvalue(), "В таблице слишком много строк. Выделите нужный фрагмент.")

    def sheet(rows):
        book = Workbook()
        for i in range(rows):
            book.active.append([i])
        out = io.BytesIO()
        book.save(out)
        return out.getvalue()

    # 501–1500 rows pass the row check and stop at the passage limit: the tighter limit wins.
    refused("medium.xlsx", sheet(501), "Слишком много фрагментов. Выделите относящиеся к проекту материалы.")
    assert len(extract_material("ok.xlsx", sheet(500)).passages) == 500


def test_html_keeps_visible_text_only():
    html = "<html><style>p{color:red}</style><script>var x = 1;</script><h1>Заголовок</h1><p> Текст </p><script>y()</script></html>"
    material = extract_material("page.html", html.encode())
    assert [(p.location, p.text) for p in material.passages] == [("Строки 1–2 · фрагмент 1", "Заголовок\nТекст")]
    assert material.warnings == []


def test_notebook_cells_are_read_without_outputs():
    notebook = {"cells": [{"source": ["import pandas\n", "df = 1"], "outputs": ["секрет"]}, {"source": []}, {"source": ["# Шаг 2"]}]}
    material = extract_material("lesson.ipynb", json.dumps(notebook).encode())
    assert [(p.location, p.text) for p in material.passages] == [
        ("Ячейка 1 · фрагмент 1", "import pandas\ndf = 1"), ("Ячейка 3 · фрагмент 1", "# Шаг 2")]
    assert material.warnings == ["Код прочитан без исполнения. Сохранённые outputs ноутбука не использованы."]


def test_text_is_cut_into_35_line_windows_and_scripts_are_not_run():
    lines = "\n".join(f"строка {i}" for i in range(1, 41))
    material = extract_material("notes.md", lines.encode())
    assert [p.location for p in material.passages] == ["Строки 1–35 · фрагмент 1", "Строки 36–40 · фрагмент 1"]
    assert material.passages[1].text == "\n".join(f"строка {i}" for i in range(36, 41))
    assert material.warnings == []
    for suffix in (".py", ".js", ".ts", ".sql", ".sh", ".ps1"):
        assert extract_material(f"check{suffix}", b"print(1)").warnings == ["Скрипт прочитан как справочный текст, не запускался."]


def test_long_text_is_split_into_5000_character_fragments():
    text = "а" * 12001
    material = extract_material("long.txt", text.encode())
    assert [(p.location, len(p.text)) for p in material.passages] == [
        ("Строки 1–1 · фрагмент 1", 5000), ("Строки 1–1 · фрагмент 2", 5000), ("Строки 1–1 · фрагмент 3", 2001)]
    assert [p.id[-2:] for p in material.passages] == ["-1", "-2", "-3"]


def test_material_limits_are_readable_refusals():
    refused("empty.txt", b"   \n  ", "Текст не найден. Для скана нужна текстовая расшифровка.")
    refused("huge.txt", ("б" * 180001).encode(), "Извлечено больше 180 000 символов. Загрузите нужный фрагмент.")
    assert extract_material("edge.txt", ("б" * 5000 * 36).encode()).passages[-1].location == "Строки 1–1 · фрагмент 36"
    many = "\n".join("x" for _ in range(35 * 501))
    refused("many.txt", many.encode(), "Слишком много фрагментов. Выделите относящиеся к проекту материалы.")
    assert len(extract_material("fits.txt", "\n".join("x" for _ in range(35 * 500)).encode()).passages) == 500
    refused("archive.zip", b"PK", "Формат не поддерживается. Используйте DOCX, PDF, XLSX или текстовый файл.")


def test_pdf_page_limit_and_scans():
    def pdf(pages):
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=200, height=200)
        buffer = io.BytesIO()
        writer.write(buffer)
        return buffer.getvalue()

    refused("book.pdf", pdf(61), "Для прототипа выделите нужные страницы PDF (до 60 страниц)")
    refused("scan.pdf", pdf(60), "Текст не найден. Для скана нужна текстовая расшифровка.")


def test_file_checks_and_encodings():
    with pytest.raises(InputError, match="^Файл пустой.csv пуст$"):
        check_file("пустой.csv", b"")
    check_file("edge.csv", b"x" * MAX_FILE_BYTES)
    with pytest.raises(InputError, match="^Максимальный размер файла — 4 МБ$"):
        check_file("big.csv", b"x" * (MAX_FILE_BYTES + 1))
    with pytest.raises(InputError, match="^Повреждённый документ Office$"):
        check_file("broken.XLSX", b"not a zip")
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"0" * (30 * 1024 * 1024 + 1))
    with pytest.raises(InputError, match="^Слишком большой распакованный документ$"):
        check_file("bomb.docx", bomb.getvalue())
    assert decode_text("Привет".encode("cp1251")) == "Привет"
    assert decode_text("﻿Привет".encode()) == "Привет"
    assert decode_text("Привет".encode("utf-16")) == "Привет"
    assert decode_text("Привет".encode("utf-16-be")[:0] + b"\xfe\xff" + "Привет".encode("utf-16-be")) == "Привет"
    with pytest.raises(InputError, match="^Не удалось определить кодировку. Сохраните текст в UTF-8.$"):
        decode_text(b"\x98\x98")


def test_header_matching_ignores_case_punctuation_and_yo():
    assert normalize_header(" Трудоёмкость, астр. Часы! ") == normalize_header("трудоемкостьастрчасы") == "трудоемкостьастрчасы"
    data = "Название проекта;Трудоемкость, ч\nПроект;6\n".encode()
    assert parse_curriculum("plan.csv", data).rows[0].hours == "6"


def test_curriculum_csv_errors_and_delimiters():
    with pytest.raises(InputError, match="^В CSV нет заголовков и проектов$"):
        parse_curriculum("blank.csv", b" \n \n")
    with pytest.raises(InputError, match="^В CSV нет проектов с заполненным названием$"):
        parse_curriculum("plan.csv", "Название проекта;Часы\n;4\n".encode())
    tabbed = parse_curriculum("plan.tsv.csv", "Название проекта\tЧасы\nАльфа\t5\n".encode())
    assert (tabbed.rows[0].title, tabbed.rows[0].hours) == ("Альфа", "5")
    with pytest.raises(InputError, match="^Запись 2: 3 ячеек вместо 2. Проверьте кавычки и разделители CSV.$"):
        parse_curriculum("plan.csv", "Название проекта;Часы\nА;4;лишнее\n".encode())


def test_legacy_blank_block_header_is_not_the_number_column():
    curriculum = parse_curriculum("legacy.csv", (FIXTURES / "legacy_curriculum.csv").read_bytes())
    assert curriculum.columns["order"] == ["№"]
    assert curriculum.columns["block"] == ["без заголовка"]
