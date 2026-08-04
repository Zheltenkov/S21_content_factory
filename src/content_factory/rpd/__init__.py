"""РПД-генератор — тонкий временный модуль (ДПО, только перенос утверждённого).

Изолирован от остального движка, чтобы чисто ретайрить при переходе на генерацию УП. Публичная
поверхность: разбор ДПО-программы (``parse_dpo_program`` → ``DpoProgram``) и её типы.
"""

from __future__ import annotations

from .ingest import parse_dpo_program
from .models import Competency, DpoProgram, ProgramSection, ProgramTable
from .render_docx import render_rpd_docx
from .template import RpdTemplate, TargetSection, default_skeleton_path, load_rpd_template
from .transfer import GAP_MARKER, FilledSection, GapQuestion, RpdDraft, build_rpd

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
    "build_rpd",
    "default_skeleton_path",
    "load_rpd_template",
    "parse_dpo_program",
    "render_rpd_docx",
]
