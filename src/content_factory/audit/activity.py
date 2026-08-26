"""Учебная активность проектов из DWH — «какие ветки актуальные и сколько активных учеников».

Аудитор знает только имена проектов внутри ветки; DWH знает, сколько учеников их сейчас сдаёт.
Связка идёт ПО ИМЕНИ (в DWH нет ID задания из имени репозитория): имя нормализуется (буквы+цифры,
верхний регистр), хвост ``.ID_<n>`` отбрасывается, дальше индекс имён → goal_id → строка статистики.

Данные — статический снапшот DWH на DWH_SNAPSHOT_DATE (два CSV рядом, в ``data/``). Перепрогнать —
``docs``/``dwh_projects_catalog_*.sql`` в исходной выгрузке. Логика матчинга повторяет join-скрипт,
которым выгрузку собирали, один в один (включая суммирование по нескольким goal_id одного имени).
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

_DATA_DIR = Path(__file__).with_name("data")
_ACTIVITY_CSV = _DATA_DIR / "dwh_projects_activity_2026-08-26.csv"
_INDEX_CSV = _DATA_DIR / "dwh_project_names_index_2026-08-26.csv"

DWH_SNAPSHOT_DATE = "2026-08-26"

# Статусы актуальности (значения совпадают с колонкой ``activity`` выгрузки).
ACTIVE = "активен"
FADING = "застывает"
ARCHIVED = "архив"
NO_DATA = "нет данных"

# Пороги активности — из join-скрипта выгрузки; менять здесь, если поменяли там.
_FRESH_DAYS = 90
_ARCHIVE_DAYS = 365
_ACTIVE_STUDENTS = 20

_NORM_RE = re.compile(r"[^0-9A-Za-zА-Яа-яЁё]")
_ID_RE = re.compile(r"[._-]?[Ii][Dd]_[0-9A-Za-z?]+$")


def _norm(name: str) -> str:
    """Ключ сопоставления: только буквы и цифры, верхний регистр — чинит расхождение
    разделителей (репо ``Python_1.Project_1`` ↔ платформа ``Python 1.Project 1``)."""

    return _NORM_RE.sub("", name).upper()


def _strip_id(name: str) -> str:
    """``BSA15_REST.ID_1361470`` → ``BSA15_REST``: хвост ``.ID_<n>`` — ID задания в CMS, в DWH его нет."""

    return _ID_RE.sub("", name)


@dataclass(frozen=True)
class ProjectActivity:
    """Учебная активность одного проекта аудита. ``matched=False`` — имени нет в снапшоте DWH
    (пробел в данных, НЕ «мёртвый проект»)."""

    matched: bool
    activity: str
    students_last_365d: int
    students_total: int
    last_activity: str
    days_since_last: int | None
    in_sber_catalog: bool
    goal_ids: tuple[str, ...]
    ambiguous: bool  # имя ведёт на >1 goal_id (старая+новая версия курса) — счётчики суммированы


_NO_DATA_ACTIVITY = ProjectActivity(
    matched=False,
    activity=NO_DATA,
    students_last_365d=0,
    students_total=0,
    last_activity="",
    days_since_last=None,
    in_sber_catalog=False,
    goal_ids=(),
    ambiguous=False,
)


@cache
def _load() -> tuple[dict[str, list[str]], dict[str, dict[str, str]]]:
    """(индекс имя→[goal_id], каталог goal_id→строка). Кэшируется на процесс; снапшот статичен."""

    index: dict[str, list[str]] = {}
    with _INDEX_CSV.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            index.setdefault(row["match_key"], []).append(row["goal_id"])
    catalog: dict[str, dict[str, str]] = {}
    with _ACTIVITY_CSV.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            catalog[row["goal_id"]] = row
    return index, catalog


def _classify(days_since_last: int, students_last_365d: int) -> str:
    if days_since_last > _ARCHIVE_DAYS:
        return ARCHIVED
    if days_since_last <= _FRESH_DAYS and students_last_365d >= _ACTIVE_STUDENTS:
        return ACTIVE
    return FADING


def lookup(project_name: str) -> ProjectActivity:
    """Учебная активность проекта по его имени в аудите. Никогда не None: несопоставленное имя
    → ``NO_DATA`` (matched=False). Несколько goal_id одного имени суммируются (как в join-скрипте)."""

    index, catalog = _load()
    key = _norm(_strip_id(project_name))
    goal_ids = index.get(key, [])
    records = [catalog[gid] for gid in goal_ids if gid in catalog]
    if not records:
        return _NO_DATA_ACTIVITY
    students_365 = sum(int(record["students_last_365d"]) for record in records)
    students_total = sum(int(record["students_total"]) for record in records)
    days_values = [int(record["days_since_last"]) for record in records if record["days_since_last"] != ""]
    days_since_last = min(days_values) if days_values else None  # самая свежая сдача
    last_dates = [record["last_activity"] for record in records if record["last_activity"]]
    last_activity = max(last_dates) if last_dates else ""
    activity = _classify(days_since_last, students_365) if days_since_last is not None else NO_DATA
    return ProjectActivity(
        matched=True,
        activity=activity,
        students_last_365d=students_365,
        students_total=students_total,
        last_activity=last_activity,
        days_since_last=days_since_last,
        in_sber_catalog=any(record["in_sber_catalog"] == "да" for record in records),
        goal_ids=tuple(record["goal_id"] for record in records),
        ambiguous=len(records) > 1,
    )


@dataclass(frozen=True)
class BranchActivitySummary:
    """Верхнеуровневая актуальность ветки: сколько проектов активно/застывает/архив/без данных
    и сколько активных учеников за год суммарно. ``items`` — (имя, активность), активные первыми,
    внутри — по числу учеников за год по убыванию (что править в первую очередь)."""

    total: int
    active: int
    fading: int
    archived: int
    no_data: int
    students_last_365d: int
    items: tuple[tuple[str, ProjectActivity], ...]

    @property
    def matched(self) -> int:
        return self.total - self.no_data


_SORT_RANK = {ACTIVE: 0, FADING: 1, ARCHIVED: 2, NO_DATA: 3}


def summarize_branch(project_names: list[str]) -> BranchActivitySummary:
    """Свести активность по списку проектов ветки в верхнеуровневый отчёт."""

    items = [(name, lookup(name)) for name in project_names]
    items.sort(key=lambda pair: (_SORT_RANK.get(pair[1].activity, 9), -pair[1].students_last_365d))
    counts = {ACTIVE: 0, FADING: 0, ARCHIVED: 0, NO_DATA: 0}
    for _name, info in items:
        counts[info.activity] = counts.get(info.activity, 0) + 1
    return BranchActivitySummary(
        total=len(items),
        active=counts[ACTIVE],
        fading=counts[FADING],
        archived=counts[ARCHIVED],
        no_data=counts[NO_DATA],
        students_last_365d=sum(info.students_last_365d for _name, info in items),
        items=tuple(items),
    )
