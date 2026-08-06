"""Нормализованная модель УП (учебного плана) — источник содержания для РПД.

Источник УП: из Реестра УП (каталог-БД, ``get_curriculum_plan`` → ``from_registry_plan``) или из
загруженного файла (парсер — позже). Здесь только нормализация в :class:`UpPlan`; маппинг в слоты
шаблона ДПО — в :mod:`content_factory.rpd.up_transfer`.
"""

from __future__ import annotations

import csv
import io
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .models import Competency


class UpRow(BaseModel):
    """Строка УП = одна контентная единица (проект): результаты знать/уметь/навык, часы, формат, skills."""

    block_title: str = ""
    name: str = ""
    description: str = ""
    outcomes_know: str = ""
    outcomes_can: str = ""
    outcomes_skills: str = ""
    hours: str = ""
    fmt: str = ""
    software: str = ""
    p2p: str = ""
    skills: list[str] = Field(default_factory=list)

    def outcomes(self) -> list[str]:
        return [value.strip() for value in (self.outcomes_know, self.outcomes_can, self.outcomes_skills) if value.strip()]

    def control(self) -> str:
        """Форма контроля проекта из УП: p2p-проверка либо защита проекта."""
        return "p2p-проверка" if _to_int(self.p2p) else "Проект"


class UpModule(BaseModel):
    """Модуль (тематический блок) УП — сгруппированные по ``block_title`` строки-проекты."""

    title: str = ""
    goals: str = ""
    rows: list[UpRow] = Field(default_factory=list)

    @property
    def hours(self) -> str:
        total = Decimal("0")
        seen = False
        for row in self.rows:
            parsed = _to_decimal(row.hours)
            if parsed is not None:
                total += parsed
                seen = True
        return _decimal_str(total) if seen else ""

    def fmt(self) -> str:
        for row in self.rows:
            if row.fmt.strip():
                return row.fmt.strip()
        return ""

    def control(self) -> str:
        return "p2p-проверка" if any(_to_int(row.p2p) for row in self.rows) else "Проект"


class UpPlan(BaseModel):
    """Учебный план целиком: название, общий объём часов, модули."""

    title: str = ""
    total_hours: str = ""
    modules: list[UpModule] = Field(default_factory=list)

    def all_outcomes(self) -> list[str]:
        result: list[str] = []
        for module in self.modules:
            for row in module.rows:
                for outcome in row.outcomes():
                    if outcome not in result:
                        result.append(outcome)
        return result

    def outcomes_by_group(self) -> dict[str, list[str]]:
        """Результаты, разложенные по знать/уметь/владеть — как требует ДПО-шаблон."""
        groups: dict[str, list[str]] = {"know": [], "can": [], "skills": []}
        for module in self.modules:
            for row in module.rows:
                for key, value in (("know", row.outcomes_know), ("can", row.outcomes_can), ("skills", row.outcomes_skills)):
                    text = value.strip()
                    if text and text not in groups[key]:
                        groups[key].append(text)
        return groups

    def competencies(self) -> list[Competency]:
        seen: list[str] = []
        result: list[Competency] = []
        for module in self.modules:
            for row in module.rows:
                for skill in row.skills:
                    name = skill.strip()
                    if name and name not in seen:
                        seen.append(name)
                        result.append(Competency(code=name, description=""))
        return result

    def competency_rows(self) -> list[tuple[str, str, str]]:
        """Для полной таблицы компетенций: (Код ПК-i, навык, «после обучения» = умеет+навык из строк с этим навыком)."""
        order: list[str] = []
        after: dict[str, list[str]] = {}
        for module in self.modules:
            for row in module.rows:
                gains = [value.strip() for value in (row.outcomes_can, row.outcomes_skills) if value.strip()]
                for skill in row.skills:
                    name = skill.strip()
                    if not name:
                        continue
                    if name not in after:
                        after[name] = []
                        order.append(name)
                    for gain in gains:
                        if gain not in after[name]:
                            after[name].append(gain)
        # Код профессиональной компетенции является нормативным значением и не должен синтезироваться.
        return [("", name, "; ".join(after[name])) for name in order]


def from_registry_plan(plan: dict[str, Any]) -> UpPlan:
    """Адаптирует каталожный план (``get_curriculum_plan``) в :class:`UpPlan`."""
    title = str(plan.get("title") or "").strip()
    meta = plan.get("meta") or plan.get("summary") or {}
    total_hours = _hours_str(meta.get("total_hours"))

    modules: list[UpModule] = []
    by_title: dict[str, UpModule] = {}
    current_block = ""
    for raw in plan.get("rows") or []:
        # Реестр, как и CSV-экспорт, может указывать тематический блок только в первой строке
        # группы. Пустая ячейка продолжает предыдущий блок, а не создаёт общий «Модуль».
        explicit_block = str(raw.get("block_title") or "").strip()
        if explicit_block:
            current_block = explicit_block
        block_title = current_block or "Модуль"
        row = UpRow(
            block_title=block_title,
            name=str(raw.get("project_name") or ""),
            description=str(raw.get("project_summary") or ""),
            outcomes_know=str(raw.get("outcomes_know") or ""),
            outcomes_can=str(raw.get("outcomes_can") or ""),
            outcomes_skills=str(raw.get("outcomes_skills") or raw.get("learning_outcomes") or ""),
            hours=_hours_str(raw.get("effort_hours")),
            fmt=str(raw.get("delivery_format") or ""),
            software=str(raw.get("required_tools") or ""),
            p2p=str(raw.get("p2p_checks") or ""),
            skills=_skill_names(raw.get("skills") or raw.get("skills_list") or raw.get("weighted_skills")),
        )
        module = by_title.get(block_title)
        if module is None:
            module = UpModule(title=block_title, goals=str(raw.get("block_goal") or ""))
            by_title[block_title] = module
            modules.append(module)
        elif not module.goals:
            module.goals = str(raw.get("block_goal") or "")
        module.rows.append(row)
    return UpPlan(title=title, total_hours=total_hours, modules=modules)


def parse_up_csv(path: str | Path, title: str = "") -> UpPlan:
    """Разбирает УП из CSV-экспорта Реестра (колонки по именам заголовков) в :class:`UpPlan`."""
    raw = Path(path).read_bytes()
    text = _decode(raw)
    delimiter = ";" if text[:4000].count(";") > text[:4000].count(",") else ","
    reader = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if not reader:
        return UpPlan(title=title)
    header = [cell.strip().lower() for cell in reader[0]]

    col_block = _find(header, "тематический блок")
    col_goals = _find(header, "цели блока")
    col_name = _find(header, "название контентной", "название кон")
    col_desc = _find(header, "краткое описание")
    col_know = _find(header, "знать")
    col_can = _find(header, "уметь")
    col_oskill = _find(header, "какой навык")
    col_hours = _find(header, "астр. час", "трудоёмкость, астр")
    col_fmt = _find(header, "формат")
    col_soft = _find(header, "необходимое по")
    col_skills = _find(header, "список навыков")
    col_p2p = _find(header, "p2p")

    modules: list[UpModule] = []
    by_title: dict[str, UpModule] = {}
    current_block = ""
    for raw_row in reader[1:]:
        block = _cell(raw_row, col_block)
        if block:
            current_block = block
        name = _cell(raw_row, col_name)
        if not current_block and not name:
            continue  # пустая строка

        row = UpRow(
            block_title=current_block or "Модуль",
            name=name,
            description=_cell(raw_row, col_desc),
            outcomes_know=_cell(raw_row, col_know),
            outcomes_can=_cell(raw_row, col_can),
            outcomes_skills=_cell(raw_row, col_oskill),
            hours=_hours_str(_cell(raw_row, col_hours)),
            fmt=_cell(raw_row, col_fmt),
            software=_cell(raw_row, col_soft),
            p2p=_cell(raw_row, col_p2p),
            skills=_split_skills(_cell(raw_row, col_skills)),
        )
        module = by_title.get(row.block_title)
        if module is None:
            module = UpModule(title=row.block_title, goals=_cell(raw_row, col_goals))
            by_title[row.block_title] = module
            modules.append(module)
        elif not module.goals:
            module.goals = _cell(raw_row, col_goals)
        module.rows.append(row)

    values = [_to_decimal(row.hours) for module in modules for row in module.rows]
    valid_values = [value for value in values if value is not None]
    total = sum(valid_values, Decimal("0"))
    return UpPlan(title=title, total_hours=_decimal_str(total) if valid_values else "", modules=modules)


def _cell(row: list[str], index: int | None) -> str:
    return row[index].strip() if index is not None and index < len(row) else ""


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _find(header: list[str], *subs: str) -> int | None:
    for index, cell in enumerate(header):
        if any(sub in cell for sub in subs):
            return index
    return None


def _split_skills(value: str) -> list[str]:
    if not value.strip():
        return []
    parts: list[str] = []
    for chunk in value.replace(";", "\n").split("\n"):
        name = chunk.split(":")[0].strip()  # отбрасываем развесовку "навык: 30%"
        if name and name not in parts:
            parts.append(name)
    return parts


def _to_int(value: Any) -> int | None:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _hours_str(value: Any) -> str:
    parsed = _to_decimal(value)
    return _decimal_str(parsed) if parsed is not None else ""


def _skill_names(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        return _split_skills(text)
    return []


def _to_decimal(value: Any) -> Decimal | None:
    text = str(value or "").strip().replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _decimal_str(value: Decimal) -> str:
    return format(value.normalize(), "f")
