"""Редактируемый черновик РПД: УП → структурированные разделы (для human-in-the-loop).

Разделяет пайплайн: build (здесь) собирает содержимое каждого редактируемого раздела в
:class:`RpdBuild`; :func:`render_selective.render_build` кладёт черновик в DOCX по шаблону. Между
ними встаёт редактор — методист правит разделы, потом экспорт. boilerplate-разделы (форма ДПО) в
черновик не попадают: их держит шаблон.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .consilium import run_consilium, section_key
from .dpo_spec import Cat
from .llm_runtime import RpdLlmEvent, collect_llm_events
from .prose import generate_prose
from .quality import QualityIssue, RpdQualityReport, evaluate_build_quality, preflight_up_plan
from .source_fidelity import CanonicalProgramSpec, canonicalize_up_plan
from .template import load_rpd_template
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

#: Понятные методисту ярлыки источника раздела для навигатора редактора.
_CATEGORY_LABEL = {
    Cat.UP_PLAN: "из УП",
    Cat.UP_GRAFIK: "из УП",
    Cat.UP_MODULES: "из УП + платформа",
    Cat.UP_COMP: "из УП",
    Cat.GENERATE: "сгенерировано",
    Cat.PLATFORM: "с платформы",
    Cat.GAP: "методист",
}


class RpdSection(BaseModel):
    """Один редактируемый раздел черновика: либо абзацы (проза/пробел), либо таблица (данные УП)."""

    number: str = ""
    title: str = ""
    category: str = ""
    label: str = ""
    kind: Literal["paragraphs", "table"] = "paragraphs"
    paragraphs: list[str] = Field(default_factory=list)
    table_header: list[str] = Field(default_factory=list)
    table_rows: list[list[str]] = Field(default_factory=list)
    done: bool = False  # методист отметил раздел готовым
    flag: str = ""  # замечание валидатора-критика (если есть)
    initial_content_hash: str = ""

    def content_hash(self) -> str:
        """Return a stable hash used to measure accepted-without-edit sections."""
        payload = {
            "kind": self.kind,
            "paragraphs": self.paragraphs,
            "table_header": self.table_header,
            "table_rows": self.table_rows,
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
    delivery_mode: str = ""
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
            ("delivery_mode", "форма обучения"),
            ("final_attestation", "итоговая аттестация"),
            ("issued_document", "выдаваемый документ"),
            ("city", "город"),
            ("year", "год"),
        ]


class RpdBuild(BaseModel):
    """Черновик РПД: название программы + редактируемые разделы (без boilerplate — тот в шаблоне)."""

    schema_version: int = 4
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
    specs = [
        {"number": section.number, "title": section.title, "is_container": section.is_container}
        for section in template.sections
    ]
    roles, prose_texts, flags = run_consilium(specs, up, llm_client)

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
        if category in {Cat.BOILERPLATE, Cat.CONTAINER}:
            continue  # форма ДПО и структурные заголовки остаются в шаблоне без отдельного текста
        section = _section_content(
            target.number,
            target.title,
            category,
            up,
            llm_client,
            profile,
            prose_texts.get(key),
        )
        section.flag = flags.get(key, "")
        section.initial_content_hash = section.content_hash()
        sections.append(section)
    source_payload = json.dumps(up.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    build = RpdBuild(
        template_profile_id=profile.profile_id,
        template_report=template_report,
        program_title=up.title,
        total_hours=up.total_hours,
        source_spec=source_spec,
        sections=sections,
        preflight_issues=preflight_issues,
        generation_trace=RpdGenerationTrace(
            source_sha256=hashlib.sha256(source_payload.encode("utf-8")).hexdigest(),
            template_sha256=template_report.template_sha256,
            template_profile_id=profile.profile_id,
            build_duration_ms=round((time.perf_counter() - started) * 1000),
            llm_events=collect_llm_events(llm_client),
        ),
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
    if fallback in {Cat.UP_PLAN, Cat.UP_GRAFIK, Cat.UP_MODULES, Cat.UP_COMP, Cat.BOILERPLATE}:
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
) -> RpdSection:
    base = RpdSection(number=number, title=title, category=category.value, label=_CATEGORY_LABEL.get(category, ""))
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
    if category in {Cat.UP_PLAN, Cat.UP_GRAFIK, Cat.UP_MODULES, Cat.UP_COMP}:
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
