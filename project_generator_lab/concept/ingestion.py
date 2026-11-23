"""Read uploaded references without executing them; preserve source locations."""

import csv
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Sequence
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal, NamedTuple

from docx import Document
from openpyxl import Workbook, load_workbook
from openpyxl.worksheet._read_only import ReadOnlyWorksheet
from pydantic import ValidationError
from pypdf import PdfReader

from . import skills
from .curriculum_columns import COLUMNS
from .models import Curriculum, CurriculumRow, Material, Passage, SkillBlock

MAX_FILE_BYTES = 4 * 1024 * 1024
# Declared sheet size (rows × columns): a real task sheet is far below; a crafted one burns minutes of CPU.
MAX_XLSX_CELLS = 300_000
# Fields whose absence changes the brief; reported by name instead of left silently empty.
KEY_FIELDS = {"description": "Краткое описание", "outcomes": "Образовательные результаты",
              "hours": "Трудоёмкость", "audience": "Уровень аудитории"}
# The standard passport keeps the audience level in the customer's brief and the programme concept, never in the plan:
# «колонка не найдена» appeared for every plan and said nothing (checked on «Паспорт программы_Батон» 05.10.2026).
# A plan that has the column and left it empty is still told so.
NORMALLY_ABSENT = {"audience"}
ACADEMIC_HOUR = Decimal("0.75")  # 45 minutes
# A plan tab starts with its header: a tab whose first rows are empty is not one.
HEADER_SEARCH_ROWS = 50
# What is read of a plan tab: a workbook can declare any size or none, so the rows actually read are counted. A plan
# has gaps (the second day of a two-day project), never hundreds of empty lines in a row.
MAX_PLAN_ROWS = 5000
EMPTY_RUN = 200
# A passport has two or three skills tabs for its plan (the current one, an old one); more are not offered.
MAX_SKILL_TABS = 10
MAX_CATALOG = 1000
MAX_NAMED_CODES = 10
# What the skills of a workbook may weigh in a plan, in characters: the plan goes back to the server whole with every
# start request, and that request has a limit of its own (StartRequest.input_limits).
MAX_BLOCKS_SIZE, MAX_NOTES_SIZE = 250_000, 50_000
NOT_OFFERED = {"false", "ложь", "нет", "0"}
# A cell that is only a dash: how a plan writes «нет».
NOTHING = re.compile(r"[-‒–—−]+")
# The fields that say what a line is; a dash there is taken as written, as it always was.
STRUCTURE = {"block", "goals", "order", "title"}
EXAM_FORMAT = re.compile(r"экзамен|exam", re.I)
# «Финальный экзамен (тестирование)», «Экзамен_1», but not «Экзаменационный тренажёр» or «Подготовка к экзамену».
EXAM_TITLE = re.compile(r"(?<![а-яёa-z])экзамен(?![а-яёa-z])", re.I)
TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".py", ".js", ".ts", ".sql", ".sh", ".ps1", ".html", ".ipynb"}


class InputError(ValueError):
    """Readable error for malformed or unsupported input."""


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise InputError("Не удалось определить кодировку. Сохраните текст в UTF-8.")


def check_file(name: str, data: bytes) -> None:
    if not data:
        raise InputError(f"Файл {name} пуст")
    if len(data) > MAX_FILE_BYTES:
        raise InputError("Максимальный размер файла — 4 МБ")
    if Path(name).suffix.lower() in {".docx", ".xlsx"}:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if sum(f.file_size for f in archive.infolist()) > 30 * 1024 * 1024:
                    raise InputError("Слишком большой распакованный документ")
        except zipfile.BadZipFile as exc:
            raise InputError("Повреждённый документ Office") from exc


def normalize_header(value: str) -> str:
    return re.sub(r"[^а-яa-z0-9]", "", value.casefold().replace("ё", "е"))


TITLES = {normalize_header(alias) for alias in COLUMNS["title"]}
GROUP_SIZES = {normalize_header(alias) for alias in COLUMNS["group_size"]}


def row_kind(project_format: str, title: str) -> Literal["project", "exam"]:
    """An exam when the plan's «Формат» is «экзамен», the whole cell. When the plan states no format, a title with the
    word «экзамен» decides — a guess, reported in the import warnings; a stated format is the plan's own word and is
    never overridden by the title."""
    if project_format:
        return "exam" if EXAM_FORMAT.fullmatch(project_format.strip()) else "project"
    return "exam" if EXAM_TITLE.search(title) else "project"


def astronomical(academic: str) -> str:
    """Academic hours (45 minutes) as astronomical ones, to the hundredth; "" when the cell is not a positive number."""
    try:
        value = Decimal(academic.replace(",", ".").strip())
    except InvalidOperation:
        return ""
    if not value.is_finite() or value <= 0:
        return ""
    hours = (value * ACADEMIC_HOUR).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(hours.normalize(), "f")


def outcome_lines(text: str) -> str:
    """One outcome per line: outcomes are numbered by lines. Some plans list them in one cell
    separated by «;» — that is several outcomes, not one."""
    if "\n" in text.strip() or ";" not in text:
        return text
    return "\n".join(part.strip() for part in text.split(";") if part.strip())


def csv_records(data: bytes) -> list[list[str]]:
    text = decode_text(data)
    # Fall back to the header when multiline cells defeat Sniffer's sample.
    try:
        dialect = csv.Sniffer().sniff(text[:16000], delimiters=",;\t")
        delimiter = dialect.delimiter
    except csv.Error:
        header = next((line for line in text.splitlines() if line.strip()), "")
        delimiter = max((",", ";", "\t"), key=header.count)
    # Sniffer can prefer commas inside a semicolon row. Confirm against the
    # recognized title column before accepting its delimiter guess.
    for candidate in dict.fromkeys((delimiter, ",", ";", "\t")):
        candidate_header = next((r for r in csv.reader(io.StringIO(text), delimiter=candidate) if any(c.strip() for c in r)), [])
        if any(normalize_header(column) in TITLES for column in candidate_header):
            delimiter = candidate
            break
    try:
        return list(csv.reader(io.StringIO(text), delimiter=delimiter, strict=True))
    except csv.Error as exc:
        raise InputError("Не удалось прочитать CSV. Проверьте разделитель и заголовки.") from exc


def cell_text(value: object, as_range: bool) -> str:
    """A workbook cell as text: a whole number without «.0» (a workbook stores every number as a float), a date as a
    date. as_range: the cell holds a range of numbers («2-4» people in a group), which a spreadsheet stores as a date
    (2 April) — written back as the range, smaller number first."""
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return f"{min(value.day, value.month)}-{max(value.day, value.month)}" if as_range else value.strftime("%d.%m.%Y")
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return str(value)


def blank(cells: Sequence[object]) -> bool:
    return not any(str(c).strip() for c in cells if c is not None)


class SkillsTab(NamedTuple):
    title: str
    visible: bool
    records: list[list[str]]


class Book(NamedTuple):
    """What a passport workbook gives: the tab with the projects, the platform's catalogue of skills, and every tab
    laid out as a skills tab («Проект», «Задание», a column per skill) — which of them belongs to this plan is decided
    by the project codes, when the plan has been read. notes — what could not be read of the tabs beside the plan;
    unread — whether a skills tab is among what was not read."""

    tab: str
    records: list[list[str]]
    catalog: list[str]
    skill_tabs: list[SkillsTab]
    notes: list[str]
    unread: bool


def tab_header(sheet: ReadOnlyWorksheet) -> tuple[Sequence[object], int]:
    """The first filled line among the first lines of a tab, and the number of its columns: cells to the right of the
    last header have no column to belong to."""
    # The size a workbook declares for a tab can be stale or absent: the rows themselves are read.
    sheet.reset_dimensions()
    header = next((cells for cells in sheet.iter_rows(max_row=HEADER_SEARCH_ROWS, values_only=True) if not blank(cells)), ())
    return header, max((i + 1 for i, c in enumerate(header) if c is not None and str(c).strip()), default=0)


def tab_rows(sheet: ReadOnlyWorksheet, width: int) -> list[list[object]]:
    """The lines of a tab up to its end: a table has gaps, never hundreds of empty lines in a row."""
    table: list[list[object]] = []
    empty = 0
    for cells in sheet.iter_rows(values_only=True, max_col=width):
        empty = empty + 1 if blank(cells) else 0
        if empty > EMPTY_RUN:
            break  # the table has ended; whatever a workbook keeps far below is not read
        table.append((list(cells) + [None] * width)[:width])
        if len(table) > MAX_PLAN_ROWS:
            raise InputError(f"На вкладке «{sheet.title}» больше {MAX_PLAN_ROWS} строк. "
                             "Скопируйте строки учебного плана на новую вкладку и загрузите книгу снова.")
    return table


def plan_tab(workbook: Workbook) -> tuple[ReadOnlyWorksheet, int]:
    """The tab with the projects and the number of its columns, found by its header and not by its name or place in
    the workbook. Hidden tabs of a passport are forms and samples: they count only when no visible tab has projects.
    Several candidates are never chosen between: the methodologist is told which they are."""
    found = []
    for sheet in workbook.worksheets:
        header, width = tab_header(sheet)
        if any(normalize_header(str(c)) in TITLES for c in header if c is not None):
            found.append((sheet, width))
    candidates = [f for f in found if f[0].sheet_state == "visible"] or found
    if not candidates:
        tabs = ", ".join(f"«{s.title}»" for s in workbook.worksheets)[:600]
        raise InputError(f"В книге нет вкладки с колонкой «Название проекта». Вкладки: {tabs}. "
                         "Проверьте заголовки вкладки с учебным планом или выгрузите её в CSV.")
    if len(candidates) > 1:
        tabs = ", ".join(f"«{s.title}»" for s, _ in candidates)
        raise InputError(f"В книге несколько вкладок с проектами: {tabs}. Выгрузите нужную в CSV и загрузите её.")
    return candidates[0]


def catalog_names(records: list[list[str]]) -> list[str]:
    """The names under «Skills» on the catalogue tab. The column after it, when it has no header of its own, says
    whether the platform offers the skill: a skill marked there as not offered is not in the catalogue. A column
    with a header is something else (a description, a count) and decides nothing."""
    header = next(record for record in records if any(cell.strip() for cell in record))
    column = next(i for i, cell in enumerate(header) if skills.key(cell) == "skills")
    flagged = column + 1 >= len(header) or not header[column + 1].strip()
    names: dict[str, str] = {}
    for record in records[records.index(header) + 1:]:
        name = " ".join(record[column].split()) if column < len(record) else ""
        offered = record[column + 1].strip() if flagged and column + 1 < len(record) else ""
        if name and len(name) <= skills.MAX_NAME and skills.key(offered) not in NOT_OFFERED:
            names.setdefault(skills.key(name), name)
    return list(names.values())


def skill_tabs_of(workbook: Workbook, plan: ReadOnlyWorksheet) -> tuple[list[str], list[SkillsTab], list[str], bool]:
    """The catalogue, the skills tabs of a workbook, what could not be read of them, and whether a skills tab is among
    what was not read. A tab beside the plan never refuses the plan: one that is too long is left out and named."""
    catalogs: list[SkillsTab] = []
    tabs: list[SkillsTab] = []
    notes: list[str] = []
    unread = False
    for sheet in workbook.worksheets:
        if sheet is plan:
            continue
        header, width = tab_header(sheet)
        names = [str(c) for c in header if c is not None]
        laid_out = skills.is_skills_header(names)
        if not laid_out and not any(skills.key(name) == "skills" for name in names):
            continue
        try:
            # On the catalogue tab the column that says whether a skill is offered stands after «Skills» without a header.
            table = tab_rows(sheet, width if laid_out else width + 1)
        except InputError:
            notes.append(f"Вкладка «{sheet.title.strip()}» не прочитана: в ней больше {MAX_PLAN_ROWS} строк.")
            unread = unread or laid_out
            continue
        records = [[cell_text(c, False) for c in cells] for cells in table]
        (tabs if laid_out else catalogs).append(SkillsTab(sheet.title.strip(), sheet.sheet_state == "visible", records))
    candidates = [c for c in catalogs if c.visible] or catalogs
    if len(candidates) > 1:
        # A wrong catalogue would call every real skill a stranger: between several nothing is chosen, as with plans.
        notes.append("В книге несколько вкладок с колонкой «Skills»: " + ", ".join(f"«{c.title}»" for c in candidates)
                     + ". Справочник навыков не взят ни с одной — названия не сверены.")
    catalog = catalog_names(candidates[0].records) if len(candidates) == 1 else []
    if len(catalog) > MAX_CATALOG:
        notes.append(f"В справочнике навыков больше {MAX_CATALOG} названий: взяты первые {MAX_CATALOG}.")
    return catalog[:MAX_CATALOG], tabs, notes, unread


def read_book(data: bytes) -> Book:
    """A passport workbook as text records, the same shape a CSV gives."""
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            sheet, width = plan_tab(workbook)
            table = tab_rows(sheet, width)
            catalog, skill_tabs, notes, unread = skill_tabs_of(workbook, sheet)
        finally:
            workbook.close()
    except (InputError, MemoryError):
        raise  # a readable refusal stays as it is; memory is the isolated process's to report
    except Exception as exc:
        raise InputError("Не удалось прочитать книгу XLSX. Сохраните её заново или выгрузите вкладку учебного плана в CSV.") from exc
    header = next(cells for cells in table if not blank(cells))  # plan_tab found it: the tab has one
    sizes = {i for i, c in enumerate(header) if c is not None and normalize_header(str(c)) in GROUP_SIZES}
    records = [[cell_text(c, i in sizes) for i, c in enumerate(cells)] for cells in table]
    return Book(sheet.title.strip(), records, catalog, skill_tabs, notes, unread)


def attach_skills(book: Book, rows: list[CurriculumRow], chosen: str) -> tuple[list[str], str, list[SkillBlock], list[str]]:
    """The skills of the plan's projects from the workbook's skills tab: (tabs to choose from, the tab read, its
    blocks, what to tell the methodologist). A tab belongs to this plan when it has a block of one of its projects, by
    code; the exams' tab of a passport does not. Among the tabs that belong, hidden ones count only when no visible
    one does. Between several nothing is chosen for the methodologist."""
    notes = list(book.notes)
    if not book.skill_tabs:
        # Said once: a skills tab left out for its length has already been named.
        return [], "", [], notes if book.unread else [*notes, "В книге нет вкладки развесовки (колонки «Проект» и «Задание»): навыки не прочитаны."]
    projects = [row for row in rows if row.kind == "project"]
    codes = {skills.code_of(row.code) for row in projects if row.code}
    if not codes:
        return [], "", [], [*notes, "У проектов УП нет кода (колонка «Название проекта на платформе и в Gitlab»): "
                                    "вкладки развесовки не с чем связать, навыки не прочитаны."]
    # By place in the workbook, not by title: two titles can differ by nothing but a space at the end.
    parsed = [skills.parse_blocks(tab.records) for tab in book.skill_tabs]
    own = [i for i, found in enumerate(parsed) if any(block.code in codes for block in found)]
    offered = [i for i in own if book.skill_tabs[i].visible] or own
    if not offered:
        return [], "", [], [*notes, "На вкладках развесовки нет ни одного проекта этого УП по коду: навыки не прочитаны."]
    if len(offered) > MAX_SKILL_TABS:
        notes.append(f"Вкладок развесовки этого УП больше {MAX_SKILL_TABS}: на выбор даны первые {MAX_SKILL_TABS}.")
        offered = offered[:MAX_SKILL_TABS]
    tabs = [book.skill_tabs[i].title for i in offered]
    at = offered[0] if len(offered) == 1 else next((i for i in offered if book.skill_tabs[i].title == chosen.strip()), None)
    if at is None:
        return tabs, "", [], [*notes, "В книге несколько вкладок развесовки: " + ", ".join(f"«{t}»" for t in tabs)
                                      + ". Выберите вкладку — навыки проектов возьмутся из неё."]
    tab = book.skill_tabs[at].title
    by_code: dict[str, SkillBlock] = {}
    found: list[str] = []
    for block in parsed[at]:
        if block.code not in by_code:
            by_code[block.code] = block
        # Of this plan's projects only: the codes of other programmes on the tab are counted below, not gone through.
        elif block.code in codes and (twice := f"Развесовка «{block.code}»: код встречается на вкладке дважды, взят первый блок.") not in found:
            found.append(twice)
    known = {skills.code_of(row.code) for row in rows if row.code}
    strangers = [code for code in by_code if code not in known]
    # Without a catalogue the names are not compared.
    strange = skills.strangers((name for block in by_code.values() for name in skills.skill_names(block)), book.catalog) if book.catalog else {}
    blocks: list[SkillBlock] = []
    for row in projects:
        block_of_row = by_code.get(skills.code_of(row.code))
        if block_of_row is None:
            continue
        if not row.skills:  # a plan that names its skills itself keeps its own
            row.skills = "\n".join(skills.skill_names(block_of_row))
        row.bonus = bool(block_of_row.bonus.shares or block_of_row.bonus.tasks)
        if block_of_row in blocks:
            continue  # two lines of the plan with one code: the block is one
        block_of_row.issues = skills.block_issues(block_of_row, strange)
        blocks.append(block_of_row)
        if block_of_row.issues:
            found.append(f"Развесовка «{block_of_row.code}»: " + "; ".join(block_of_row.issues) + ".")
    given = sum(bool(skills.skill_names(by_code[code])) for row in projects if (code := skills.code_of(row.code)) in by_code)
    summary = f"Развесовка — вкладка «{tab}»: навыки у {given} из {len(projects)} проектов" + (
        f"; в справочнике навыков {len(book.catalog)}." if book.catalog else "; справочника навыков в книге нет — названия не сверены.")
    without = [row.code or row.title for row in projects if skills.code_of(row.code) not in by_code]
    if without:
        found.append("Без развесовки: " + ", ".join(without) + ".")
    if strangers:
        # A tab can hold the blocks of other programmes: a few codes are named, the rest are counted.
        more = f" и ещё {len(strangers) - MAX_NAMED_CODES}" if len(strangers) > MAX_NAMED_CODES else ""
        found.append("На вкладке развесовки есть коды, которых нет в УП: " + ", ".join(strangers[:MAX_NAMED_CODES]) + more + ".")
    return tabs, tab, *within_limits(blocks, [*notes, summary, *found])


def within_limits(blocks: list[SkillBlock], notes: list[str]) -> tuple[list[SkillBlock], list[str]]:
    """The blocks and the notes a plan can carry. A plan is sent back whole with every start request: a tab that made
    it heavier than that request may be would load and then never start a project."""
    kept: list[str] = []
    for note in notes:
        if sum(map(len, kept)) + len(note) > MAX_NOTES_SIZE:
            kept.append(f"Заметок о развесовке слишком много, чтобы показать все: не показано {len(notes) - len(kept)}.")
            break
        kept.append(note)
    if sum(len(block.model_dump_json()) for block in blocks) > MAX_BLOCKS_SIZE:
        return [], [*kept, "Доли развесовки с планом не сохранены: вкладка слишком большая. Названия навыков проектов из неё взяты."]
    return blocks, kept


def parse_curriculum(name: str, data: bytes, skills_tab: str = "") -> Curriculum:
    """skills_tab — the skills tab the methodologist chose, when the workbook has several for this plan."""
    check_file(name, data)
    warnings: list[str] = []
    book: Book | None = None
    if Path(name).suffix.lower() != ".xlsx" and data[:4] == b"PK\x03\x04":
        raise InputError("Это не CSV. Загрузите учебный план как CSV или как книгу XLSX.")
    if Path(name).suffix.lower() == ".xlsx":
        book = read_book(data)
        tab, records = book.tab, book.records
        inside, order_of = f"На вкладке «{tab}»", "строк вкладки"
        warnings.append(f"Учебный план прочитан с вкладки «{tab}».")
    else:
        records, inside, order_of = csv_records(data), "В CSV", "записей в CSV"
    header_index = next((i for i, record in enumerate(records) if any(cell.strip() for cell in record)), None)
    if header_index is None:
        raise InputError(f"{inside} нет заголовков и проектов")
    columns = records[header_index]
    # Use column indexes: DictReader overwrites repeated outcome headers.
    indexes = {field: [i for i, column in enumerate(columns) if normalize_header(column) in
                      {normalize_header(alias) for alias in aliases}] for field, aliases in COLUMNS.items()}
    # The number sign must not collide with the legacy blank block header.
    named_order = {normalize_header(alias) for alias in COLUMNS["order"] if alias != "№"}
    indexes["order"] = [i for i, column in enumerate(columns)
                        if column.strip() == "№" or (normalize_header(column) and normalize_header(column) in named_order)]
    if not columns[0].strip():
        indexes["block"] = [0]
    if not indexes["title"]:
        # Называем и то, что нашли, и то, что принимаем: методолог жаловался «колонки есть, а
        # не находит» — колонки у него были, но названные по-своему. Список синонимов закрытый
        # сознательно, поэтому отказ обязан научить, какого вида файл подходит.
        from .template import accepted_names

        found = ", ".join(f"«{c.strip()}»" for c in columns if c.strip())[:600] or "заголовков нет"
        raise InputError(f"Не найдена колонка с названием проекта. В файле: {found}. "
                         f"Колонка должна называться {accepted_names('title')}. "
                         "Переименуйте её или заполните шаблон УП — его можно скачать на странице загрузки.")

    def value(record: list[str], field: str) -> str:
        # A lone dash is how a plan writes «нет» (group size of an individual project, cases of an exam).
        cells = (record[i].strip() for i in indexes[field] if i < len(record))
        return "\n".join(cell for cell in cells if cell and (field in STRUCTURE or not NOTHING.fullmatch(cell)))

    # The module counts in astronomical hours. Academic ones are read only when the plan gives no others — no such
    # column, or not one cell filled in it; next to astronomical hours they stay among the columns that are not used.
    if any(value(record, "hours") for record in records[header_index + 1:]):
        indexes["hours_academic"] = []
    elif indexes["hours_academic"]:
        warnings.append("Часы взяты из колонки академических часов и пересчитаны в астрономические (1 ак. ч = 45 минут).")

    # Preserve every original cell, including headers repeated in a passport.
    raw_keys: list[str] = []
    for i, column in enumerate(columns, 1):
        key = column.strip()
        while key in raw_keys:
            key += f" [колонка {i}]"
        raw_keys.append(key)
    digest = hashlib.sha256(data).hexdigest()
    rows: list[CurriculumRow] = []
    block, goals = "", ""
    missing_order = 0
    guessed: list[str] = []
    for number, record in enumerate(records[header_index + 1:], header_index + 2):
        if not any(cell.strip() for cell in record):
            continue
        if len(record) != len(columns):
            raise InputError(f"Запись {number}: {len(record)} ячеек вместо {len(columns)}. Проверьте кавычки и разделители CSV.")
        title = value(record, "title")
        if normalize_header(title) in TITLES:
            continue
        # A new block resets inherited goals; block-only rows can introduce it.
        next_block, next_goals = value(record, "block"), value(record, "goals")
        if next_block and next_block != block:
            block, goals = next_block, next_goals
        elif next_goals:
            goals = "\n".join(dict.fromkeys(filter(None, (goals, next_goals))))
        if not title:
            continue
        missing_order += not bool(value(record, "order"))
        raw = dict(zip(raw_keys, record, strict=True))
        project_format, group_size = value(record, "format"), value(record, "group_size")
        is_group = bool(re.search(r"груп|group", project_format, re.I)) or (not project_format and bool(group_size))
        group_numbers = re.findall(r"\d+", group_size)
        # Normalize only form values; ranges and original spelling stay in raw.
        kind = row_kind(project_format, title)
        if kind == "exam" and not project_format:
            guessed.append(title)
        rows.append(CurriculumRow(
            id=f"up-{digest[:10]}-{number}", row_number=number, kind=kind,
            block=block or "Без блока", block_goals=goals, code=value(record, "code"), repository=value(record, "repository"),
            title=title, description=value(record, "description"), cases=value(record, "cases"),
            outcomes=outcome_lines(value(record, "outcomes")),
            program=value(record, "program"), checking=value(record, "checking"),
            skills=value(record, "skills"), audience=value(record, "audience"), tools=value(record, "tools"),
            software=value(record, "software"), storytelling=value(record, "storytelling"),
            storytelling_type=value(record, "storytelling_type"),
            format="group" if is_group else "individual", group_size=group_numbers[-1] if is_group and group_numbers else "",
            hours=value(record, "hours") or astronomical(value(record, "hours_academic")),
            materials=value(record, "materials"), raw=raw,
        ))
    if not rows:
        raise InputError(f"{inside} нет проектов с заполненным названием")
    if len(rows) > 200:
        raise InputError("В прототипе поддерживается до 200 проектов в одном УП")
    if all(row.kind == "exam" for row in rows):
        raise InputError(f"{inside} только экзамены: проектов для подготовки нет")
    if missing_order:
        warnings.append(f"Без номера: {missing_order} проектов. Сохранён порядок {order_of}.")
    if guessed:
        # A guess must be seen and must have a way out: the plan's own «Формат» always wins.
        warnings.append("Экзаменами сочтены по названию, формат в УП не указан: " + ", ".join(f"«{t}»" for t in guessed[:10])
                        + ". Если это проект, укажите его формат в УП (индивидуальный или групповой).")
    # Report the mapping explicitly: a silently empty field is how a brief gets filled by hand.
    mapped = {i for found in indexes.values() for i in found}
    report = {field: [columns[i].strip() or "без заголовка" for i in found] for field, found in indexes.items() if found}
    missing = []
    for field, label in KEY_FIELDS.items():
        if not (indexes[field] or (field == "hours" and indexes["hours_academic"])):
            if field not in NORMALLY_ABSENT:
                missing.append(f"{label}: колонка не найдена")
        elif not any(getattr(row, field) for row in rows):
            missing.append(f"{label}: колонка пустая во всех проектах")
    unmapped = [c.strip() for i, c in enumerate(columns) if c.strip() and i not in mapped]
    tabs, tab_read, blocks, notes = attach_skills(book, rows, skills_tab) if book else ([], "", [], [])
    try:
        return Curriculum(filename=Path(name).name, sha256=digest, rows=rows, warnings=warnings,
                          columns=report, missing_fields=missing, unmapped_columns=unmapped,
                          skill_catalog=book.catalog if book else [], skill_tabs=tabs[:MAX_SKILL_TABS], skills_tab=tab_read,
                          skill_blocks=blocks, skill_notes=notes)
    except ValidationError as error:
        # Whatever a file holds, the answer is a refusal the methodologist can read, never a server error.
        where = "; ".join(".".join(str(part) for part in e["loc"]) + ": " + e["msg"] for e in error.errors()[:3])
        raise InputError(f"Учебный план не удалось разобрать: {where}.") from error


class VisibleHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data: str) -> None:
        if not self.hidden and data.strip():
            self.parts.append(data.strip())


def extract_material(name: str, data: bytes) -> Material:
    check_file(name, data)
    suffix = Path(name).suffix.lower()
    parts: list[tuple[str, str]] = []
    warnings: list[str] = []
    unread: list[str] = []
    if suffix == ".docx":
        document = Document(io.BytesIO(data))
        parts.extend((f"Абзац {i}", p.text) for i, p in enumerate(document.paragraphs, 1) if p.text.strip())
        for i, table in enumerate(document.tables, 1):
            parts.extend((f"Таблица {i}, строка {j}", " | ".join(c.text for c in row.cells)) for j, row in enumerate(table.rows, 1))
        warnings.append("Извлечены абзацы и таблицы. Изображения, текстовые поля и комментарии не анализируются.")
        if document.element.xpath('//w:drawing | //w:pict | //w:txbxContent'):
            unread.append("Изображения или текстовые поля DOCX без текстовой расшифровки")
    elif suffix == ".pdf":
        pdf = PdfReader(io.BytesIO(data))
        if len(pdf.pages) > 60:
            raise InputError("Для прототипа выделите нужные страницы PDF (до 60 страниц)")
        parts = [(f"Страница {i}", page.extract_text() or "") for i, page in enumerate(pdf.pages, 1)]
        unread.extend(location for location, text in parts if not text.strip())
        warnings.append("PDF прочитан как текст; изображения и сканы требуют отдельной расшифровки.")
    elif suffix == ".xlsx":
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=False)
        try:
            for sheet in workbook:
                # A 5 KB file can declare cells up to XFD1048576; read-only mode walks every empty row up to it.
                declared = (sheet.max_row or 0) * (sheet.max_column or 0)
                if declared > MAX_XLSX_CELLS:
                    raise InputError(f"Лист «{sheet.title}» объявлен размером {sheet.max_row}×{sheet.max_column} ячеек. "
                                     "Скопируйте нужные строки на новый лист и загрузите его.")
                for i, cells in enumerate(sheet.iter_rows(values_only=True), 1):
                    if any(c is not None for c in cells):
                        # A zero is data (a 0% weight, a 0 score): only an empty cell is empty.
                        parts.append((f"{sheet.title}, строка {i}", " | ".join("" if c is None else str(c) for c in cells)))
                    if len(parts) > 1500:
                        raise InputError("В таблице слишком много строк. Выделите нужный фрагмент.")
        finally:
            workbook.close()
    elif suffix in TEXT_SUFFIXES:
        text = decode_text(data)
        if suffix == ".html":
            html = VisibleHTML()
            html.feed(text)
            text = "\n".join(html.parts)
        if suffix == ".ipynb":
            notebook = json.loads(text)
            parts = [(f"Ячейка {i}", "".join(cell.get("source", []))) for i, cell in enumerate(notebook.get("cells", []), 1)]
            warnings.append("Код прочитан без исполнения. Сохранённые outputs ноутбука не использованы.")
        else:
            lines = text.splitlines()
            parts = [(f"Строки {i + 1}–{min(i + 35, len(lines))}", "\n".join(lines[i:i + 35])) for i in range(0, len(lines), 35)]
        if suffix in {".py", ".js", ".ts", ".sql", ".sh", ".ps1"}:
            warnings.append("Скрипт прочитан как справочный текст, не запускался.")
    else:
        raise InputError("Формат не поддерживается. Используйте DOCX, PDF, XLSX или текстовый файл.")
    if sum(len(t) for _, t in parts) > 180000:
        raise InputError("Извлечено больше 180 000 символов. Загрузите нужный фрагмент.")
    digest = hashlib.sha256(data).hexdigest()
    passages: list[Passage] = []
    for location, text in parts:
        # Large paragraphs are split with explicit continuation locations.
        for start in range(0, len(text), 5000):
            chunk = text[start:start + 5000].strip()
            if chunk:
                passages.append(Passage(id=f"s-{digest[:10]}-{len(passages) + 1}", location=f"{location} · фрагмент {start // 5000 + 1}", text=chunk))
    if not passages:
        raise InputError("Текст не найден. Для скана нужна текстовая расшифровка.")
    if len(passages) > 500:
        raise InputError("Слишком много фрагментов. Выделите относящиеся к проекту материалы.")
    return Material(id=digest[:16], name=Path(name).name, sha256=digest, passages=passages, warnings=warnings,
                    read_status="partial" if unread else "text_extracted", unread_locations=unread)
