"""Source-bound tables for organizational and pedagogical conditions 5.1-5.3.

The curriculum can establish the program topic, learning format and tools. It cannot establish
bibliographic requisites, verified URLs, legal access grounds or a teacher's identity. Those fields
remain explicit human checkpoints instead of LLM-generated facts.
"""

from __future__ import annotations

import re

from .organizational_conditions_contracts import (
    ConditionsTableValidation,
    OrganizationalConditionsResult,
    OrganizationalConditionsTrace,
)
from .up_models import UpPlan

SCHOOL_PK_PROFILE_ID = "dpo_pk_v1"

METHODOLOGICAL_HEADER = ["Категория", "Минимальный пример", "Реквизиты / ссылка", "Доступ"]
METHODOLOGICAL_CATEGORIES = [
    "Основная литература",
    "Нормативные документы",
    "Электронные ресурсы",
    "Методические материалы",
]
MATERIAL_HEADER = ["Вид работы", "Помещение / среда", "Оборудование и ПО", "Параметры", "Основание доступа"]
MATERIAL_WORK_KINDS = ["Лекции", "Практические занятия"]
STAFFING_HEADER = ["Роль", "Требования", "Практический опыт", "Ф.И.О. / должность", "Подтверждение"]

_HUMAN_MARKER_RE = re.compile(r"\[Методист:[^\]]+\]", re.IGNORECASE)
_TECHNICAL_TITLE_RE = re.compile(r"(?:curriculum[_ -]?plan|учебный план|\.csv$|\.xlsx?$)", re.IGNORECASE)
_TOOL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GitHub", re.compile(r"\bgithub\b", re.IGNORECASE)),
    ("Git", re.compile(r"\bgit\b", re.IGNORECASE)),
    ("Docker", re.compile(r"\bdocker\b", re.IGNORECASE)),
    ("IDE", re.compile(r"\bide\b", re.IGNORECASE)),
    ("LLM-сервисы", re.compile(r"\bllm\b", re.IGNORECASE)),
    ("CRM", re.compile(r"\bcrm\b", re.IGNORECASE)),
    ("CI/CD", re.compile(r"\bci\s*/\s*cd\b", re.IGNORECASE)),
    ("API-инструменты", re.compile(r"\bapi\b", re.IGNORECASE)),
    ("Helpdesk", re.compile(r"\bhelpdesk\b", re.IGNORECASE)),
    ("No-code-платформы", re.compile(r"\bno[- ]?code\b", re.IGNORECASE)),
    ("средства аналитики", re.compile(r"аналитик", re.IGNORECASE)),
    ("средства мониторинга и логирования", re.compile(r"мониторинг|логирован", re.IGNORECASE)),
    ("облачная инфраструктура", re.compile(r"облак", re.IGNORECASE)),
)


def build_organizational_conditions(up: UpPlan, *, profile_id: str) -> OrganizationalConditionsResult:
    """Build the three fixed internal tables without inventing external evidence."""
    if profile_id != SCHOOL_PK_PROFILE_ID:
        raise ValueError(f"Organizational conditions contract is not registered for profile: {profile_id}")
    topic = _program_topic(up)
    software_values = _source_software_values(up)
    tools = _detected_tools(software_values)
    display_tools = _display_tools(tools)
    practical_equipment = ", ".join(
        [
            "компьютер слушателя",
            "современный браузер",
            "доступ в интернет и LMS/ЭИОС",
            *(display_tools or ["программные средства по тематике программы"]),
        ]
    )

    methodological_rows = [
        [
            "Основная литература",
            f"Учебные издания по тематике программы «{topic}»",
            "[Методист: укажите проверенные библиографические реквизиты]",
            "ЭБС / библиотека",
        ],
        [
            "Нормативные документы",
            "Нормативно-правовые акты в сфере дополнительного профессионального образования",
            (
                "Федеральный закон от 29.12.2012 № 273-ФЗ; "
                "приказ Минобрнауки России от 24.03.2025 № 266"
            ),
            "Открытый доступ",
        ],
        [
            "Электронные ресурсы",
            _electronic_resources_example(display_tools),
            "[Методист: укажите проверенные URL официальной документации]",
            "Открытый доступ",
        ],
        [
            "Методические материалы",
            "Инструкции к практическим заданиям, взаимной проверке и итоговой аттестации",
            "Электронный курс программы в LMS/ЭИОС",
            "Личный кабинет",
        ],
    ]
    material_rows = [
        [
            "Лекции",
            "Учебная аудитория или среда ВКС",
            "Компьютер преподавателя, средства демонстрации и ВКС, браузер, доступ в интернет и LMS/ЭИОС",
            "По числу учебных групп",
            "[Методист: подтвердите собственность, договор или иное основание доступа]",
        ],
        [
            "Практические занятия",
            "Компьютерный класс или удалённое рабочее место слушателя",
            practical_equipment,
            "Одно рабочее место на слушателя",
            "[Методист: подтвердите собственность, договор или иное основание доступа]",
        ],
    ]
    staffing_rows = [
        [
            "Преподаватель",
            f"Высшее образование и квалификация, соответствующие тематике программы «{topic}»",
            f"Опыт практической работы по тематике программы «{topic}»",
            "[Методист: укажите Ф.И.О. и должность]",
            "Документы об образовании, сведения о квалификации и резюме",
        ]
    ]
    return OrganizationalConditionsResult(
        methodological_header=list(METHODOLOGICAL_HEADER),
        methodological_rows=methodological_rows,
        material_header=list(MATERIAL_HEADER),
        material_rows=material_rows,
        staffing_header=list(STAFFING_HEADER),
        staffing_rows=staffing_rows,
        trace=OrganizationalConditionsTrace(
            program_topic=topic,
            source_software_values=software_values,
            detected_tools=tools,
            human_required_fields=[
                "5.1:библиографические реквизиты",
                "5.1:проверенные URL",
                "5.2:основание доступа",
                "5.3:Ф.И.О. и должность",
            ],
        ),
    )


def validate_methodological_support_table(
    header: list[str], rows: list[list[str]]
) -> ConditionsTableValidation:
    """Validate exact 5.1 categories and separate missing evidence from semantic defects."""
    result = _validate_fixed_table(header, METHODOLOGICAL_HEADER, rows, METHODOLOGICAL_CATEGORIES)
    by_category = _rows_by_label(rows)
    normative = by_category.get("Нормативные документы", [])
    if len(normative) >= 3 and not ("273-ФЗ" in normative[2] and "266" in normative[2]):
        result.methodology_violations.append("normative_requisites")
    electronic = by_category.get("Электронные ресурсы", [])
    if len(electronic) >= 3 and electronic[2] and not (
        _HUMAN_MARKER_RE.search(electronic[2]) or "http" in electronic[2].casefold()
    ):
        result.methodology_violations.append("electronic_resource_url")
    _collect_human_markers(rows, result)
    return result


def validate_material_conditions_table(
    header: list[str], rows: list[list[str]]
) -> ConditionsTableValidation:
    """Validate the two template work kinds and actionable equipment/access descriptions."""
    result = _validate_fixed_table(header, MATERIAL_HEADER, rows, MATERIAL_WORK_KINDS)
    for index, row in enumerate(rows, start=1):
        if len(row) != len(MATERIAL_HEADER):
            continue
        if len(_compact(row[1]).split()) < 4:
            result.methodology_violations.append(f"environment:row:{index}")
        equipment = _compact(row[2])
        if len(equipment.split()) < 7 or "интернет" not in equipment.casefold():
            result.methodology_violations.append(f"equipment:row:{index}")
        if len(_compact(row[3]).split()) < 4:
            result.methodology_violations.append(f"parameters:row:{index}")
    _collect_human_markers(rows, result)
    return result


def validate_staffing_table(header: list[str], rows: list[list[str]]) -> ConditionsTableValidation:
    """Validate the staffing schema while keeping personal identity human-owned."""
    result = _validate_fixed_table(header, STAFFING_HEADER, rows, ["Преподаватель"])
    for index, row in enumerate(rows, start=1):
        if len(row) != len(STAFFING_HEADER):
            continue
        if len(_compact(row[1]).split()) < 6:
            result.methodology_violations.append(f"requirements:row:{index}")
        if len(_compact(row[2]).split()) < 6:
            result.methodology_violations.append(f"experience:row:{index}")
        if not re.search(r"образован|квалификац|резюме", row[4], re.IGNORECASE):
            result.methodology_violations.append(f"confirmation:row:{index}")
    _collect_human_markers(rows, result)
    return result


def _validate_fixed_table(
    header: list[str],
    expected_header: list[str],
    rows: list[list[str]],
    expected_labels: list[str],
) -> ConditionsTableValidation:
    result = ConditionsTableValidation()
    if header != expected_header:
        result.schema_violations.append("header")
    actual_labels: list[str] = []
    for index, row in enumerate(rows, start=1):
        if len(row) != len(expected_header):
            result.schema_violations.append(f"columns:row:{index}")
            continue
        actual_labels.append(_compact(row[0]))
        for column, cell in enumerate(row, start=1):
            if not _compact(cell):
                result.value_violations.append(f"empty:row:{index}:column:{column}")
    if actual_labels != expected_labels:
        result.label_violations.append("row_labels")
    return result


def _collect_human_markers(rows: list[list[str]], result: ConditionsTableValidation) -> None:
    for row_index, row in enumerate(rows, start=1):
        for column_index, cell in enumerate(row, start=1):
            if _HUMAN_MARKER_RE.search(cell):
                result.review_required.append(f"row:{row_index}:column:{column_index}")


def _rows_by_label(rows: list[list[str]]) -> dict[str, list[str]]:
    return {_compact(row[0]): row for row in rows if row}


def _program_topic(up: UpPlan) -> str:
    title = _compact(up.title)
    if title and not _TECHNICAL_TITLE_RE.search(title):
        return title[:240]
    content = " ".join(
        [
            title,
            *(module.title for module in up.modules),
            *(row.name for module in up.modules for row in module.rows),
        ]
    )
    if re.search(r"биоинформат", content, re.IGNORECASE):
        return "биоинформатика и применение искусственного интеллекта"
    if re.search(r"предприним|стартап|\bmvp\b|цифров\w*\s+продукт", content, re.IGNORECASE):
        if re.search(r"\bai\b|\bии\b|\bllm\b|искусственн\w*\s+интеллект", content, re.IGNORECASE):
            return "технологическое предпринимательство, запуск цифровых продуктов и применение AI-инструментов"
        return "технологическое предпринимательство и запуск цифровых продуктов"
    module_titles = [_compact(module.title) for module in up.modules if _compact(module.title)]
    if module_titles:
        return "; ".join(module_titles[:2])[:160]
    return "тематика программы"


def _source_software_values(up: UpPlan) -> list[str]:
    result: list[str] = []
    for module in up.modules:
        for row in module.rows:
            value = _compact(row.software)
            if value and value not in result:
                result.append(value)
    return result


def _detected_tools(values: list[str]) -> list[str]:
    content = "\n".join(values)
    return [label for label, pattern in _TOOL_PATTERNS if pattern.search(content)]


def _display_tools(tools: list[str]) -> list[str]:
    if len(tools) <= 8:
        return tools
    return [*tools[:8], "иные средства, указанные в УП"]


def _electronic_resources_example(tools: list[str]) -> str:
    if tools:
        return "Официальная документация применяемых средств: " + ", ".join(tools)
    return "Официальная документация применяемых цифровых инструментов"


def _compact(value: str) -> str:
    return " ".join(str(value or "").split())


__all__ = [
    "MATERIAL_HEADER",
    "MATERIAL_WORK_KINDS",
    "METHODOLOGICAL_CATEGORIES",
    "METHODOLOGICAL_HEADER",
    "SCHOOL_PK_PROFILE_ID",
    "STAFFING_HEADER",
    "build_organizational_conditions",
    "validate_material_conditions_table",
    "validate_methodological_support_table",
    "validate_staffing_table",
]
