"""Structured passport contract for the standard School 21 DPO-PK template.

The LLM may suggest semantic values, but it never controls the table schema, row order,
program facts, or DOCX placement.  A template profile can later select a different contract
without changing the orchestration or the editor representation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .up_models import UpPlan

PASSPORT_PROMPT_VERSION = "rpd-passport-school-pk-v1"
PASSPORT_REVIEW_MARKER = "[Требует уточнения методистом]"
PASSPORT_HEADER = ["Параметр", "Значение"]


class PassportFieldSpec(BaseModel):
    """One row of a template-owned passport table."""

    key: str
    label: str
    source: Literal["up_title", "up_hours", "fixed", "llm"]
    fixed_value: str = ""
    document_field: str = ""


class PassportContract(BaseModel):
    """Stable schema selected by a concrete template profile."""

    profile_id: str
    fields: list[PassportFieldSpec]

    @property
    def labels(self) -> list[str]:
        return [field.label for field in self.fields]


class SchoolPkPassportSuggestions(BaseModel):
    """Typed semantic suggestions produced by the LLM for human review."""

    audience: str = Field(default="", max_length=700)
    admission_requirements: str = Field(default="", max_length=700)
    delivery_mode: str = Field(default="", max_length=500)
    duration: str = Field(default="", max_length=300)
    study_mode: str = Field(default="", max_length=500)
    final_attestation: str = Field(default="", max_length=500)

    @field_validator("*", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> str:
        """Normalize provider output before it reaches a table cell."""
        return " ".join(str(value or "").split()).strip()


SCHOOL_PK_PASSPORT_CONTRACT = PassportContract(
    profile_id="dpo_pk_v1",
    fields=[
        PassportFieldSpec(key="program_title", label="Наименование", source="up_title"),
        PassportFieldSpec(
            key="program_type",
            label="Вид программы",
            source="fixed",
            fixed_value="Дополнительная профессиональная программа — программа повышения квалификации",
        ),
        PassportFieldSpec(
            key="audience",
            label="Категория слушателей",
            source="llm",
            document_field="audience",
        ),
        PassportFieldSpec(
            key="admission_requirements",
            label="Требования к поступающим",
            source="llm",
            document_field="admission_requirements",
        ),
        PassportFieldSpec(key="total_hours", label="Объем", source="up_hours"),
        PassportFieldSpec(
            key="delivery_mode",
            label="Форма обучения",
            source="llm",
            document_field="delivery_mode",
        ),
        PassportFieldSpec(
            key="duration",
            label="Продолжительность",
            source="llm",
            document_field="duration",
        ),
        PassportFieldSpec(
            key="study_mode",
            label="Режим занятий",
            source="llm",
            document_field="study_mode",
        ),
        PassportFieldSpec(
            key="final_attestation",
            label="Форма итоговой аттестации",
            source="llm",
            document_field="final_attestation",
        ),
        PassportFieldSpec(
            key="issued_document",
            label="Документ о квалификации",
            source="fixed",
            fixed_value="Удостоверение о повышении квалификации установленного образца",
            document_field="issued_document",
        ),
    ],
)


def contract_for_profile(profile_id: str) -> PassportContract:
    """Return the explicit contract for a supported template family."""
    if profile_id == SCHOOL_PK_PASSPORT_CONTRACT.profile_id:
        return SCHOOL_PK_PASSPORT_CONTRACT.model_copy(deep=True)
    raise ValueError(f"Passport contract is not registered for template profile: {profile_id}")


def build_passport_table(up: UpPlan, client: Any, profile_id: str) -> tuple[list[str], list[list[str]]]:
    """Combine verified UP facts, fixed normative values and typed LLM suggestions."""
    contract = contract_for_profile(profile_id)
    suggestions = _suggest_semantic_values(up, client)
    rows: list[list[str]] = []
    for field in contract.fields:
        value = _field_value(field, up, suggestions)
        rows.append([field.label, value or PASSPORT_REVIEW_MARKER])
    return list(PASSPORT_HEADER), rows


def document_field_updates(rows: list[list[str]], profile_id: str) -> dict[str, str]:
    """Map editable passport rows back to canonical document metadata fields."""
    contract = contract_for_profile(profile_id)
    values_by_label = {
        row[0].strip(): row[1].strip()
        for row in rows
        if len(row) >= 2 and row[0].strip()
    }
    return {
        field.document_field: values_by_label.get(field.label, "")
        for field in contract.fields
        if field.document_field
    }


def merge_document_fields(
    rows: list[list[str]], fields: Mapping[str, object] | object, profile_id: str
) -> list[list[str]]:
    """Apply non-empty metadata edits to matching passport rows without changing its schema."""
    contract = contract_for_profile(profile_id)
    field_by_label = {field.label: field for field in contract.fields if field.document_field}
    result = [list(row) for row in rows]
    for row in result:
        if len(row) < 2:
            continue
        spec = field_by_label.get(row[0].strip())
        if spec is None:
            continue
        raw_value = fields.get(spec.document_field, "") if isinstance(fields, Mapping) else getattr(
            fields, spec.document_field, ""
        )
        value = str(raw_value or "").strip()
        if value:
            row[1] = value
    return result


def validate_passport_rows(rows: list[list[str]], profile_id: str) -> tuple[list[str], list[str], list[str]]:
    """Return missing labels, duplicate labels and labels with empty values."""
    contract = contract_for_profile(profile_id)
    actual_labels = [row[0].strip() for row in rows if row and row[0].strip()]
    missing = [label for label in contract.labels if label not in actual_labels]
    duplicates = sorted({label for label in actual_labels if actual_labels.count(label) > 1})
    values_by_label = {row[0].strip(): row[1].strip() if len(row) > 1 else "" for row in rows if row}
    empty = [label for label in contract.labels if label in values_by_label and not values_by_label[label]]
    return missing, duplicates, empty


def _field_value(
    field: PassportFieldSpec, up: UpPlan, suggestions: SchoolPkPassportSuggestions
) -> str:
    """Resolve one cell while keeping source ownership explicit."""
    if field.source == "up_title":
        return up.title.strip()
    if field.source == "up_hours":
        return f"{up.total_hours} академических часов" if up.total_hours.strip() else ""
    if field.source == "fixed":
        return field.fixed_value
    return str(getattr(suggestions, field.key, "") or "").strip()


def _suggest_semantic_values(up: UpPlan, client: Any) -> SchoolPkPassportSuggestions:
    """Ask the LLM only for semantic draft values, with a safe empty fallback."""
    complete_structured = getattr(client, "complete_structured", None)
    if not callable(complete_structured):
        return SchoolPkPassportSuggestions()

    system = (
        "Ты методист дополнительного профессионального образования. Заполни только смысловые поля "
        "паспорта программы повышения квалификации. Название, вид программы, объем и выдаваемый документ "
        "заполняются кодом и в ответ не входят. Значения являются предложениями для обязательной проверки "
        "методистом. Не меняй структуру и не добавляй поля."
    )
    example = SchoolPkPassportSuggestions(
        audience="Специалисты и руководители, которым необходимы навыки по тематике программы",
        admission_requirements="Наличие или получение среднего профессионального и/или высшего образования",
        delivery_mode="Очно-заочная, с применением электронного обучения и дистанционных образовательных технологий",
        duration="4 недели",
        study_mode="Не более 8 академических часов в неделю",
        final_attestation="Защита практической работы",
    )
    user = (
        f"Название программы: {up.title or 'не указано'}\n"
        f"Объем: {up.total_hours or 'не указан'} академических часов\n"
        f"Модули: {'; '.join(module.title for module in up.modules) or 'не указаны'}\n"
        f"Результаты обучения: {'; '.join(up.all_outcomes()[:20]) or 'не указаны'}\n\n"
        "Пример формы ответа для другой программы (не копируй предметную область):\n"
        f"{example.model_dump_json(indent=2)}\n\n"
        "Сформируй значения для текущей программы. Категория слушателей и итоговая аттестация должны "
        "соответствовать ее содержанию. Продолжительность и режим являются проектными предложениями, а не "
        "фактами источника."
    )
    try:
        result = complete_structured(
            output_model=SchoolPkPassportSuggestions,
            system=system,
            user=user,
            llm_role="rpd_prose",
            prompt_version=PASSPORT_PROMPT_VERSION,
        )
        return SchoolPkPassportSuggestions.model_validate(result)
    except Exception:  # noqa: BLE001 - the draft remains editable and visibly marked for review
        return SchoolPkPassportSuggestions()


__all__ = [
    "PASSPORT_HEADER",
    "PASSPORT_PROMPT_VERSION",
    "PASSPORT_REVIEW_MARKER",
    "PassportContract",
    "SCHOOL_PK_PASSPORT_CONTRACT",
    "SchoolPkPassportSuggestions",
    "build_passport_table",
    "contract_for_profile",
    "document_field_updates",
    "merge_document_fields",
    "validate_passport_rows",
]
