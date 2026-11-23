"""Deterministic working-program rows and screenshot evidence for section 3.3.1.

The built-in School DPO profile is platform-first: every curriculum item represents an assignment
in LMS/ЭИОС and has a separate control step.  The document therefore needs two auditable pieces of
visual evidence per item.  External profiles may opt in only when their source row explicitly
mentions online/LMS delivery.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from .competency_contracts import CompetencyTableResult
from .curriculum_contracts import CurriculumTableResult
from .up_models import UpModule, UpPlan, UpRow

EvidenceKind = Literal["assignment", "control"]
EvidenceStatus = Literal["pending", "uploaded"]

MODULE_PROGRAM_HEADER = ["№", "Тема и краткое содержание", "Вид работы", "Часы", "Методы", "Контроль"]
MODULE_PASSPORT_HEADER = ["Параметр", "Минимальный пример заполнения"]
ASSESSMENT_EVIDENCE_DEFAULT_HEADER = [
    "Здесь нужен скрин из задания (если программа реализуется в очно-заочной форме)."
]
SCHOOL_PK_PROFILE_ID = "dpo_pk_v1"

_EVIDENCE_TOKEN_RE = re.compile(r"\{\{RPD_EVIDENCE:([a-z0-9-]+)\}\}")
_ONLINE_RE = re.compile(
    r"(?:\blms\b|эиос|платформ\w*|онлайн|online|интернет|дистанц\w*|электронн\w+\s+обуч)",
    re.IGNORECASE,
)
_INDIVIDUAL_RE = re.compile(r"индивидуаль", re.IGNORECASE)
_GROUP_RE = re.compile(r"группов", re.IGNORECASE)


class PlatformEvidenceRequirement(BaseModel):
    """One required screenshot, bound to exactly one assignment or control in one module."""

    evidence_id: str
    module_index: int = Field(ge=1)
    item_index: int = Field(ge=1)
    control_index: int = Field(default=0, ge=0)
    module_title: str
    item_title: str
    kind: EvidenceKind
    control_title: str = ""
    reason: str
    required: bool = True
    status: EvidenceStatus = "pending"
    relative_path: str = ""
    original_filename: str = ""
    content_type: str = ""
    sha256: str = ""

    @property
    def token(self) -> str:
        return f"{{{{RPD_EVIDENCE:{self.evidence_id}}}}}"

    @property
    def kind_label(self) -> str:
        return "задания" if self.kind == "assignment" else "контроля"

    @property
    def subject(self) -> str:
        return self.item_title if self.kind == "assignment" else self.control_title


class ModuleProgramBlock(BaseModel):
    """Three template-owned tables that form one complete section 3.3.1 module block."""

    module_index: int = Field(ge=1)
    module_title: str
    passport_header: list[str] = Field(default_factory=lambda: list(MODULE_PASSPORT_HEADER))
    passport_rows: list[list[str]] = Field(default_factory=list)
    topics_header: list[str] = Field(default_factory=lambda: list(MODULE_PROGRAM_HEADER))
    topics_rows: list[list[str]] = Field(default_factory=list)
    evidence_header: list[str] = Field(
        default_factory=lambda: list(ASSESSMENT_EVIDENCE_DEFAULT_HEADER)
    )
    evidence_rows: list[list[str]] = Field(default_factory=list)

    def all_rows(self) -> list[list[str]]:
        """Return every editable or evidence row for hashing and source-fidelity checks."""
        return [*self.passport_rows, *self.topics_rows, *self.evidence_rows]


class ModuleProgramResult(BaseModel):
    """Repeated module blocks plus evidence requirements persisted with an RPD build."""

    blocks: list[ModuleProgramBlock] = Field(default_factory=list)
    evidence: list[PlatformEvidenceRequirement] = Field(default_factory=list)

    def evidence_rows(self) -> list[list[str]]:
        """Return screenshot-marker rows without mixing them into the topics table."""
        return [row for block in self.blocks for row in block.evidence_rows]


class ModuleEvidenceValidation(BaseModel):
    """Deterministic validation findings consumed by the global quality gate."""

    contract_violations: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    invalid_uploads: list[str] = Field(default_factory=list)


def build_module_program_table(
    up: UpPlan,
    *,
    profile_id: str,
    curriculum_result: CurriculumTableResult | None = None,
    competency_result: CompetencyTableResult | None = None,
) -> ModuleProgramResult:
    """Build one passport, topics table and screenshot area for every source module."""
    blocks: list[ModuleProgramBlock] = []
    evidence: list[PlatformEvidenceRequirement] = []
    for module_index, module in enumerate(up.modules, start=1):
        topic_rows: list[list[str]] = []
        evidence_rows: list[list[str]] = []
        module_requirements: list[PlatformEvidenceRequirement] = []
        for item_index, source_row in enumerate(module.rows, start=1):
            control = _control_title(source_row)
            evidence_required, reason = _evidence_policy(source_row, profile_id)
            theme = source_row.name.strip()
            if source_row.description:
                theme = f"{theme}: {source_row.description}" if theme else source_row.description
            topic_rows.append(
                [
                    f"{module_index}.{item_index}",
                    theme,
                    _work_kind(source_row),
                    source_row.hours,
                    _teaching_method(source_row, evidence_required=evidence_required),
                    control,
                ]
            )

            if not evidence_required:
                continue
            evidence_specs: list[tuple[EvidenceKind, int]] = [("assignment", 0)]
            evidence_specs.extend(("control", index) for index in range(1, _control_count(source_row) + 1))
            for kind, control_index in evidence_specs:
                evidence_control = (
                    f"{control} №{control_index}"
                    if kind == "control" and _control_count(source_row) > 1
                    else control
                )
                requirement = PlatformEvidenceRequirement(
                    evidence_id=_evidence_id(module_index, item_index, kind, control_index),
                    module_index=module_index,
                    item_index=item_index,
                    control_index=control_index,
                    module_title=module.title or f"Модуль {module_index}",
                    item_title=source_row.name or f"Задание {item_index}",
                    kind=kind,
                    control_title=evidence_control,
                    reason=reason,
                )
                evidence.append(requirement)
                module_requirements.append(requirement)
                evidence_rows.append([pending_evidence_cell(requirement)])

        topic_rows.append(["", "ИТОГО", "", module.hours, "", ""])
        blocks.append(
            ModuleProgramBlock(
                module_index=module_index,
                module_title=module.title or f"Модуль {module_index}",
                passport_rows=_module_passport_rows(
                    up,
                    module,
                    module_index,
                    curriculum_result=curriculum_result,
                    competency_result=competency_result,
                    requirements=module_requirements,
                ),
                topics_rows=topic_rows,
                evidence_rows=evidence_rows,
            )
        )
    return ModuleProgramResult(blocks=blocks, evidence=evidence)


def build_assessment_evidence_table(
    requirements: list[PlatformEvidenceRequirement],
    *,
    template_header: list[str] | None = None,
) -> tuple[list[str], list[list[str]]]:
    """Project existing control evidence into section 4.4 without duplicating uploaded files."""
    header = list(template_header or ASSESSMENT_EVIDENCE_DEFAULT_HEADER)
    rows = [
        [
            f"Модуль {requirement.module_index} «{requirement.module_title}»: "
            f"контроль «{requirement.control_title}» по заданию «{requirement.item_title}». "
            f"{requirement.token}"
        ]
        for requirement in requirements
        if requirement.kind == "control"
    ]
    return header, rows


def pending_evidence_cell(requirement: PlatformEvidenceRequirement) -> str:
    """Return a visible human instruction plus a stable renderer token."""
    if requirement.kind == "assignment":
        message = (
            f"[Методист: вставьте скрин задания «{requirement.item_title}» из модуля "
            f"«{requirement.module_title}» с платформы/LMS/ЭИОС]"
        )
    else:
        message = (
            f"[Методист: вставьте скрин контроля «{requirement.control_title}» по заданию "
            f"«{requirement.item_title}» из модуля «{requirement.module_title}» с платформы/LMS/ЭИОС]"
        )
    return f"{message} {requirement.token}"


def uploaded_evidence_cell(requirement: PlatformEvidenceRequirement) -> str:
    """Keep the renderer token while replacing the blocking instruction with an audit caption."""
    return (
        f"Скриншот {requirement.kind_label} «{requirement.subject}» приложен "
        f"({requirement.original_filename}). {requirement.token}"
    )


def evidence_id_from_cell(value: str) -> str | None:
    """Extract a renderer-safe evidence identifier from one editable table cell."""
    match = _EVIDENCE_TOKEN_RE.search(value)
    return match.group(1) if match else None


def visible_evidence_text(value: str) -> str:
    """Hide the internal token from the exported document and browser captions."""
    return _EVIDENCE_TOKEN_RE.sub("", value).strip()


def synchronize_evidence_row(rows: list[list[str]], requirement: PlatformEvidenceRequirement) -> bool:
    """Update the matching table marker without relying on mutable row positions."""
    replacement = uploaded_evidence_cell(requirement) if requirement.status == "uploaded" else pending_evidence_cell(requirement)
    for row in rows:
        for index, cell in enumerate(row):
            if evidence_id_from_cell(cell) == requirement.evidence_id:
                row[index] = replacement
                return True
    return False


def synchronize_module_evidence(
    blocks: list[ModuleProgramBlock],
    requirement: PlatformEvidenceRequirement,
) -> bool:
    """Update one screenshot marker inside its module-owned evidence area."""
    block = next(
        (item for item in blocks if item.module_index == requirement.module_index),
        None,
    )
    return synchronize_evidence_row(block.evidence_rows, requirement) if block is not None else False


def validate_module_evidence(
    requirements: list[PlatformEvidenceRequirement],
    rows: list[list[str]],
    *,
    expected_minimum_count: int | None = None,
    expected_items: list[tuple[int, int]] | None = None,
) -> ModuleEvidenceValidation:
    """Validate cardinality, row bindings and upload metadata without opening files."""
    contract: list[str] = []
    missing: list[str] = []
    invalid: list[str] = []
    ids = [item.evidence_id for item in requirements]
    if len(set(ids)) != len(ids):
        contract.append("duplicate_evidence_ids")
    if expected_minimum_count is not None and len(requirements) < expected_minimum_count:
        contract.append(f"count:minimum={expected_minimum_count}:actual={len(requirements)}")
    requirement_keys = {
        (item.module_index, item.item_index, item.kind)
        for item in requirements
    }
    for module_index, item_index in expected_items or []:
        for kind in ("assignment", "control"):
            if (module_index, item_index, kind) not in requirement_keys:
                contract.append(f"requirement_missing:m{module_index:03d}-i{item_index:03d}-{kind}")

    row_ids = {
        evidence_id
        for row in rows
        for cell in row
        if (evidence_id := evidence_id_from_cell(cell)) is not None
    }
    for requirement in requirements:
        if requirement.evidence_id not in row_ids:
            contract.append(f"marker_missing:{requirement.evidence_id}")
        if requirement.required and requirement.status != "uploaded":
            missing.append(_requirement_label(requirement))
        if requirement.status == "uploaded":
            expected_prefix = f"evidence/{requirement.evidence_id}."
            metadata_valid = all(
                [
                    requirement.relative_path,
                    requirement.original_filename,
                    requirement.content_type,
                    requirement.sha256,
                ]
            )
            if (
                not metadata_valid
                or not requirement.relative_path.startswith(expected_prefix)
                or requirement.content_type not in {"image/png", "image/jpeg"}
                or re.fullmatch(r"[0-9a-f]{64}", requirement.sha256) is None
            ):
                invalid.append(requirement.evidence_id)
    return ModuleEvidenceValidation(
        contract_violations=contract,
        missing_evidence=missing,
        invalid_uploads=invalid,
    )


def validate_module_program_blocks(
    blocks: list[ModuleProgramBlock],
    *,
    expected_project_counts: list[int],
) -> list[str]:
    """Validate the internal template's per-module three-surface structural contract."""
    violations: list[str] = []
    expected_indices = list(range(1, len(expected_project_counts) + 1))
    actual_indices = [block.module_index for block in blocks]
    if actual_indices != expected_indices:
        violations.append(f"module_indices:expected={expected_indices}:actual={actual_indices}")
    if len(blocks) != len(expected_project_counts):
        violations.append(
            f"module_count:expected={len(expected_project_counts)}:actual={len(blocks)}"
        )

    passport_labels = [
        "Наименование",
        "Цель",
        "Связанные компетенции",
        "Результаты",
        "Трудоемкость",
        "Форма контроля",
    ]
    for block, expected_count in zip(blocks, expected_project_counts, strict=False):
        prefix = f"module:{block.module_index}"
        if block.passport_header != MODULE_PASSPORT_HEADER:
            violations.append(f"{prefix}:passport_header")
        if [row[0] if row else "" for row in block.passport_rows] != passport_labels:
            violations.append(f"{prefix}:passport_rows")
        if block.topics_header != MODULE_PROGRAM_HEADER:
            violations.append(f"{prefix}:topics_header")
        if len(block.topics_rows) != expected_count + 1:
            violations.append(
                f"{prefix}:topic_count:expected={expected_count}:actual={max(len(block.topics_rows) - 1, 0)}"
            )
        for item_index, row in enumerate(block.topics_rows[:expected_count], start=1):
            if not row or row[0].strip() != f"{block.module_index}.{item_index}":
                violations.append(f"{prefix}:topic_number:{item_index}")
        if (
            not block.topics_rows
            or len(block.topics_rows[-1]) < 2
            or block.topics_rows[-1][1].strip().casefold() != "итого"
        ):
            violations.append(f"{prefix}:total_row")
        if block.evidence_header != ASSESSMENT_EVIDENCE_DEFAULT_HEADER:
            violations.append(f"{prefix}:evidence_header")
    return violations


def _evidence_policy(row: UpRow, profile_id: str) -> tuple[bool, str]:
    # The internal School template is known to be implemented on the platform; future profiles
    # require an explicit online/LMS signal from the imported curriculum row.
    if profile_id == SCHOOL_PK_PROFILE_ID:
        return True, "builtin_school_platform_policy"
    source = " ".join([row.fmt, row.software, row.description])
    return (True, "source_online_marker") if _ONLINE_RE.search(source) else (False, "not_platform_based")


def _evidence_id(module_index: int, item_index: int, kind: EvidenceKind, control_index: int) -> str:
    suffix = f"-{control_index:03d}" if kind == "control" else ""
    return f"m{module_index:03d}-i{item_index:03d}-{kind}{suffix}"


def _control_title(row: UpRow) -> str:
    return "Практическое задание с взаимной проверкой" if row.control() == "p2p-проверка" else "Проект"


def _control_count(row: UpRow) -> int:
    """Treat the source p2p count as distinct control instances; every one needs evidence."""
    try:
        parsed = int(float(row.p2p.strip().replace(",", "."))) if row.p2p.strip() else 0
    except ValueError:
        parsed = 0
    return max(parsed, 1)


def _work_kind(row: UpRow) -> str:
    if _INDIVIDUAL_RE.search(row.fmt):
        return "Индивидуальная работа"
    if _GROUP_RE.search(row.fmt):
        return "Групповая работа"
    return row.fmt.strip()


def _teaching_method(row: UpRow, *, evidence_required: bool) -> str:
    """Project a source format into a concise method label used by the template."""
    work_kind = _work_kind(row).casefold()
    if "лекц" in work_kind:
        return "Обзор с разбором примеров"
    if "самостоят" in work_kind:
        return "Работа в ЭИОС" if evidence_required else "Самостоятельная работа"
    return "Практикум в LMS/ЭИОС" if evidence_required else "Практикум"


def _module_passport_rows(
    up: UpPlan,
    module: UpModule,
    module_index: int,
    *,
    curriculum_result: CurriculumTableResult | None,
    competency_result: CompetencyTableResult | None,
    requirements: list[PlatformEvidenceRequirement],
) -> list[list[str]]:
    """Fill the six semantic passport cells from the same source contracts as sections 2.2/3.1."""
    controls = list(
        dict.fromkeys(
            requirement.control_title
            for requirement in requirements
            if requirement.kind == "control" and requirement.control_title.strip()
        )
    )
    if not controls:
        controls = list(dict.fromkeys(_control_title(row) for row in module.rows))
    outcomes = list(
        dict.fromkeys(
            outcome
            for row in module.rows
            for outcome in row.outcomes()
            if outcome.strip()
        )
    )
    return [
        ["Наименование", module.title or f"Модуль {module_index}"],
        ["Цель", module.goals.strip() or _fallback_module_goal(module)],
        [
            "Связанные компетенции",
            _module_competency_codes(up, module, competency_result),
        ],
        ["Результаты", "; ".join(outcomes)],
        [
            "Трудоемкость",
            _module_workload(module, module_index, curriculum_result),
        ],
        ["Форма контроля", "; ".join(controls)],
    ]


def _fallback_module_goal(module: UpModule) -> str:
    subject = next((row.name.strip() for row in module.rows if row.name.strip()), module.title.strip())
    return f"Освоение способов выполнения практических задач модуля «{subject}»" if subject else ""


def _module_workload(
    module: UpModule,
    module_index: int,
    curriculum_result: CurriculumTableResult | None,
) -> str:
    labels = ("лекции", "практические занятия", "самостоятельная работа", "контроль")
    values: list[str] = []
    total = module.hours
    if curriculum_result is not None and 0 < module_index <= len(curriculum_result.rows):
        row = curriculum_result.rows[module_index - 1]
        if len(row) >= 7:
            total = row[2]
            values = row[3:7]
    if not values:
        values = ["0", module.hours or "0", "0", "0"]
    details = ", ".join(
        f"{label} - {value}"
        for label, value in zip(labels, values, strict=True)
        if value.strip() not in {"", "0", "0.0", "—", "-"}
    )
    return f"{total} академических часов" + (f": {details}" if details else "")


def _module_competency_codes(
    up: UpPlan,
    module: UpModule,
    competency_result: CompetencyTableResult | None,
) -> str:
    if competency_result is None:
        return ""
    coded_rows = [
        (row[0].strip(), row[1].strip())
        for row in competency_result.rows
        if len(row) >= 2 and row[0].strip()
    ]
    if not coded_rows:
        return ""

    module_skills = list(
        dict.fromkeys(skill.strip() for row in module.rows for skill in row.skills if skill.strip())
    )
    all_skills = list(
        dict.fromkeys(
            skill.strip()
            for source_module in up.modules
            for row in source_module.rows
            for skill in row.skills
            if skill.strip()
        )
    )
    selected: list[str] = []
    if len(coded_rows) == len(all_skills):
        code_by_skill = {
            skill.casefold(): code
            for skill, (code, _title) in zip(all_skills, coded_rows, strict=True)
        }
        selected.extend(
            code_by_skill[skill.casefold()]
            for skill in module_skills
            if skill.casefold() in code_by_skill
        )
    else:
        for code, title in coded_rows:
            normalized_title = title.casefold()
            if any(
                skill.casefold() in normalized_title or normalized_title in skill.casefold()
                for skill in module_skills
            ):
                selected.append(code)
    return ", ".join(dict.fromkeys(selected))


def _requirement_label(requirement: PlatformEvidenceRequirement) -> str:
    return (
        f"модуль {requirement.module_index}, задание {requirement.item_index}: "
        f"скрин {requirement.kind_label} «{requirement.subject}»"
    )


__all__ = [
    "ASSESSMENT_EVIDENCE_DEFAULT_HEADER",
    "MODULE_PASSPORT_HEADER",
    "MODULE_PROGRAM_HEADER",
    "ModuleEvidenceValidation",
    "ModuleProgramBlock",
    "ModuleProgramResult",
    "PlatformEvidenceRequirement",
    "build_assessment_evidence_table",
    "build_module_program_table",
    "evidence_id_from_cell",
    "pending_evidence_cell",
    "synchronize_evidence_row",
    "synchronize_module_evidence",
    "uploaded_evidence_cell",
    "validate_module_evidence",
    "validate_module_program_blocks",
    "visible_evidence_text",
]
