"""Выборочная сборка РПД на реальном ДПО-шаблоне (повышение квалификации).

В отличие от полной очистки тела: идём по разделам шаблона и заменяем ТОЛЬКО те, что должны прийти
из УП / генерации / пробела; защищённые разделы-boilerplate (нормативка, документ, условия
инвалидов) оставляем как в шаблоне, даже когда профиль показывает их методисту для review.
Категорию раздела определяет профиль шаблона.
"""

from __future__ import annotations

import re
import zipfile
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from typing import Any

import docx
from docx.image.exceptions import UnrecognizedImageError
from docx.image.image import Image as DocxImage
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Inches
from docx.table import Table
from docx.text.paragraph import Paragraph

from .build import RpdBuild, RpdSection, build_rpd_sections
from .ingest import _is_heading, _split_heading
from .module_evidence import (
    PlatformEvidenceRequirement,
    evidence_id_from_cell,
    visible_evidence_text,
)
from .template_profiles import get_template_profile
from .up_models import UpPlan

_Content = tuple[list[str], list[tuple[list[str], list[list[str]]]]]

_SCHEDULE_HEADER = ("Компонент", "Период", "Часы", "Форма организации", "Контроль")
# The built-in template gives the last column too little room: Word breaks Russian
# assessment names inside a word. Keep the original total width but redistribute it
# toward the semantically important period/control columns.
_SCHEDULE_COLUMN_WIDTHS = (3300, 1350, 850, 2200, 1939)


def render_rpd_pk(up: UpPlan, template_path: str | Path | None = None, llm_client: Any = None) -> bytes:
    """Удобная обёртка: собрать черновик РПД из УП (+ проза LLM) и сразу отрендерить в DOCX."""
    build = build_rpd_sections(up, template_path, llm_client)
    return render_build(build, template_path)


def render_build(
    build: RpdBuild,
    template_path: str | Path | None = None,
    evidence_root: str | Path | None = None,
) -> bytes:
    """Кладёт (возможно отредактированный) черновик РПД в DOCX по шаблону: editable-разделы заменяет,
    boilerplate (форму ДПО) держит как в шаблоне. Комментарии вырезаются."""
    profile = get_template_profile(build.template_profile_id)
    path = profile.template_path(template_path)
    document = docx.Document(str(path))
    body = document.element.body
    sectpr = qn("w:sectPr")
    para_tag = qn("w:p")
    table_tag = qn("w:tbl")

    elements = [child for child in body.iterchildren() if child.tag != sectpr]
    spans = _section_spans(document, elements, para_tag)
    evidence_by_id = {item.evidence_id: item for item in build.module_evidence}
    resolved_evidence_root = Path(evidence_root).resolve() if evidence_root is not None else None

    for heading, number, title, body_els in spans:
        section = build.section(number, title)
        if section is None:
            continue
        if section.preserve_template and not _template_section_edited(section):
            continue  # boilerplate не тронут методистом — держим исходную вёрстку шаблона
        table_prototypes = [deepcopy(element) for element in body_els if element.tag == table_tag]
        for element in body_els:
            # A section break is stored inside a paragraph's pPr. Removing that apparently empty
            # paragraph silently collapses portrait/landscape sections in the resulting DOCX.
            if not _has_section_break(element):
                body.remove(element)
        _insert_after(
            document,
            heading,
            _content_of(section),
            table_prototypes,
            evidence_by_id=evidence_by_id,
            evidence_root=resolved_evidence_root,
        )

    # Обложка и паспорт находятся вне секций с заголовками, поэтому заполняются отдельным контрактом.
    profile.bind_document(document, build)

    buffer = BytesIO()
    document.save(buffer)
    return _drop_comments(buffer.getvalue())


def _template_section_edited(section: RpdSection) -> bool:
    """Правил ли методист template-owned раздел (иначе держим дословную вёрстку DOCX)."""
    return bool(section.template_baseline_hash) and section.content_hash() != section.template_baseline_hash


def _content_of(section: RpdSection) -> _Content:
    if section.kind == "table" and section.table_rows:
        return ([], [(section.table_header, section.table_rows)])
    if section.kind == "module_blocks" and section.module_blocks:
        tables: list[tuple[list[str], list[list[str]]]] = []
        for block in section.module_blocks:
            # The internal template defines exactly three consecutive table surfaces per module:
            # semantic passport, topic/work rows, and a full-width screenshot evidence area.
            tables.extend(
                [
                    (block.passport_header, block.passport_rows),
                    (block.topics_header, block.topics_rows),
                    (block.evidence_header, block.evidence_rows),
                ]
            )
        return ([], tables)
    return (list(section.paragraphs), [])


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


def _insert_after(
    document: Any,
    anchor: Any,
    content: _Content,
    table_prototypes: list[Any] | None = None,
    *,
    evidence_by_id: dict[str, PlatformEvidenceRequirement] | None = None,
    evidence_root: Path | None = None,
) -> None:
    paragraphs, tables = content
    for text in paragraphs:
        paragraph = document.add_paragraph(text)
        anchor.addnext(paragraph._p)
        anchor = paragraph._p
    for table_index, (header, rows) in enumerate(tables):
        prototype = _matching_table_prototype(document, table_prototypes or [], max(len(header), 1))
        grid = _build_table(
            document,
            header,
            rows,
            prototype,
            evidence_by_id=evidence_by_id,
            evidence_root=evidence_root,
        )
        anchor.addnext(grid._tbl)
        anchor = grid._tbl
        if table_index < len(tables) - 1:
            # A real paragraph keeps adjacent cloned tables as separate Word objects and gives
            # each module block the same visual rhythm as the source template.
            spacer = document.add_paragraph()
            anchor.addnext(spacer._p)
            anchor = spacer._p


def _build_table(
    document: Any,
    header: list[str],
    rows: list[list[str]],
    prototype: Any = None,
    *,
    evidence_by_id: dict[str, PlatformEvidenceRequirement] | None = None,
    evidence_root: Path | None = None,
) -> Any:
    ncols = max(len(header), max((len(r) for r in rows), default=0), 1)
    if prototype is not None:
        # Clone the template table so widths, borders, shading, alignment and fonts survive.
        table_element = deepcopy(prototype)
        existing_rows = list(table_element.tr_lst)
        if existing_rows:
            data_prototype = deepcopy(existing_rows[1] if len(existing_rows) > 1 else existing_rows[0])
            for row_element in existing_rows[1:]:
                table_element.remove(row_element)
            for _row in rows:
                table_element.append(deepcopy(data_prototype))
            grid = Table(table_element, document)
            _populate_table(
                grid,
                header,
                rows,
                ncols,
                evidence_by_id=evidence_by_id,
                evidence_root=evidence_root,
            )
            _apply_contract_layout(grid, header)
            return grid

    grid = document.add_table(rows=len(rows) + 1, cols=ncols)
    try:
        grid.style = "Table Grid"
    except (KeyError, ValueError):
        pass  # стиля может не быть в каркасе — границы всё равно ставим явно
    _set_grid_borders(grid)
    _populate_table(
        grid,
        header,
        rows,
        ncols,
        evidence_by_id=evidence_by_id,
        evidence_root=evidence_root,
    )
    _apply_contract_layout(grid, header)
    return grid


def _apply_contract_layout(grid: Any, header: list[str]) -> None:
    """Apply narrow, section-specific layout rules after cloning the template table."""
    if tuple(header) != _SCHEDULE_HEADER:
        return

    # Fixed layout makes the explicit grid widths authoritative in Word/LibreOffice.
    table_properties = grid._tbl.tblPr
    layout = table_properties.find(qn("w:tblLayout"))
    if layout is None:
        layout = OxmlElement("w:tblLayout")
        table_properties.append(layout)
    layout.set(qn("w:type"), "fixed")

    for grid_column, width in zip(grid._tbl.tblGrid.gridCol_lst, _SCHEDULE_COLUMN_WIDTHS, strict=True):
        grid_column.set(qn("w:w"), str(width))
    for row in grid.rows:
        for cell, width in zip(row.cells, _SCHEDULE_COLUMN_WIDTHS, strict=True):
            cell_properties = cell._tc.get_or_add_tcPr()
            cell_width = cell_properties.get_or_add_tcW()
            cell_width.set(qn("w:w"), str(width))
            cell_width.set(qn("w:type"), "dxa")


def _matching_table_prototype(document: Any, prototypes: list[Any], ncols: int) -> Any | None:
    """Pick the table in a section whose visible schema matches generated content."""
    for prototype in prototypes:
        table = Table(prototype, document)
        if table.rows and len(table.rows[0].cells) == ncols:
            return prototype
    return None


def _populate_table(
    grid: Any,
    header: list[str],
    rows: list[list[str]],
    ncols: int,
    *,
    evidence_by_id: dict[str, PlatformEvidenceRequirement] | None = None,
    evidence_root: Path | None = None,
) -> None:
    for col in range(ncols):
        _replace_cell_text(grid.rows[0].cells[col], header[col] if col < len(header) else "")
    for row_index, row in enumerate(rows, start=1):
        for col in range(ncols):
            _replace_cell_text(grid.rows[row_index].cells[col], row[col] if col < len(row) else "")
    _populate_evidence_rows(
        grid,
        rows,
        ncols,
        evidence_by_id=evidence_by_id or {},
        evidence_root=evidence_root,
    )


def _populate_evidence_rows(
    grid: Any,
    rows: list[list[str]],
    ncols: int,
    *,
    evidence_by_id: dict[str, PlatformEvidenceRequirement],
    evidence_root: Path | None,
) -> None:
    """Merge evidence rows and insert uploaded screenshots at a readable size."""
    for row_index, source_row in enumerate(rows, start=1):
        evidence_cell = next(
            (
                (column, value, evidence_id)
                for column, value in enumerate(source_row)
                if (evidence_id := evidence_id_from_cell(value)) is not None
            ),
            None,
        )
        if evidence_cell is None:
            continue
        column, source_text, evidence_id = evidence_cell
        row_cells = grid.rows[row_index].cells
        target_cell = row_cells[column]
        if column < ncols - 1:
            # Screenshot evidence is a full-width document block, not a cramped data cell.
            target_cell = target_cell.merge(row_cells[ncols - 1])

        requirement = evidence_by_id.get(evidence_id)
        image_path = _evidence_image_path(requirement, evidence_root)
        if requirement is not None and requirement.status == "uploaded" and image_path is None:
            visible_text = (
                f"[Методист: файл скриншота {requirement.kind_label} недоступен; загрузите его повторно]"
            )
        else:
            visible_text = visible_evidence_text(source_text)
        _replace_cell_text(target_cell, visible_text)
        if image_path is not None:
            try:
                _append_screenshot(target_cell, image_path)
            except (OSError, UnrecognizedImageError, ValueError):
                # A file may disappear or become corrupt after upload; degrade to a blocking
                # human-readable marker instead of failing the entire draft export.
                kind = requirement.kind_label if requirement is not None else "доказательства"
                _replace_cell_text(
                    target_cell,
                    f"[Методист: файл скриншота {kind} повреждён; загрузите его повторно]",
                )


def _evidence_image_path(
    requirement: PlatformEvidenceRequirement | None,
    evidence_root: Path | None,
) -> Path | None:
    """Resolve only generated relative paths below the run directory."""
    if (
        requirement is None
        or requirement.status != "uploaded"
        or not requirement.relative_path
        or evidence_root is None
    ):
        return None
    candidate = (evidence_root / requirement.relative_path).resolve()
    try:
        candidate.relative_to(evidence_root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _append_screenshot(cell: Any, path: Path) -> None:
    """Insert a screenshot without overflowing the printable page area."""
    image = DocxImage.from_file(str(path))
    max_width = Inches(6.5)
    max_height = Inches(8)
    scale = min(float(max_width) / image.width, float(max_height) / image.height, 1.0)
    width = Emu(round(image.width * scale))
    height = Emu(round(image.height * scale))
    paragraph = cell.add_paragraph()
    paragraph.add_run().add_picture(str(path), width=width, height=height)


def _replace_cell_text(cell: Any, text: str) -> None:
    """Replace text while retaining the template paragraph and first-run formatting."""
    paragraph = cell.paragraphs[0]
    if paragraph.runs:
        paragraph.runs[0].text = text
        for run in paragraph.runs[1:]:
            run.text = ""
    else:
        paragraph.add_run(text)
    for extra in cell.paragraphs[1:]:
        for run in extra.runs:
            run.text = ""


def _has_section_break(element: Any) -> bool:
    return any(node.tag == qn("w:sectPr") for node in element.iter())


def _set_grid_borders(grid: Any) -> None:
    """Явные одинарные границы: шаблонного стиля «Table Grid» может не быть → иначе таблица без рамок."""
    tbl_pr = grid._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), "4")
        element.set(qn("w:space"), "0")
        element.set(qn("w:color"), "000000")
        borders.append(element)
    tbl_pr.append(borders)
