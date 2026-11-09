"""Шаблон учебного плана: пустой файл с теми заголовками, которые читает парсер.

Собирается из ``COLUMNS`` — из той же таблицы, по которой парсер и сопоставляет колонки.
Поэтому шаблон не может с ним разойтись: добавили синоним — шаблон остался прежним, сменили
основное имя — сменился и он. Отдельный файл, написанный руками, через месяц врал бы.

Методолог жаловался: «загрузила УП, говорит не найдены колонки, хотя они там есть». Колонки
у неё были, но названные по-своему: список синонимов закрытый, и это сознательно — подстраиваться
под произвольные заголовки значит угадывать, а угаданная колонка хуже ненайденной. Значит нужно
сказать, какого вида файл подходит, и дать его скачать.

Два формата, потому что паспорт у методологов книга, а выгрузки ходят и в CSV.
"""

from __future__ import annotations

import csv
import io

from .curriculum_columns import COLUMNS

# Без этих колонок план не прочитать или прочитать наполовину: `title` обязателен для парсера,
# остальные входят в KEY_FIELDS и без них разбор входов остаётся без основания. Порядок
# значения не имеет — парсер ищет по именам, — но в шаблоне обязательные идут первыми.
REQUIRED = ("title", "description", "outcomes", "hours")
# Что ещё читается и заметно улучшает разбор. Необязательно.
OPTIONAL = ("block", "goals", "order", "code", "cases", "skills", "audience", "tools",
            "software", "storytelling", "format", "group_size", "checking", "materials")

SHEET = "УП"

# Одна заполненная строка: образец показывает вид значения, а не предлагает его переписать.
EXAMPLE = {
    "title": "Отчёт о клиентской проблеме",
    "description": "Участник разбирает обращение клиента и готовит отчёт с выводами.",
    "outcomes": "Знает структуру отчёта; умеет отделять факты от выводов",
    "hours": "14",
    "block": "Блок 1. Работа с требованиями",
    "goals": "Научить разбирать обращения и обосновывать выводы",
    "order": "1",
    "code": "D1P1_Report",
    "cases": "1. Жалоба на сроки. 2. Спор о составе работ.",
    "skills": "Анализ требований; Деловая переписка",
    "audience": "базовый",
    "tools": "Miro, Google Docs",
    "software": "Браузер",
    "storytelling": "Клиент прислал жалобу, разобраться нужно за день.",
    "format": "Индивидуальный",
    "group_size": "—",
    "checking": "p2p",
    "materials": "Пример обращения",
}


def headers() -> list[str]:
    """Заголовки шаблона: сначала обязательные, затем остальные читаемые колонки."""
    return [COLUMNS[field][0] for field in (*REQUIRED, *OPTIONAL)]


def example_row() -> list[str]:
    return [EXAMPLE.get(field, "") for field in (*REQUIRED, *OPTIONAL)]


def accepted_names(field: str) -> str:
    """Как можно назвать колонку, через «или» — для текста отказа."""
    return " или ".join(f"«{name}»" for name in COLUMNS[field])


def template_bytes(kind: str = "xlsx") -> bytes:
    """Шаблон в байтах: ``xlsx`` — книга с вкладкой «УП», ``csv`` — та же таблица строкой."""
    if kind == "csv":
        lines = io.StringIO(newline="")
        writer = csv.writer(lines, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
        writer.writerow(headers())
        writer.writerow(example_row())
        # Метка порядка байтов: без неё Excel открывает кириллицу в CSV как набор знаков.
        return lines.getvalue().encode("utf-8-sig")
    if kind != "xlsx":
        raise ValueError(f"Шаблон бывает csv или xlsx, не {kind!r}")

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    book = Workbook()
    sheet = book.active
    assert sheet is not None  # у новой книги лист всегда есть
    sheet.title = SHEET
    sheet.append(headers())
    sheet.append(example_row())
    required_fill = PatternFill("solid", fgColor="FFF2CC")
    for index, field in enumerate((*REQUIRED, *OPTIONAL), start=1):
        cell = sheet.cell(row=1, column=index)
        cell.font = Font(bold=True)
        if field in REQUIRED:
            cell.fill = required_fill
        sheet.column_dimensions[get_column_letter(index)].width = 28
    sheet.freeze_panes = "A2"
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()
