"""Render the standard curriculum table as an editable Excel worksheet."""

import re
from collections.abc import Iterable, Sequence
from io import BytesIO
from itertools import chain
from math import ceil

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


class ExcelCellError(ValueError):
    """A curriculum cell cannot be represented in Excel without data loss."""


_WIDTHS = (28, 42, 6, 40, 60, 44, 44, 44, 34, 56, 44, 19, 13, 15, 15, 16, 12, 14, 14, 45, 36, 40)
_INVALID_XML = re.compile(r"[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")


def write_curriculum_xlsx(headers: Sequence[Sequence[object]], projects: Iterable[Sequence[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Учебный план"
    sheet.sheet_view.showGridLines = True
    for column, width in enumerate(_WIDTHS, start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width

    wrapped = Alignment(vertical="top", wrap_text=True)
    heading = PatternFill("solid", fgColor="175D51")
    heading_font = Font(name="Calibri", bold=True, color="FFFFFF")
    header_count = len(headers)
    for row_index, row in enumerate(chain(headers, projects), start=1):
        is_header = row_index <= header_count
        lines = 1
        for col, value in enumerate(row, start=1):
            cell = sheet.cell(row_index, col)
            if isinstance(value, str):
                # openpyxl would silently truncate a long cell or promote a leading "=" to a formula.
                if len(value) > 32767 or _INVALID_XML.search(value):
                    raise ExcelCellError(
                        f"Excel не может сохранить текст ячейки {cell.coordinate} целиком. "
                        "Скачайте CSV или отредактируйте текст этой ячейки."
                    )
                if not is_header and col == 13 and re.fullmatch(r"0|[1-9][0-9]{0,14}", value):
                    # Group size also accepts ranges such as "3–4".
                    cell.value = int(value)
                else:
                    cell.value = value
                    cell.data_type = "s"
                width = _WIDTHS[col - 1] if col <= len(_WIDTHS) else 20
                lines = max(lines, sum(max(1, ceil(len(line) / width)) for line in value.splitlines()))
            else:
                cell.value = value
            cell.alignment = wrapped
            if is_header:
                cell.fill = heading
                cell.font = heading_font
        # Reserve some space for wrapped prose. Longer texts remain available in the formula bar.
        sheet.row_dimensions[row_index].height = min(180, max(17, 16 * lines + 3))

    if header_count:
        sheet.freeze_panes = f"A{header_count + 1}"
        sheet.auto_filter.ref = sheet.dimensions
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
