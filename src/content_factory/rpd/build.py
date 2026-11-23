"""Редактируемый черновик РПД: УП → структурированные разделы (для human-in-the-loop).

Разделяет пайплайн: build (здесь) собирает содержимое разделов в :class:`RpdBuild`;
:func:`render_selective.render_build` кладёт черновик в DOCX по шаблону. Между ними встаёт
редактор — методист проверяет и правит разрешённые разделы, потом экспорт. Большинство
boilerplate-разделов держит только шаблон; выбранные профилем разделы проецируются в черновик как
защищённые от редактирования точки review.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .assessment import build_assessment_tables
from .assessment_contracts import AssessmentProjectionTrace, AssessmentTablesResult
from .competency_agents import build_competency_table
from .competency_contracts import (
    CompetencyPipelineTrace,
    CompetencyTableResult,
    ProfessionalStandardSelection,
)
from .consilium import run_consilium, section_key
from .curriculum import apply_curriculum_titles, build_curriculum_table
from .curriculum_contracts import CurriculumProjectionTrace, CurriculumTableResult
from .dpo_spec import Cat
from .llm_runtime import RpdLlmEvent, collect_llm_events
from .module_evidence import (
    ModuleProgramBlock,
    ModuleProgramResult,
    PlatformEvidenceRequirement,
    build_assessment_evidence_table,
    build_module_program_table,
)
from .organizational_conditions import build_organizational_conditions
from .organizational_conditions_contracts import (
    OrganizationalConditionsResult,
    OrganizationalConditionsTrace,
)
from .passport import build_passport_table, document_field_updates
from .professional_activity import build_professional_activity_table
from .professional_standards import ProfessionalStandardCatalog
from .program_developers import build_program_developers_table
from .prose import generate_prose
from .quality import QualityIssue, RpdQualityReport, evaluate_build_quality, preflight_up_plan
from .schedule import build_schedule_table
from .schedule_contracts import ScheduleProjectionTrace, ScheduleTableResult
from .source_fidelity import CanonicalProgramSpec, canonicalize_up_plan
from .template import TargetSection, load_rpd_template
from .template_profiles import (
    BUILTIN_PK_PROFILE_ID,
    TemplateCapabilityReport,
    TemplateProfile,
    detect_template_profile,
    get_template_profile,
)
from .transfer import GAP_MARKER
from .up_models import UpPlan

_GENERATE_PLACEHOLDER = "[Будет сгенерировано]"
_PLATFORM_NOTE = "[Методист: вставьте скрин задания с платформы/LMS]"
_GAP_NOTE = "[Методист: проверьте и дополните раздел под программу]"
_TableContent = tuple[list[str], list[list[str]]]

#: Понятные методисту ярлыки источника раздела для навигатора редактора.
_CATEGORY_LABEL = {
    Cat.UP_PLAN: "из УП",
    Cat.UP_GRAFIK: "из УП",
    Cat.UP_MODULES: "из УП + платформа",
    Cat.UP_COMP: "УП + агенты",
    Cat.PASSPORT: "УП + LLM",
    Cat.PROF_ACTIVITY: "УП + LLM",
    Cat.CURRENT_CONTROL: "из УП",
    Cat.FINAL_ATTESTATION: "УП + паспорт",
    Cat.ASSESSMENT_EVIDENCE: "с платформы",
    Cat.METHODOLOGICAL_SUPPORT: "УП + методист",
    Cat.MATERIAL_CONDITIONS: "из УП",
    Cat.STAFFING: "методист",
    Cat.PROGRAM_DEVELOPERS: "методист",
    Cat.GENERATE: "сгенерировано",
    Cat.PLATFORM: "с платформы",
    Cat.GAP: "методист",
    Cat.BOILERPLATE: "форма ДПО",
}


class RpdSection(BaseModel):
    """Раздел черновика: редактируемый контент либо защищённая проекция текста шаблона."""

    number: str = ""
    title: str = ""
    category: str = ""
    label: str = ""
    kind: Literal["paragraphs", "table", "module_blocks"] = "paragraphs"
    paragraphs: list[str] = Field(default_factory=list)
    table_header: list[str] = Field(default_factory=list)
    table_rows: list[list[str]] = Field(default_factory=list)
    module_blocks: list[ModuleProgramBlock] = Field(default_factory=list)
    editable: bool = True
    preserve_template: bool = False
    done: bool = False  # методист отметил раздел готовым
    flag: str = ""  # замечание валидатора-критика (если есть)
    initial_content_hash: str = ""
    # Неизменяемый снимок текста из шаблона для preserve_template-разделов: если методист правил
    # (content_hash отличается) — рендер вставляет правки, иначе держит дословную вёрстку DOCX.
    template_baseline_hash: str = ""

    def content_hash(self) -> str:
        """Return a stable hash used to measure accepted-without-edit sections."""
        payload = {
            "kind": self.kind,
            "paragraphs": self.paragraphs,
            "table_header": self.table_header,
            "table_rows": self.table_rows,
            "module_blocks": [block.model_dump(mode="json") for block in self.module_blocks],
        }
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class RpdGenerationTrace(BaseModel):
    """Reproducibility and model-call evidence persisted with the editable draft."""

    source_sha256: str = ""
    template_sha256: str = ""
    template_profile_id: str = BUILTIN_PK_PROFILE_ID
    generated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    build_duration_ms: int = 0
    llm_events: list[RpdLlmEvent] = Field(default_factory=list)


class RpdDocumentFields(BaseModel):
    """Реквизиты обложки и паспорта, которых нет в учебном плане."""

    organization_name: str = ""
    review_body: str = ""
    protocol_number: str = ""
    protocol_date: str = ""
    leader_position: str = ""
    leader_name: str = ""
    order_number: str = ""
    order_date: str = ""
    audience: str = ""
    admission_requirements: str = ""
    delivery_mode: str = ""
    duration: str = ""
    study_mode: str = ""
    final_attestation: str = ""
    issued_document: str = ""
    city: str = ""
    year: str = Field(default_factory=lambda: str(datetime.now().year))

    @staticmethod
    def required_fields() -> list[tuple[str, str]]:
        """Return stable field identifiers and user-facing labels for the final-export gate."""
        return [
            ("organization_name", "образовательная организация"),
            ("review_body", "коллегиальный орган"),
            ("protocol_number", "номер протокола"),
            ("protocol_date", "дата протокола"),
            ("leader_position", "должность руководителя"),
            ("leader_name", "Ф.И.О. руководителя"),
            ("order_number", "номер приказа"),
            ("order_date", "дата приказа"),
            ("audience", "категория слушателей"),
            ("admission_requirements", "требования к поступающим"),
            ("delivery_mode", "форма обучения"),
            ("duration", "продолжительность обучения"),
            ("study_mode", "режим занятий"),
            ("final_attestation", "итоговая аттестация"),
            ("issued_document", "выдаваемый документ"),
            ("city", "город"),
            ("year", "год"),
        ]


class RpdBuild(BaseModel):
    """Черновик РПД: редактируемые разделы и заданные профилем защищённые review-разделы."""

    schema_version: int = 18
    revision: int = 0
    template_profile_id: str = BUILTIN_PK_PROFILE_ID
    template_report: TemplateCapabilityReport | None = None
    program_title: str = ""
    total_hours: str = ""
    source_spec: CanonicalProgramSpec = Field(default_factory=CanonicalProgramSpec)
    document_fields: RpdDocumentFields = Field(default_factory=RpdDocumentFields)
    sections: list[RpdSection] = Field(default_factory=list)
    preflight_issues: list[QualityIssue] = Field(default_factory=list)
    quality: RpdQualityReport = Field(default_factory=RpdQualityReport)
    generation_trace: RpdGenerationTrace = Field(default_factory=RpdGenerationTrace)
    competency_trace: CompetencyPipelineTrace | None = None
    curriculum_trace: CurriculumProjectionTrace | None = None
    schedule_trace: ScheduleProjectionTrace | None = None
    assessment_trace: AssessmentProjectionTrace | None = None
    conditions_trace: OrganizationalConditionsTrace | None = None
    module_evidence: list[PlatformEvidenceRequirement] = Field(default_factory=list)

    def section(self, number: str, title: str) -> RpdSection | None:
        for section in self.sections:
            if section.number == number and section.title == title:
                return section
        return None


def build_rpd_sections(
    up: UpPlan,
    template_path: str | Path | None = None,
    llm_client: Any = None,
    template_profile_id: str | None = None,
    professional_standard_catalog: ProfessionalStandardCatalog | None = None,
    professional_standard_selection: ProfessionalStandardSelection | None = None,
) -> RpdBuild:
    """Собирает редактируемый черновик РПД из УП.

    С LLM-клиентом запускает консилиум (осмысление шаблона + УП + проза, template-agnostic); при сбое
    или без клиента откатывается на детерминированную классификацию ``category_for``. Значения УП-таблиц
    всегда переносятся кодом — консилиум их не трогает (без домыслов).
    """
    started = time.perf_counter()
    if template_profile_id is None and template_path is not None:
        profile, template_report = detect_template_profile(template_path)
        resolved_template_path = Path(template_path)
    else:
        profile = get_template_profile(template_profile_id or BUILTIN_PK_PROFILE_ID)
        resolved_template_path = profile.template_path(template_path)
        template_report = profile.inspect(resolved_template_path)
    template = load_rpd_template(resolved_template_path)
    source_spec = canonicalize_up_plan(up)
    preflight_issues = preflight_up_plan(up, llm_client)
    curriculum_result: CurriculumTableResult | None = None
    if profile.profile_id == BUILTIN_PK_PROFILE_ID and any(
        profile.category_for(target.number, target.title) is Cat.UP_PLAN
        for target in template.sections
    ):
        curriculum_result = build_curriculum_table(
            up,
            llm_client,
            profile_id=profile.profile_id,
        )
    presentation_up = (
        apply_curriculum_titles(up, curriculum_result.trace.display_titles)
        if curriculum_result is not None
        else up
    )
    specs = []
    for template_section in template.sections:
        fallback = profile.category_for(template_section.number, template_section.title)
        specs.append(
            {
                "number": template_section.number,
                "title": template_section.title,
                "is_container": template_section.is_container,
                # The template profile owns structural roles.  The consilium may still classify
                # prose, but it must not turn the passport table back into free-form text.
                "forced_role": (
                    fallback.value
                    if fallback
                    in {
                        Cat.PASSPORT,
                        Cat.PROF_ACTIVITY,
                        Cat.CURRENT_CONTROL,
                        Cat.FINAL_ATTESTATION,
                        Cat.ASSESSMENT_EVIDENCE,
                        Cat.METHODOLOGICAL_SUPPORT,
                        Cat.MATERIAL_CONDITIONS,
                        Cat.STAFFING,
                        Cat.PROGRAM_DEVELOPERS,
                        Cat.BOILERPLATE,
                    }
                    else ""
                ),
            }
        )
    roles, prose_texts, flags = run_consilium(specs, presentation_up, llm_client)

    competency_result: CompetencyTableResult | None = None
    if any(
        profile.category_for(target.number, target.title) is Cat.UP_COMP
        for target in template.sections
    ):
        competency_result = build_competency_table(
            presentation_up,
            llm_client,
            profile_id=profile.profile_id,
            program_type=profile.program_type,
            catalog=professional_standard_catalog,
            selection=professional_standard_selection,
        )

    passport_table: _TableContent | None = None
    document_fields = RpdDocumentFields()
    if any(
        profile.category_for(target.number, target.title) is Cat.PASSPORT
        for target in template.sections
    ):
        passport_table = build_passport_table(presentation_up, llm_client, profile.profile_id)
        # Schedule periods and final-control wording consume the same canonical passport values.
        updates = document_field_updates(passport_table[1], profile.profile_id)
        document_fields = document_fields.model_copy(update=updates)

    schedule_result: ScheduleTableResult | None = None
    if any(
        profile.category_for(target.number, target.title) is Cat.UP_GRAFIK
        for target in template.sections
    ):
        schedule_result = build_schedule_table(
            presentation_up,
            duration=document_fields.duration,
            final_attestation=document_fields.final_attestation,
            profile_id=profile.profile_id,
        )

    module_result: ModuleProgramResult | None = None
    if any(
        profile.category_for(target.number, target.title) is Cat.UP_MODULES
        for target in template.sections
    ):
        module_result = build_module_program_table(
            presentation_up,
            profile_id=profile.profile_id,
            curriculum_result=curriculum_result,
            competency_result=competency_result,
        )

    assessment_result: AssessmentTablesResult | None = None
    if any(
        profile.category_for(target.number, target.title)
        in {Cat.CURRENT_CONTROL, Cat.FINAL_ATTESTATION}
        for target in template.sections
    ):
        assessment_result = build_assessment_tables(
            presentation_up,
            final_attestation=document_fields.final_attestation,
            profile_id=profile.profile_id,
            schedule_trace=schedule_result.trace if schedule_result is not None else None,
        )

    conditions_result: OrganizationalConditionsResult | None = None
    if any(
        profile.category_for(target.number, target.title)
        in {
            Cat.METHODOLOGICAL_SUPPORT,
            Cat.MATERIAL_CONDITIONS,
            Cat.STAFFING,
        }
        for target in template.sections
    ):
        conditions_result = build_organizational_conditions(
            presentation_up,
            profile_id=profile.profile_id,
        )

    sections: list[RpdSection] = []
    for target in template.sections:
        key = section_key(target.number, target.title)
        category = _resolve_category(
            roles.get(key),
            target.number,
            target.title,
            profile,
            is_container=target.is_container,
        )
        if category is Cat.CONTAINER:
            continue  # структурный заголовок не является отдельным содержательным разделом
        if category is Cat.BOILERPLATE:
            if not profile.should_review_boilerplate(target.number, target.title):
                continue
            section = _template_owned_section(target)
        else:
            section = _section_content(
                target.number,
                target.title,
                category,
                presentation_up,
                llm_client,
                profile,
                prose_texts.get(key),
                competency_result,
                curriculum_result,
                passport_table,
                schedule_result,
                module_result,
                assessment_result,
                conditions_result,
                template_table_header=(list(target.tables[0].header) if target.tables else None),
            )
        # A profile-owned normative section is not model output, so a prose critic must not flag it.
        section.flag = "" if category is Cat.BOILERPLATE else flags.get(key, "")
        section.initial_content_hash = section.content_hash()
        sections.append(section)
    source_payload = json.dumps(up.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    build = RpdBuild(
        template_profile_id=profile.profile_id,
        template_report=template_report,
        program_title=up.title,
        total_hours=up.total_hours,
        source_spec=source_spec,
        document_fields=document_fields,
        sections=sections,
        preflight_issues=preflight_issues,
        generation_trace=RpdGenerationTrace(
            source_sha256=hashlib.sha256(source_payload.encode("utf-8")).hexdigest(),
            template_sha256=template_report.template_sha256,
            template_profile_id=profile.profile_id,
            build_duration_ms=round((time.perf_counter() - started) * 1000),
            llm_events=collect_llm_events(llm_client),
        ),
        competency_trace=competency_result.trace if competency_result is not None else None,
        curriculum_trace=curriculum_result.trace if curriculum_result is not None else None,
        schedule_trace=schedule_result.trace if schedule_result is not None else None,
        assessment_trace=assessment_result.trace if assessment_result is not None else None,
        conditions_trace=conditions_result.trace if conditions_result is not None else None,
        module_evidence=list(module_result.evidence) if module_result is not None else [],
    )
    build.quality = evaluate_build_quality(build)
    return build


def _resolve_category(
    role: str | None,
    number: str,
    title: str,
    profile: TemplateProfile,
    *,
    is_container: bool = False,
) -> Cat:
    """Роль из консилиума → категория; при отсутствии/невалидной — детерминированный fallback."""
    if is_container:
        return Cat.CONTAINER
    fallback = profile.category_for(number, title)
    # Таблицы УП и нормативный boilerplate определяются контрактом профиля. LLM не должна
    # превращать их в свободную прозу либо человеческий gap.
    if fallback in {
        Cat.UP_PLAN,
        Cat.UP_GRAFIK,
        Cat.UP_MODULES,
        Cat.UP_COMP,
        Cat.PASSPORT,
        Cat.PROF_ACTIVITY,
        Cat.CURRENT_CONTROL,
        Cat.FINAL_ATTESTATION,
        Cat.ASSESSMENT_EVIDENCE,
        Cat.METHODOLOGICAL_SUPPORT,
        Cat.MATERIAL_CONDITIONS,
        Cat.STAFFING,
        Cat.PROGRAM_DEVELOPERS,
        Cat.BOILERPLATE,
    }:
        return fallback
    if role:
        try:
            resolved = Cat(role)
            return fallback if resolved is Cat.CONTAINER else resolved
        except ValueError:
            pass
    return fallback


def _section_content(
    number: str,
    title: str,
    category: Cat,
    up: UpPlan,
    client: Any,
    profile: TemplateProfile,
    prose_override: list[str] | None = None,
    competency_result: CompetencyTableResult | None = None,
    curriculum_result: CurriculumTableResult | None = None,
    passport_table: _TableContent | None = None,
    schedule_result: ScheduleTableResult | None = None,
    module_result: ModuleProgramResult | None = None,
    assessment_result: AssessmentTablesResult | None = None,
    conditions_result: OrganizationalConditionsResult | None = None,
    template_table_header: list[str] | None = None,
) -> RpdSection:
    base = RpdSection(number=number, title=title, category=category.value, label=_CATEGORY_LABEL.get(category, ""))
    if category is Cat.PASSPORT:
        base.kind = "table"
        if passport_table is not None:
            base.table_header, base.table_rows = passport_table
        else:
            base.table_header, base.table_rows = build_passport_table(up, client, profile.profile_id)
        return base
    if category is Cat.PROF_ACTIVITY:
        base.kind = "table"
        base.table_header, base.table_rows = build_professional_activity_table(up, client, profile.profile_id)
        return base
    if category is Cat.CURRENT_CONTROL:
        if assessment_result is None:
            base.paragraphs = [GAP_MARKER]
            return base
        base.kind = "table"
        base.table_header = list(assessment_result.current_header)
        base.table_rows = [list(row) for row in assessment_result.current_rows]
        return base
    if category is Cat.FINAL_ATTESTATION:
        if assessment_result is None:
            base.paragraphs = [GAP_MARKER]
            return base
        base.kind = "table"
        base.table_header = list(assessment_result.final_header)
        base.table_rows = [list(row) for row in assessment_result.final_rows]
        return base
    if category is Cat.ASSESSMENT_EVIDENCE:
        base.kind = "table"
        base.editable = False
        requirements = list(module_result.evidence) if module_result is not None else []
        base.table_header, base.table_rows = build_assessment_evidence_table(
            requirements,
            template_header=template_table_header,
        )
        return base
    if category is Cat.METHODOLOGICAL_SUPPORT:
        if conditions_result is None:
            base.paragraphs = [GAP_MARKER]
            return base
        base.kind = "table"
        base.table_header = list(conditions_result.methodological_header)
        base.table_rows = [list(row) for row in conditions_result.methodological_rows]
        return base
    if category is Cat.MATERIAL_CONDITIONS:
        if conditions_result is None:
            base.paragraphs = [GAP_MARKER]
            return base
        base.kind = "table"
        base.table_header = list(conditions_result.material_header)
        base.table_rows = [list(row) for row in conditions_result.material_rows]
        return base
    if category is Cat.STAFFING:
        if conditions_result is None:
            base.paragraphs = [GAP_MARKER]
            return base
        base.kind = "table"
        base.table_header = list(conditions_result.staffing_header)
        base.table_rows = [list(row) for row in conditions_result.staffing_rows]
        return base
    if category is Cat.PROGRAM_DEVELOPERS:
        base.kind = "table"
        base.table_header, base.table_rows = build_program_developers_table(profile.profile_id)
        return base
    if category is Cat.UP_COMP:
        if competency_result is not None and competency_result.rows:
            base.kind = "table"
            base.table_header = list(competency_result.header)
            base.table_rows = [list(row) for row in competency_result.rows]
            return base
        base.paragraphs = [GAP_MARKER]
        return base
    if category is Cat.UP_PLAN:
        if curriculum_result is not None and curriculum_result.rows:
            base.kind = "table"
            base.table_header = list(curriculum_result.header)
            base.table_rows = [list(row) for row in curriculum_result.rows]
            return base
        base.paragraphs = [GAP_MARKER]
        return base
    if category is Cat.UP_GRAFIK:
        if schedule_result is not None and schedule_result.rows:
            base.kind = "table"
            base.table_header = list(schedule_result.header)
            base.table_rows = [list(row) for row in schedule_result.rows]
            return base
        base.paragraphs = [GAP_MARKER]
        return base
    if category is Cat.GENERATE:
        base.kind = "paragraphs"
        paragraphs = prose_override or (generate_prose(title, up, client) if client is not None else [])
        base.paragraphs = paragraphs or [_GENERATE_PLACEHOLDER]
        return base
    if category is Cat.PLATFORM:
        base.kind = "paragraphs"
        draft = prose_override or (generate_prose(title, up, client) if client is not None else [])
        base.paragraphs = [*draft, _PLATFORM_NOTE]
        return base
    if category is Cat.UP_MODULES:
        if module_result is not None and module_result.blocks:
            base.kind = "module_blocks"
            base.module_blocks = [block.model_copy(deep=True) for block in module_result.blocks]
            return base
        table = profile.table_for(category, up)
        if table is not None:
            base.kind = "table"
            base.table_header, base.table_rows = table
            return base
        base.paragraphs = [GAP_MARKER]  # нет данных УП
        return base
    # Cat.GAP — раздел для методиста: общий черновик (консилиум/LLM) + явная пометка.
    base.kind = "paragraphs"
    draft = prose_override or (generate_prose(title, up, client) if client is not None else [])
    base.paragraphs = [*draft, _GAP_NOTE]
    return base


def _template_owned_section(target: TargetSection) -> RpdSection:
    """Expose a profile-owned template section for HITL review AND editing.

    The section is editable, но ``preserve_template`` остаётся: пока методист не правил, ``render_build``
    держит исходную вёрстку DOCX дословно (фидельность формы). Как только контент отличается от
    ``template_baseline_hash`` — рендер вставляет правки методиста вместо шаблонной вёрстки.
    """
    section = RpdSection(
        number=target.number,
        title=target.title,
        category=Cat.BOILERPLATE.value,
        label=_CATEGORY_LABEL[Cat.BOILERPLATE],
        editable=True,
        preserve_template=True,
    )
    if target.tables:
        table = target.tables[0]
        section.kind = "table"
        section.table_header = list(table.header)
        section.table_rows = [list(row) for row in table.rows]
    else:
        section.paragraphs = list(target.paragraphs)
    # Снимок исходной вёрстки: пока равен текущему контенту — рендер держит DOCX дословно.
    section.template_baseline_hash = section.content_hash()
    return section
