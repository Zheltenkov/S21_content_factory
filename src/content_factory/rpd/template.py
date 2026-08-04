"""Каркас-шаблон РПД: целевой набор разделов, выведенный из образца.

Пока реального ДПО-шаблона нет, каркасом служит `_итог.docx` (образец выхода). Разбираем его тем же
``parse_dpo_program`` — структура его разделов и есть целевая структура РПД. Контент образца в шаблон
НЕ переносим: он только задаёт, какие разделы должны быть и в каком порядке. При приходе настоящего
шаблона достаточно подменить файл-каркас.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from .ingest import parse_dpo_program


class TargetSection(BaseModel):
    """Целевой раздел РПД. ``is_container`` — заголовок-контейнер без собственного тела (напр.
    «1. ХАРАКТЕРИСТИКА ПРОГРАММЫ»): переносить в нём нечего, пробелом он тоже не считается."""

    number: str = ""
    title: str = ""
    level: int = 0
    is_container: bool = False


class RpdTemplate(BaseModel):
    sections: list[TargetSection] = Field(default_factory=list)


def default_skeleton_path() -> Path:
    """Путь к временному каркасу-ресурсу (пока — копия образца `_итог.docx`).

    При приходе настоящего ДПО-шаблона достаточно заменить этот файл — код не меняется.
    """
    return Path(__file__).parent / "templates" / "dpo_skeleton.docx"


def load_rpd_template(skeleton_path: str | Path) -> RpdTemplate:
    """Загружает каркас-шаблон из DOCX: список целевых разделов (без контента образца)."""
    skeleton = parse_dpo_program(skeleton_path)
    sections = [
        TargetSection(
            number=section.number,
            title=section.title,
            level=section.level,
            is_container=not (section.paragraphs or section.tables),
        )
        for section in skeleton.sections
    ]
    return RpdTemplate(sections=sections)
