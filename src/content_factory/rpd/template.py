"""Каркас-шаблон РПД: целевой набор разделов, выведенный из образца.

Пока реального ДПО-шаблона нет, каркасом служит `_итог.docx` (образец выхода). Разбираем его тем же
``parse_dpo_program`` — структура его разделов и есть целевая структура РПД. Тело разделов сохраняем
в typed-модели только для profile-owned read-only проекций: профиль явно решает, какие нормативные
разделы показать методисту, не заменяя их содержимое при DOCX-рендере.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from .ingest import parse_dpo_program
from .models import ProgramTable


class TargetSection(BaseModel):
    """Целевой раздел РПД. ``is_container`` — заголовок-контейнер без собственного тела (напр.
    «1. ХАРАКТЕРИСТИКА ПРОГРАММЫ»): переносить в нём нечего, пробелом он тоже не считается."""

    number: str = ""
    title: str = ""
    level: int = 0
    is_container: bool = False
    paragraphs: list[str] = Field(default_factory=list)
    tables: list[ProgramTable] = Field(default_factory=list)


class RpdTemplate(BaseModel):
    sections: list[TargetSection] = Field(default_factory=list)


def default_skeleton_path() -> Path:
    """Путь к временному каркасу-ресурсу (пока — копия образца `_итог.docx`).

    При приходе настоящего ДПО-шаблона достаточно заменить этот файл — код не меняется.
    """
    return Path(__file__).parent / "templates" / "dpo_skeleton.docx"


def default_pk_skeleton_path() -> Path:
    """Реальный ДПО-шаблон «повышение квалификации» (с комментариями-правилами методиста)."""
    return Path(__file__).parent / "templates" / "dpo_pk_skeleton.docx"


def load_rpd_template(skeleton_path: str | Path) -> RpdTemplate:
    """Загрузить структуру и typed-тело разделов, не смешивая их с generated content."""
    skeleton = parse_dpo_program(skeleton_path)
    sections: list[TargetSection] = []
    for index, section in enumerate(skeleton.sections):
        # Пустое тело не означает контейнер: во внутренних и внешних формах пустой листовой
        # раздел часто является слотом для будущей генерации. Контейнер определяется только
        # структурой заголовков - сразу после него должен идти дочерний уровень.
        next_section = skeleton.sections[index + 1] if index + 1 < len(skeleton.sections) else None
        has_child = next_section is not None and next_section.level > section.level
        sections.append(
            TargetSection(
                number=section.number,
                title=section.title,
                level=section.level,
                is_container=not (section.paragraphs or section.tables) and has_child,
                paragraphs=list(section.paragraphs),
                tables=[table.model_copy(deep=True) for table in section.tables],
            )
        )
    return RpdTemplate(sections=sections)
