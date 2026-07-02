"""Contracts and deterministic guards for methodologist change requests."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from content_gen.practice_contract import find_non_raw_material_issues

ChangeTargetStage = Literal[
    "context",
    "task_planning",
    "title",
    "annotation",
    "skeleton",
    "theory",
    "practice",
    "dataset",
    "final",
]
ChangeScope = Literal["local_section_only", "task_only", "materials_only"]
ConflictSeverity = Literal["warning", "hard"]


class MethodologistChangeRequest(BaseModel):
    """Typed request from a methodologist for a paused generation."""

    model_config = ConfigDict(str_strip_whitespace=True)

    target_stage: ChangeTargetStage = "final"
    target_selector: str = Field(default="", max_length=300)
    scope: ChangeScope = "local_section_only"
    instruction: str = Field(min_length=3, max_length=4000)
    issue_codes: list[str] = Field(default_factory=list)
    forbidden_changes: list[str] = Field(default_factory=list)
    expected_outcome: str = Field(default="", max_length=1000)

    @field_validator("issue_codes", "forbidden_changes", mode="before")
    @classmethod
    def _coerce_string_list(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value


class ChangeRequestConflict(BaseModel):
    """A deterministic conflict between requested edit and generator policy."""

    code: str
    message: str
    severity: ConflictSeverity = "hard"
    details: dict[str, object] = Field(default_factory=dict)


_FIX_INTENT_MARKERS = (
    "убери",
    "убрать",
    "удали",
    "удалить",
    "исключи",
    "исключить",
    "запрети",
    "запретить",
    "не добавляй",
    "не включай",
    "очисти",
    "remove",
    "delete",
    "exclude",
    "avoid",
    "do not",
)

_SOLUTION_LEAK_RE = re.compile(
    r"(?:добавь|включи|дай|покажи|сформируй|подготовь|include|add|provide|show)"
    r".{0,80}"
    r"(?:готов\w*\s+(?:ответ|решени|реестр|матриц|таблиц)|"
    r"правильн\w*\s+ответ|полуответ|answer key|ready answer|solution)",
    re.I | re.S,
)

_BROAD_REWRITE_RE = re.compile(
    r"(?:перепиш\w*|измени|переделай|перегенерируй|rewrite|regenerate|change)"
    r".{0,80}"
    r"(?:весь|всю|полностью|целиком|all|entire|whole)",
    re.I | re.S,
)

_POLICY_OVERRIDE_RE = re.compile(
    r"(?:игнорируй|отключи|обойди|не проверяй|skip|disable|ignore|bypass)"
    r".{0,80}"
    r"(?:валидатор|guard|policy|hard\s+rule|контракт|провер\w*)",
    re.I | re.S,
)

_STATIC_INSTRUCTION_LEAK_RE = re.compile(
    r"(?:добавь|включи|верни|подтяни|insert|include|add)"
    r".{0,80}"
    r"(?:p2p|peer[-\s]?to[-\s]?peer|репозитор|gitlab|статическ\w+\s+инструкц|проверку\s+через)",
    re.I | re.S,
)


def validate_methodologist_change_request(
    request: MethodologistChangeRequest,
) -> list[ChangeRequestConflict]:
    """Validate a requested edit before it can affect generation state."""
    instruction = request.instruction or ""
    combined_text = " ".join(
        [
            request.target_selector or "",
            instruction,
            request.expected_outcome or "",
            " ".join(request.forbidden_changes or []),
        ]
    )
    normalized = combined_text.lower()
    fix_intent = any(marker in normalized for marker in _FIX_INTENT_MARKERS)

    conflicts: list[ChangeRequestConflict] = []

    material_issues = find_non_raw_material_issues(instruction)
    if material_issues and not fix_intent:
        conflicts.append(
            ChangeRequestConflict(
                code="raw_evidence_contract_violation",
                message=(
                    "Запрос просит или описывает материалы как готовую учебную заготовку. "
                    "Materials должны оставаться raw evidence без готовых решений."
                ),
                details={"issues": material_issues},
            )
        )

    if _SOLUTION_LEAK_RE.search(instruction) and not fix_intent:
        conflicts.append(
            ChangeRequestConflict(
                code="solution_leak_request",
                message=(
                    "Запрос ведет к готовым ответам или полуответам в материалах/практике. "
                    "Студент должен выводить артефакты сам."
                ),
                details={"scope": request.scope, "target_stage": request.target_stage},
            )
        )

    if (
        request.scope == "materials_only"
        and not fix_intent
        and re.search(r"(?:готов\w*|заполненн\w*)\s+(?:ответ|решени|реестр|матриц|таблиц)", normalized)
    ):
        conflicts.append(
            ChangeRequestConflict(
                code="materials_scope_solution_risk",
                message="Правка materials_only не может добавлять готовые или заполненные артефакты.",
                details={"scope": request.scope},
            )
        )

    if request.scope == "local_section_only" and _BROAD_REWRITE_RE.search(instruction) and not fix_intent:
        conflicts.append(
            ChangeRequestConflict(
                code="scope_expansion_violation",
                message="Локальная правка не может требовать полного переписывания результата.",
                details={"scope": request.scope},
            )
        )

    if _POLICY_OVERRIDE_RE.search(instruction) and not fix_intent:
        conflicts.append(
            ChangeRequestConflict(
                code="policy_override_request",
                message="Запрос не может отключать валидаторы, guard-правила или hard contracts.",
            )
        )

    if request.target_stage == "theory" and _STATIC_INSTRUCTION_LEAK_RE.search(instruction) and not fix_intent:
        conflicts.append(
            ChangeRequestConflict(
                code="static_instruction_leak_request",
                message=(
                    "Правка теории не должна подтягивать статическую инструкцию "
                    "про репозитории, P2P или проверку, если это не предмет проекта."
                ),
                details={"target_stage": request.target_stage},
            )
        )

    if request.scope == "local_section_only" and not request.target_selector:
        conflicts.append(
            ChangeRequestConflict(
                code="missing_local_selector",
                message="Для локальной правки желательно указать главу, часть или артефакт.",
                severity="warning",
            )
        )

    return conflicts


def has_hard_conflicts(conflicts: list[ChangeRequestConflict]) -> bool:
    """Return True when at least one conflict must block the request."""
    return any(conflict.severity == "hard" for conflict in conflicts)
