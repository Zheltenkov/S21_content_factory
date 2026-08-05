"""Выборочная сборка РПД на реальном ДПО-шаблоне (повышение квалификации).

В отличие от полной очистки тела: идём по разделам шаблона и заменяем ТОЛЬКО те, что должны прийти
из УП / генерации / пробела; разделы-boilerplate (нормативка, документ, условия инвалидов) оставляем
как в шаблоне. Категорию раздела определяет :func:`dpo_spec.category_for`.
"""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

import docx
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from .dpo_spec import Cat, category_for
from .ingest import _is_heading, _split_heading
from .prose import generate_prose
from .template import default_pk_skeleton_path
from .transfer import GAP_MARKER
from .up_models import UpPlan

_GENERATE_PLACEHOLDER = "[Будет сгенерировано]"
_PLATFORM_PLACEHOLDER = "[Вставьте скрин задания с платформы/LMS]"

_Content = tuple[list[str], list[tuple[list[str], list[list[str]]]]]


def render_rpd_pk(up: UpPlan, template_path: str | Path | None = None, llm_client: Any = None) -> bytes:
    """Собирает РПД на ДПО-шаблоне ПК: УП-разделы заполняет, прозу генерирует (если дан ``llm_client``),
    boilerplate держит, прочее помечает."""
    path = Path(template_path) if template_path else default_pk_skeleton_path()
    document = docx.Document(str(path))
    body = document.element.body
    sectpr = qn("w:sectPr")
    para_tag = qn("w:p")

    elements = [child for child in body.iterchildren() if child.tag != sectpr]
    spans = _section_spans(document, elements, para_tag)

    for heading, number, title, body_els in spans:
        category = category_for(number, title)
        # BOILERPLATE (в т.ч. заголовки-контейнеры разделов по умолчанию) держим как в шаблоне.
        # Fill-категории вставляем даже в контейнер (напр. «3.3 Рабочие программы» перед подразделом 3.3.1).
        if category is Cat.BOILERPLATE:
            continue
        content = _content_for(category, up, title, llm_client)
        for element in body_els:
            body.remove(element)
        _insert_after(document, heading, content)

    buffer = BytesIO()
    document.save(buffer)
    return _drop_comments(buffer.getvalue())


# Части-комментарии и их разметка, которые нельзя оставлять в готовом РПД (в шаблоне это заметки методиста).
_COMMENT_PARTS = {
    "word/comments.xml",
    "word/commentsExtended.xml",
    "word/commentsIds.xml",
    "word/commentsExtensible.xml",
    "word/people.xml",
}
_COMMENT_ELEMENT_RE = re.compile(r"<w:comment(?:RangeStart|RangeEnd|Reference)\b[^>]*/>")
_COMMENT_OVERRIDE_RE = re.compile(r"<Override\b[^>]*comments[^>]*/>")
_COMMENT_REL_RE = re.compile(r"<Relationship\b[^>]*comments[^>]*/>")


def _drop_comments(data: bytes) -> bytes:
    """Убирает все комментарии из DOCX: части comments*.xml, их привязки в тексте, rels и content-types."""
    source = zipfile.ZipFile(BytesIO(data))
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            if item.filename in _COMMENT_PARTS:
                continue
            payload = source.read(item.filename)
            if item.filename == "word/document.xml":
                payload = _COMMENT_ELEMENT_RE.sub("", payload.decode("utf-8")).encode("utf-8")
            elif item.filename == "[Content_Types].xml":
                payload = _COMMENT_OVERRIDE_RE.sub("", payload.decode("utf-8")).encode("utf-8")
            elif item.filename.endswith("document.xml.rels"):
                payload = _COMMENT_REL_RE.sub("", payload.decode("utf-8")).encode("utf-8")
            target.writestr(item, payload)
    return out.getvalue()


def _section_spans(document: Any, elements: list[Any], para_tag: str) -> list[tuple[Any, str, str, list[Any]]]:
    spans: list[tuple[Any, str, str, list[Any]]] = []
    heading: Any = None
    number = title = ""
    body_els: list[Any] = []

    def flush() -> None:
        if heading is not None:
            spans.append((heading, number, title, body_els))

    for element in elements:
        if element.tag == para_tag and _is_heading(Paragraph(element, document)):
            flush()
            heading = element
            number, title = _split_heading(Paragraph(element, document).text.strip())
            body_els = []
        elif heading is not None:
            body_els.append(element)
    flush()
    return spans


def _content_for(category: Cat, up: UpPlan, title: str, llm_client: Any) -> _Content:
    if category is Cat.GENERATE:
        if llm_client is not None:
            paragraphs = generate_prose(title, up, llm_client)
            if paragraphs:
                return (paragraphs, [])
        return ([_GENERATE_PLACEHOLDER], [])
    if category is Cat.PLATFORM:
        return ([_PLATFORM_PLACEHOLDER], [])
    if category is Cat.UP_PLAN:
        return _up_or_gap(_uchebnyi_plan(up))
    if category is Cat.UP_GRAFIK:
        return _up_or_gap(_grafik(up))
    if category is Cat.UP_MODULES:
        return _up_or_gap(_modules(up))
    if category is Cat.UP_COMP:
        return _up_or_gap(_competencies(up))
    return ([GAP_MARKER], [])  # Cat.GAP


def _up_or_gap(table: tuple[list[str], list[list[str]]] | None) -> _Content:
    return ([], [table]) if table is not None else ([GAP_MARKER], [])


def _uchebnyi_plan(up: UpPlan) -> tuple[list[str], list[list[str]]] | None:
    """Учебный план (внутренние школы — всё практическое: часы в «Практические»)."""
    if not up.modules:
        return None
    header = ["№", "Наименование модуля", "Всего, ч", "Лекции", "Практические", "Самостоятельная", "Контроль"]
    rows = [[str(i), m.title, m.hours, "", m.hours, "", m.control()] for i, m in enumerate(up.modules, start=1)]
    return (header, rows)


def _grafik(up: UpPlan) -> tuple[list[str], list[list[str]]] | None:
    if not up.modules:
        return None
    header = ["Компонент", "Период", "Часы", "Форма организации", "Контроль"]
    rows = [[m.title, "", m.hours, m.fmt(), m.control()] for m in up.modules]
    return (header, rows)


def _modules(up: UpPlan) -> tuple[list[str], list[list[str]]] | None:
    """Рабочие программы модулей — построчно по проектам (тема+содержание, вид работы, часы, контроль)."""
    if not up.modules:
        return None
    header = ["№", "Тема и краткое содержание", "Вид работы", "Часы", "Методы", "Контроль"]
    rows: list[list[str]] = []
    index = 1
    for module in up.modules:
        for row in module.rows:
            theme = f"[{module.title}] {row.name}".strip()
            if row.description:
                theme = f"{theme}: {row.description}"
            rows.append([str(index), theme, row.fmt, row.hours, "", row.control()])
            index += 1
    return (header, rows) if rows else None


def _competencies(up: UpPlan) -> tuple[list[str], list[list[str]]] | None:
    """Полная 6-колоночная таблица; «после обучения» = умеет+навык из УП, основание/уровень — методисту."""
    comp_rows = up.competency_rows()
    if not comp_rows:
        return None
    header = ["Код", "Профессиональная компетенция", "Исходный уровень", "Качественное изменение", "После обучения", "Основание"]
    rows = [[code, name, "", "", after, ""] for code, name, after in comp_rows]
    return (header, rows)


def _insert_after(document: Any, anchor: Any, content: _Content) -> None:
    paragraphs, tables = content
    for text in paragraphs:
        paragraph = document.add_paragraph(text)
        anchor.addnext(paragraph._p)
        anchor = paragraph._p
    for header, rows in tables:
        grid = _build_table(document, header, rows)
        anchor.addnext(grid._tbl)
        anchor = grid._tbl


def _build_table(document: Any, header: list[str], rows: list[list[str]]) -> Any:
    ncols = max(len(header), max((len(r) for r in rows), default=0), 1)
    grid = document.add_table(rows=len(rows) + 1, cols=ncols)
    try:
        grid.style = "Table Grid"
    except (KeyError, ValueError):
        pass
    for col in range(ncols):
        grid.rows[0].cells[col].text = header[col] if col < len(header) else ""
    for row_index, row in enumerate(rows, start=1):
        for col in range(ncols):
            grid.rows[row_index].cells[col].text = row[col] if col < len(row) else ""
    return grid
