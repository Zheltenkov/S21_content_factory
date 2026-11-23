"""Versioned local professional-standard catalog used by the RPD competency pipeline.

The generation request never depends on the availability of the Ministry of Labour website.
An external ingestion job may refresh a JSON snapshot; this module only validates, fingerprints and
searches that snapshot.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator

from .up_models import UpPlan

PROFESSIONAL_STANDARD_REGISTRY_URL = (
    "https://profstandart.rosmintrud.ru/obshchiy-informatsionnyy-blok/"
    "natsionalnyy-reestr-professionalnykh-standartov/reestr-professionalnykh-standartov/"
)
PROFESSIONAL_STANDARD_CATALOG_ENV = "RPD_PROFSTANDARD_CATALOG_PATH"


class ProfessionalStandardFunction(BaseModel):
    """One structured labour function extracted from an approved professional standard."""

    code: str
    title: str
    qualification_level: str = ""
    labor_actions: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    required_knowledge: list[str] = Field(default_factory=list)

    @field_validator("code", "title", "qualification_level", mode="before")
    @classmethod
    def normalize_scalar(cls, value: object) -> str:
        return _compact(value)

    @field_validator("labor_actions", "required_skills", "required_knowledge", mode="before")
    @classmethod
    def normalize_list(cls, value: object) -> list[str]:
        return _text_list(value)


class ProfessionalStandardRecord(BaseModel):
    """Registry metadata plus the evidence-bearing functions of one approved standard."""

    registry_number: str = ""
    code: str
    title: str
    professional_area: str = ""
    activity_type: str = ""
    order_reference: str = ""
    source_url: str = ""
    functions: list[ProfessionalStandardFunction] = Field(default_factory=list)

    @field_validator(
        "registry_number",
        "code",
        "title",
        "professional_area",
        "activity_type",
        "order_reference",
        "source_url",
        mode="before",
    )
    @classmethod
    def normalize_text(cls, value: object) -> str:
        return _compact(value)


class ProfessionalStandardCatalog(BaseModel):
    """Reproducible snapshot that can be searched without network access."""

    schema_version: int = 1
    snapshot_at: str = ""
    source_url: str = PROFESSIONAL_STANDARD_REGISTRY_URL
    source_sha256: str = ""
    standards: list[ProfessionalStandardRecord] = Field(default_factory=list)
    load_error: str = ""

    @model_validator(mode="after")
    def codes_are_unique(self) -> ProfessionalStandardCatalog:
        codes = [standard.code for standard in self.standards]
        if len(codes) != len(set(codes)):
            raise ValueError("Professional-standard catalog contains duplicate codes")
        return self

    @classmethod
    def from_json_bytes(cls, payload: bytes) -> ProfessionalStandardCatalog:
        """Validate a snapshot and bind its exact source fingerprint."""
        catalog = cls.model_validate_json(payload)
        return catalog.model_copy(
            update={"source_sha256": hashlib.sha256(payload).hexdigest(), "load_error": ""}
        )

    def by_code(self, code: str) -> ProfessionalStandardRecord | None:
        normalized = _compact(code)
        return next((standard for standard in self.standards if standard.code == normalized), None)

    def search(self, up: UpPlan, *, limit: int = 12) -> list[ProfessionalStandardRecord]:
        """Return deterministic lexical candidates for a later LLM matcher and HITL selection."""
        query = " ".join(
            [
                up.title,
                *(module.title for module in up.modules),
                *(row.name for module in up.modules for row in module.rows),
                *(skill for module in up.modules for row in module.rows for skill in row.skills),
                *up.all_outcomes(),
            ]
        )
        query_terms = _terms(query)
        if not query_terms:
            return []

        scored: list[tuple[int, str, ProfessionalStandardRecord]] = []
        for standard in self.standards:
            searchable = " ".join(
                [
                    standard.title,
                    standard.professional_area,
                    standard.activity_type,
                    *(function.title for function in standard.functions),
                    *(item for function in standard.functions for item in function.required_skills),
                ]
            )
            standard_terms = _terms(searchable)
            overlap = len(query_terms & standard_terms)
            title_bonus = 2 * len(query_terms & _terms(standard.title))
            score = overlap + title_bonus
            if score:
                scored.append((score, standard.code, standard))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [standard.model_copy(deep=True) for _, _, standard in scored[: max(limit, 0)]]


def load_configured_professional_standard_catalog(
    path: str | Path | None = None,
) -> ProfessionalStandardCatalog:
    """Load an explicit/env snapshot; any I/O or schema failure degrades to an empty catalog."""
    configured = Path(path) if path is not None else _configured_path()
    if configured is None:
        return ProfessionalStandardCatalog()
    try:
        return ProfessionalStandardCatalog.from_json_bytes(configured.read_bytes())
    except Exception as exc:  # noqa: BLE001 - generation must survive a stale/broken snapshot
        return ProfessionalStandardCatalog(load_error=type(exc).__name__)


def _configured_path() -> Path | None:
    value = os.getenv(PROFESSIONAL_STANDARD_CATALOG_ENV, "").strip()
    return Path(value) if value else None


def _compact(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _text_list(value: object) -> list[str]:
    raw = re.split(r"[;\n•]+", value) if isinstance(value, str) else value
    if not isinstance(raw, (list, tuple)):
        return []
    result: list[str] = []
    for item in raw:
        text = _compact(item).strip(" .;-")
        if text and text.casefold() not in {existing.casefold() for existing in result}:
            result.append(text)
    return result


def _terms(value: str) -> set[str]:
    stop_words = {
        "для",
        "или",
        "при",
        "что",
        "как",
        "это",
        "программа",
        "обучение",
        "модуль",
        "навык",
    }
    return {
        token
        for token in re.findall(r"[a-zа-яё0-9]+", value.casefold())
        if len(token) >= 3 and token not in stop_words
    }


__all__ = [
    "PROFESSIONAL_STANDARD_CATALOG_ENV",
    "PROFESSIONAL_STANDARD_REGISTRY_URL",
    "ProfessionalStandardCatalog",
    "ProfessionalStandardFunction",
    "ProfessionalStandardRecord",
    "load_configured_professional_standard_catalog",
]
