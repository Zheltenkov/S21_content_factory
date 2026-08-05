"""РПД-генератор — тонкий временный модуль (ДПО, только перенос утверждённого).

Изолирован от остального движка, чтобы чисто ретайрить при переходе на генерацию УП. Публичная
поверхность: разбор ДПО-программы (``parse_dpo_program`` → ``DpoProgram``) и её типы.
"""

from __future__ import annotations

from .ingest import parse_dpo_program
from .models import Competency, DpoProgram, ProgramSection, ProgramTable
from .render_docx import render_rpd_docx
from .render_selective import render_rpd_pk
from .template import (
    RpdTemplate,
    TargetSection,
    default_pk_skeleton_path,
    default_skeleton_path,
    load_rpd_template,
)
from .transfer import GAP_MARKER, FilledSection, GapQuestion, RpdDraft, build_rpd
from .up_models import UpModule, UpPlan, UpRow, from_registry_plan, parse_up_csv
from .up_transfer import build_rpd_from_up

__all__ = [
    "GAP_MARKER",
    "Competency",
    "DpoProgram",
    "FilledSection",
    "GapQuestion",
    "ProgramSection",
    "ProgramTable",
    "RpdDraft",
    "RpdTemplate",
    "TargetSection",
    "UpModule",
    "UpPlan",
    "UpRow",
    "build_rpd",
    "build_rpd_from_up",
    "default_pk_skeleton_path",
    "default_skeleton_path",
    "from_registry_plan",
    "load_rpd_template",
    "parse_dpo_program",
    "parse_up_csv",
    "render_rpd_docx",
    "render_rpd_pk",
]
