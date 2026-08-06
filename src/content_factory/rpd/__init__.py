"""РПД-генератор — тонкий временный модуль (ДПО, только перенос утверждённого).

Изолирован от остального движка, чтобы чисто ретайрить при переходе на генерацию УП. Публичная
поверхность: разбор ДПО-программы (``parse_dpo_program`` → ``DpoProgram``) и её типы.
"""

from __future__ import annotations

from .build import RpdBuild, RpdDocumentFields, RpdGenerationTrace, RpdSection, build_rpd_sections
from .evaluation import RpdEvaluationArtifact, RpdRoleMetrics, evaluate_rpd_build
from .ingest import parse_dpo_program
from .models import Competency, DpoProgram, ProgramSection, ProgramTable
from .quality import QualityIssue, RpdQualityReport, evaluate_build_quality, preflight_up_plan
from .render_docx import render_rpd_docx
from .render_selective import render_build, render_rpd_pk
from .template import (
    RpdTemplate,
    TargetSection,
    default_pk_skeleton_path,
    default_skeleton_path,
    load_rpd_template,
)
from .template_profiles import (
    BUILTIN_PK_PROFILE_ID,
    TemplateCapabilityReport,
    TemplateProfile,
    UnsupportedTemplateProfileError,
    detect_template_profile,
    get_template_profile,
    registered_template_profiles,
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
    "RpdBuild",
    "RpdDocumentFields",
    "RpdDraft",
    "RpdEvaluationArtifact",
    "RpdGenerationTrace",
    "RpdQualityReport",
    "RpdSection",
    "RpdTemplate",
    "RpdRoleMetrics",
    "TemplateCapabilityReport",
    "TemplateProfile",
    "TargetSection",
    "QualityIssue",
    "UpModule",
    "UpPlan",
    "UpRow",
    "UnsupportedTemplateProfileError",
    "BUILTIN_PK_PROFILE_ID",
    "build_rpd",
    "build_rpd_from_up",
    "build_rpd_sections",
    "default_pk_skeleton_path",
    "default_skeleton_path",
    "detect_template_profile",
    "evaluate_build_quality",
    "evaluate_rpd_build",
    "from_registry_plan",
    "get_template_profile",
    "load_rpd_template",
    "parse_dpo_program",
    "parse_up_csv",
    "preflight_up_plan",
    "registered_template_profiles",
    "render_build",
    "render_rpd_docx",
    "render_rpd_pk",
]
