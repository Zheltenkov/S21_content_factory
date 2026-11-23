"""Strict table contract for section 7 of the internal DPO template."""

from __future__ import annotations

from typing import Final

from .template_profiles import BUILTIN_PK_PROFILE_ID

PROGRAM_DEVELOPERS_HEADER: Final[tuple[str, ...]] = (
    "Роль",
    "Ф.И.О.",
    "Ученая степень / квалификация",
    "Должность / место работы",
    "Подпись / дата",
)

PROGRAM_DEVELOPER_ROLES: Final[tuple[str, ...]] = (
    "Руководитель программы",
    "Разработчик",
    "Эксперт / рецензент",
)


def build_program_developers_table(profile_id: str) -> tuple[list[str], list[list[str]]]:
    """Return the exact editable section-7 table without inventing personal data."""
    if profile_id != BUILTIN_PK_PROFILE_ID:
        raise ValueError(f"Unsupported program-developers profile: {profile_id}")

    rows = [
        ["Руководитель программы", "[Указать]", "[Указать]", "[Указать]", "[Указать]"],
        ["Разработчик", "[Указать]", "[Указать]", "[Указать]", "[Указать]"],
        ["Эксперт / рецензент", "[При наличии]", "[Указать]", "[Указать]", "[Указать]"],
    ]
    return list(PROGRAM_DEVELOPERS_HEADER), rows


def validate_program_developers_table(header: list[str], rows: list[list[str]]) -> list[str]:
    """Validate the fixed schema and role rows used by the internal template."""
    violations: list[str] = []
    if tuple(header) != PROGRAM_DEVELOPERS_HEADER:
        violations.append("Нарушена схема колонок раздела 7.")
    if [row[0] for row in rows if row] != list(PROGRAM_DEVELOPER_ROLES):
        violations.append("Нарушен состав или порядок ролей раздела 7.")
    for index, row in enumerate(rows, start=1):
        if len(row) != len(PROGRAM_DEVELOPERS_HEADER):
            violations.append(f"Строка {index} должна содержать 5 ячеек.")
    return violations


__all__ = [
    "PROGRAM_DEVELOPER_ROLES",
    "PROGRAM_DEVELOPERS_HEADER",
    "build_program_developers_table",
    "validate_program_developers_table",
]
