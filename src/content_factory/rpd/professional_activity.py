"""Structured professional-activity table for the internal School 21 DPO-PK profile.

The template owns headers, row order and evidence labels. The LLM may suggest only the semantic
middle-column values from the curriculum plan; it cannot turn the table into prose or claim a
professional standard that was not supplied.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .up_models import UpPlan

PROFESSIONAL_ACTIVITY_PROMPT_VERSION = "rpd-professional-activity-school-pk-v1"
PROFESSIONAL_ACTIVITY_REVIEW_MARKER = "[Требует уточнения методистом]"


class ProfessionalActivityRowSpec(BaseModel):
    """One profile-owned row whose position and evidence source are deterministic."""

    key: Literal["activity_area", "professional_tasks", "required_skills"]
    label: str
    basis: str


class ProfessionalActivityContract(BaseModel):
    """Stable DOCX table schema selected by a template profile."""

    profile_id: str
    header: list[str]
    rows: list[ProfessionalActivityRowSpec]

    @property
    def labels(self) -> list[str]:
        return [row.label for row in self.rows]


class SchoolPkProfessionalActivitySuggestions(BaseModel):
    """Typed LLM suggestions for cells that require semantic interpretation of the UP."""

    activity_area: str = Field(default="", max_length=1200)
    professional_tasks: list[str] = Field(default_factory=list, max_length=5)
    required_skills: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("activity_area", mode="before")
    @classmethod
    def normalize_area(cls, value: object) -> str:
        """Collapse provider formatting into one editable table-cell value."""
        return " ".join(str(value or "").split()).strip()

    @field_validator("professional_tasks", "required_skills", mode="before")
    @classmethod
    def normalize_items(cls, value: object) -> list[str]:
        """Accept a list or a delimited provider string and remove empty/duplicate items."""
        raw_items = re.split(r"[;\n•]+", value) if isinstance(value, str) else value
        if not isinstance(raw_items, (list, tuple)):
            return []
        result: list[str] = []
        for raw in raw_items:
            item = " ".join(str(raw or "").split()).strip(" .;-")
            if item and item.casefold() not in {existing.casefold() for existing in result}:
                result.append(item)
        return result


SCHOOL_PK_PROFESSIONAL_ACTIVITY_CONTRACT = ProfessionalActivityContract(
    profile_id="dpo_pk_v1",
    header=["Позиция", "Минимальный пример заполнения", "Основание"],
    rows=[
        ProfessionalActivityRowSpec(
            key="activity_area",
            label="Область деятельности",
            basis="Анализ профессиональной деятельности по содержанию учебного плана",
        ),
        ProfessionalActivityRowSpec(
            key="professional_tasks",
            label="Профессиональная задача",
            basis="Анализ профессиональной деятельности и содержание учебного плана",
        ),
        ProfessionalActivityRowSpec(
            key="required_skills",
            label="Необходимые умения",
            basis="Планируемые результаты обучения и содержание учебного плана",
        ),
    ],
)


def contract_for_profile(profile_id: str) -> ProfessionalActivityContract:
    """Return the explicit contract only for profiles that declare this table."""
    if profile_id == SCHOOL_PK_PROFESSIONAL_ACTIVITY_CONTRACT.profile_id:
        return SCHOOL_PK_PROFESSIONAL_ACTIVITY_CONTRACT.model_copy(deep=True)
    raise ValueError(f"Professional-activity contract is not registered for template profile: {profile_id}")


def build_professional_activity_table(
    up: UpPlan,
    client: Any,
    profile_id: str,
) -> tuple[list[str], list[list[str]]]:
    """Build fixed rows and evidence sources around typed semantic LLM suggestions."""
    contract = contract_for_profile(profile_id)
    suggestions = _suggest_values(up, client)
    values = {
        "activity_area": suggestions.activity_area,
        "professional_tasks": "; ".join(suggestions.professional_tasks),
        "required_skills": "; ".join(suggestions.required_skills),
    }
    rows = [
        [row.label, values[row.key] or PROFESSIONAL_ACTIVITY_REVIEW_MARKER, row.basis]
        for row in contract.rows
    ]
    return list(contract.header), rows


def validate_professional_activity_rows(
    header: list[str],
    rows: list[list[str]],
    profile_id: str,
) -> tuple[list[str], list[str], list[str]]:
    """Return schema violations, duplicate positions and positions without a value/basis."""
    contract = contract_for_profile(profile_id)
    schema_violations = [] if header == contract.header else ["header"]
    actual_labels = [row[0].strip() for row in rows if row and row[0].strip()]
    schema_violations.extend(f"missing:{label}" for label in contract.labels if label not in actual_labels)
    schema_violations.extend(
        f"unexpected:{label}" for label in actual_labels if label not in contract.labels
    )
    if actual_labels != contract.labels:
        schema_violations.append("row_order")
    schema_violations.extend(
        f"columns:row:{index}"
        for index, row in enumerate(rows, start=1)
        if len(row) != len(contract.header)
    )
    duplicates = sorted({label for label in actual_labels if actual_labels.count(label) > 1})
    incomplete = [
        row[0].strip() or f"row:{index}"
        for index, row in enumerate(rows, start=1)
        if (
            len(row) < 3
            or not row[1].strip()
            or row[1].strip() == PROFESSIONAL_ACTIVITY_REVIEW_MARKER
            or not row[2].strip()
        )
    ]
    return schema_violations, duplicates, incomplete


def _suggest_values(up: UpPlan, client: Any) -> SchoolPkProfessionalActivitySuggestions:
    """Ask the model for typed values; unavailable inference remains visible to the methodologist."""
    complete_structured = getattr(client, "complete_structured", None)
    if not callable(complete_structured):
        return SchoolPkProfessionalActivitySuggestions()

    example = SchoolPkProfessionalActivitySuggestions(
        activity_area="Организационно-управленческая и информационно-аналитическая деятельность",
        professional_tasks=["Подготовка и представление информации для принятия решений"],
        required_skills=["Выбирать инструмент", "Формировать запрос", "Проверять и оформлять результат"],
    )
    modules = "; ".join(module.title for module in up.modules) or "не указаны"
    projects = "; ".join(row.name for module in up.modules for row in module.rows if row.name) or "не указаны"
    outcomes = "; ".join(up.all_outcomes()[:30]) or "не указаны"
    system = (
        "Ты методист ДПО. Заполни только смысловые значения таблицы «Характеристика профессиональной "
        "деятельности» программы повышения квалификации. Структура, позиции и колонка оснований задаются "
        "кодом. Опирайся только на учебный план: не придумывай должности, трудовые функции, коды или "
        "профессиональные стандарты. Верни одну область деятельности, 2-4 профессиональные задачи и 3-7 "
        "необходимых умений. Формулировки должны различаться по смыслу и быть пригодны для таблицы РПД."
    )
    user = (
        f"Название программы: {up.title or 'не указано'}\n"
        f"Объем: {up.total_hours or 'не указан'} академических часов\n"
        f"Модули: {modules}\n"
        f"Практические проекты: {projects}\n"
        f"Планируемые результаты: {outcomes}\n\n"
        "Пример структуры для другой программы (не копируй предметную область):\n"
        f"{example.model_dump_json(indent=2)}\n\n"
        "Сформируй значения для текущей программы. Область описывает контекст деятельности, задачи - "
        "типовые профессиональные действия, умения - наблюдаемые способы выполнения этих задач."
    )
    try:
        result = complete_structured(
            output_model=SchoolPkProfessionalActivitySuggestions,
            system=system,
            user=user,
            llm_role="rpd_prose",
            prompt_version=PROFESSIONAL_ACTIVITY_PROMPT_VERSION,
        )
        return SchoolPkProfessionalActivitySuggestions.model_validate(result)
    except Exception:  # noqa: BLE001 - a visible review marker is safer than invented content
        return SchoolPkProfessionalActivitySuggestions()


__all__ = [
    "PROFESSIONAL_ACTIVITY_PROMPT_VERSION",
    "PROFESSIONAL_ACTIVITY_REVIEW_MARKER",
    "ProfessionalActivityContract",
    "SCHOOL_PK_PROFESSIONAL_ACTIVITY_CONTRACT",
    "SchoolPkProfessionalActivitySuggestions",
    "build_professional_activity_table",
    "contract_for_profile",
    "validate_professional_activity_rows",
]
