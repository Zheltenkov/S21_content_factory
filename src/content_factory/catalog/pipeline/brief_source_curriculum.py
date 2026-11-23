"""Read explicitly identified historical project registers from curriculum briefs."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal, TypedDict


class SourceProject(TypedDict):
    source_id: str
    title: str
    description: str
    source_hours: float | None
    team_mode: Literal["team", "individual", "unknown"]
    block: str
    source_ref: str
    outcomes: list[str]
    card_title: str
    card_ref: str
    review_note: str


@dataclass(frozen=True, slots=True)
class CurriculumBrief:
    requirements_text: str
    projects: list[SourceProject]


_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_HISTORY = re.compile(r"\b(?:исходн|прежн|стар[оыа]|действовавш|существующ|предыдущ|историческ|справочн|текущ|legacy|existing|previous|source|historical|original)", re.I)
_PROJECTS = re.compile(r"\b(?:работ|проект|дисциплин|модул|учебн\w*\s+план|УП\b|curriculum|projects?|modules?)", re.I)
_REGISTER = re.compile(r"^(?:реестр|перечень|список|работы\b|проекты\b|прежн|исходн|историческ|справочн|стар[оыа]|существующ|предыдущ|действовавш|текущ|legacy|existing|previous|source|historical|original|projects?\b)", re.I)
_CARD = re.compile(r"^(?:[-*+]\s+)?\*\*(?P<title>.+?)\*\*\s*·\s*(?P<ref>[^·]+?)\s*·\s*(?P<hours>[^·]+?)\s*·\s*(?P<mode>[^·]+?)\s*$")
_OLD_CARD = re.compile(r"^(?:старая|прежняя|исходная) карточка\s+«([^»]+)»\s+(?:в\s+)?(.+?!\$?[A-Z]+\$?\d+(?::\$?[A-Z]+\$?\d+)?)\.\s*(.*)$", re.I)
_DESCRIPTION = re.compile(r"^(?:содержание(?: прежней работы)?|описание|description)\s*[.:]\s*", re.I)
_OUTCOMES = re.compile(r"^(?:образовательные результаты|результаты обучения|исходные ОР|ОР|outcomes)\s*[.:]\s*", re.I)
_REVIEW = re.compile(r"^(?:отзыв эксперта|экспертный отзыв|review note)\b", re.I)
_HOURS = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:(?:ч(?:ас(?:а|ов)?)?\.?|h(?:ours?)?)(?:\s+в\s+(?:прежнем|старом|исходном)\s+плане)?)?", re.I)
_COLUMNS = {
    "title": ("название", "наименование", "проект", "название проекта", "работа", "project", "title"),
    "description": ("описание", "содержание", "описание проекта", "description"),
    "source_hours": ("часы", "исходные часы", "часы в прежнем плане", "трудоемкость", "ч", "hours"),
    "team_mode": ("форма", "формат", "форма работы", "формат работы", "team mode", "format"),
    "block": ("блок", "раздел", "модуль", "block"),
    "source_ref": ("источник", "ссылка", "ячейка", "координаты", "source", "reference"),
    "outcomes": ("ор", "результаты", "образовательные результаты", "результаты обучения", "outcomes"),
    "review_note": ("отзыв эксперта", "отзыв", "review note"),
}
_COLUMN_NAMES = {alias: key for key, aliases in _COLUMNS.items() for alias in aliases}


def _identity(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _outcomes(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?i)<br\s*/?>|(?=\b(?:знает|понимает|умеет)\s*:)", text) if s.strip()]


def _project(title: str, ref: str, hours: str, mode: str, block: str) -> SourceProject:
    title = title.removeprefix("**").removesuffix("**").strip()
    if not title:
        raise ValueError("В строке реестра прежних работ отсутствует название проекта.")
    hours_match = _HOURS.fullmatch(hours)
    if not hours_match and hours not in {"", "-", "—", "–"}:
        raise ValueError(f"Проект «{title}»: не удалось прочитать исходную трудоёмкость «{hours}».")
    source_hours = float(hours_match[1].replace(",", ".")) if hours_match else None
    if source_hours is not None and not math.isfinite(source_hours):
        raise ValueError(f"Проект «{title}»: исходная трудоёмкость выходит за пределы числового диапазона.")
    if ref in {"-", "—", "–"}:
        ref = ""
    mode = _identity(mode)
    team_mode: Literal["team", "individual", "unknown"] = "unknown"
    if re.match(r"(группов|командн|team\b|group\b)", mode):
        team_mode = "team"
    elif re.match(r"(индивидуальн|individual\b|solo\b)", mode):
        team_mode = "individual"
    # File coordinates survive renames and text edits. Unreferenced rows use their labelled block and title.
    key = "ref:" + _identity(ref) if ref else "title:" + _identity(block) + "\n" + _identity(title)
    return SourceProject(
        source_id="src-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16],
        title=title, source_ref=ref, source_hours=source_hours,
        team_mode=team_mode, block=block, description="", outcomes=[], card_title="", card_ref="", review_note="",
    )


def _table_cells(line: str) -> list[str]:
    # A literal pipe in a project title is escaped in a Markdown table.
    if line.startswith("|"):
        line = line[1:].removesuffix("|")
    return [s.replace(r"\|", "|").strip() for s in re.split(r"(?<!\\)\|", line)]


def _parse_register(lines: list[str]) -> list[SourceProject]:
    projects: list[SourceProject] = []
    block = ""
    current: SourceProject | None = None
    columns: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        heading = _HEADING.match(line)
        if heading:
            block, current, columns = heading[2], None, []
            continue
        card = _CARD.match(line)
        if card:
            current = _project(card["title"], card["ref"].strip(), card["hours"].strip(), card["mode"], block)
            projects.append(current)
            columns = []
        elif line.startswith(("**", "- **")) and "·" in line:
            raise ValueError(f"Неполная строка реестра прежних работ: {line[:120]}")
        elif "|" in line and (columns or current is None):
            cells = _table_cells(line)
            names = [_COLUMN_NAMES.get(_identity(s.strip("*")).replace("ё", "е"), "") for s in cells]
            if "title" in names and ("source_ref" in names or "source_hours" in names):
                columns, current = names, None
            elif columns and not all(re.fullmatch(r":?-{3,}:?", s) for s in cells):
                if len(cells) != len(columns):
                    raise ValueError(f"Некорректное число ячеек в строке реестра: {line[:120]}")
                values = dict(zip(columns, cells, strict=True))
                row = _project(values["title"], values.get("source_ref", ""), values.get("source_hours", ""),
                               values.get("team_mode", ""), values.get("block", "") or block)
                row["description"] = values.get("description", "")
                row["outcomes"] = _outcomes(values.get("outcomes", ""))
                row["review_note"] = values.get("review_note", "")
                projects.append(row)
        elif current:
            # Old reviews and old learning outcomes are evidence with distinct provenance.
            old_card = _OLD_CARD.match(line)
            if old_card:
                current["card_title"], current["card_ref"] = old_card[1], old_card[2]
                current["outcomes"].extend(_outcomes(old_card[3]))
            elif _OUTCOMES.match(line):
                current["outcomes"].extend(_outcomes(_OUTCOMES.sub("", line, count=1)))
            elif _REVIEW.match(line):
                current["review_note"] = line
            else:
                content = _DESCRIPTION.sub("", line, count=1)
                current["description"] = (current["description"] + "\n" + content).strip()
    return projects


def split_curriculum_brief(text: str) -> CurriculumBrief:
    """Extract a marked register, preserving all other sections and encounter order."""
    lines = text.splitlines(keepends=True)
    requirements: list[str] = []
    projects: list[SourceProject] = []
    offset = 0
    while offset < len(lines):
        heading = _HEADING.match(lines[offset].strip())
        if not (heading and _REGISTER.search(heading[2]) and _HISTORY.search(heading[2]) and _PROJECTS.search(heading[2])):
            requirements.append(lines[offset])
            offset += 1
            continue
        end = offset + 1
        while end < len(lines):
            following = _HEADING.match(lines[end].strip())
            if following and len(following[1]) <= len(heading[1]):
                break
            end += 1
        rows = _parse_register(lines[offset + 1:end])
        if rows:
            projects.extend(rows)
        else:
            if re.search(r"реестр|register|inventory", heading[2], re.I):
                raise ValueError(
                    f"В разделе «{heading[2]}» не распознано ни одной исходной работы. "
                    "Укажите название, источник или часы в Markdown-таблице либо используйте карточки с разделителем ·."
                )
            requirements.extend(lines[offset:end])
        offset = end
    # Ambiguous IDs would make coverage and subsequent changes unverifiable.
    seen: set[str] = set()
    for project in projects:
        if project["source_id"] in seen:
            raise ValueError(f"Повтор идентификатора источника в реестре прежних работ: {project['title']}.")
        seen.add(project["source_id"])
    return CurriculumBrief("".join(requirements).strip() if projects else text, projects)
