"""
api/routers/curriculum.py

API endpoint для загрузки и парсинга учебного плана (УП / Паспорт программы).

Поддерживает:
- Загрузка CSV файла
- Парсинг структуры: блоки → проекты
- Автозаполнение полей формы генерации
"""

import csv
import io
import logging
import re
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from content_factory.api.db.session import get_db_session
from content_factory.api.dependencies import get_current_user
from content_factory.api.integrations.spravochnik_curriculum_sync import (
    convert_spravochnik_plan_to_generator_curriculum,
    sync_spravochnik_curriculum_plans,
)
from content_factory.api.utils.file_validation import MAX_FILE_SIZE, read_upload_limited, validate_file
from content_factory.generation.models.curriculum import (
    CurriculumPlan,
    CurriculumProject,
    ThematicBlock,
)

router = APIRouter(prefix="/curriculum", tags=["curriculum"])
logger = logging.getLogger("content_factory.api.curriculum")


CURRICULUM_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "block_name": (
        "тематический блок",
        "название всего блока (если делим на блоки)",
    ),
    "block_goals": ("цели блока",),
    "order": ("№",),
    "title": ("название проекта", "название контентной единицы"),
    "description": ("краткое описание проекта", "краткое описание"),
    "expert_notes": ("что нужно разработать эксперту",),
    "learning_outcomes": (
        "образовательные результаты (знает, понимает, умеет)",
        "образовательные результаты",
    ),
    "skills": ("список навыков",),
    "audience_level": ("уровень аудитории",),
    "required_tools": ("обязательные инструменты (через запятую)", "обязательные инструменты"),
    "sjm": (
        "сторителлинг",
        "сторителтнг",
        "sjm",
        "sjm (описание ситуации/кейса, с которым сталкивается участник, сторителлинг или моделирование среды)",
    ),
    "storytelling_type": (
        "тип сторителлинга",
        "storytelling type",
        "storytelling_type",
    ),
    "format": ("формат",),
    "additional_materials": ("дополнительные материалы", "дополнительные материалы для генерации"),
    "group_size": ("кол-во в группе",),
    "workload_hours": ("трудоемкость, астр.часы",),
    "workload_days": ("трудоемкость, дни",),
    "total_workload_days": ("общая трудоемкость, дни",),
    "xp": ("xp за проект",),
    "passing_threshold": ("% прохождения проекта",),
    "required_software": ("необходимое по/веб", "необходимое по"),
    "platform_name": ("название проекта на платформе и в gitlab",),
    "gitlab_link": ("ссылки на gitlab/google docs", "ссылки на gitlab"),
}


class BuildCurriculumContextRequest(BaseModel):
    """Тело запроса для построения curriculum context."""
    block_name: str
    project_order: int
    curriculum_data: dict[str, Any]


def _mirror_plan_summary(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Compact plan summary for the generator UI selector (relational mirror row)."""

    plan_id = int(plan["id"])
    return {
        "id": plan_id,
        "source_id": str(plan_id),
        "title": plan.get("title") or plan.get("direction") or f"УП #{plan_id}",
        "status": plan.get("status"),
        "direction": plan.get("direction") or None,
        "blocks": int(plan.get("total_blocks") or 0),
        "projects": int(plan.get("total_projects") or 0),
        "updated_at": plan.get("updated_at"),
        "source_updated_at": plan.get("updated_at"),
    }


def _assemble_plan_payload(plan: Mapping[str, Any], rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Rebuild the plan payload shape the generator-contract converter expects.

    ``convert_spravochnik_plan_to_generator_curriculum`` regroups a flat ``rows``
    list by ``block_index``, so the relational mirror rows feed it directly.
    """

    payload = dict(plan)
    payload["rows"] = [dict(row) for row in rows]
    return payload


def _normalize_column_name(value: str) -> str:
    """Приводит имя CSV-колонки к виду для устойчивого сопоставления."""
    return re.sub(r"\s+", " ", (value or "").replace("\ufeff", "").strip().lower())


def _detect_csv_delimiter(text: str) -> str:
    """Определяет CSV-разделитель с учетом quoted comma headers."""
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,")
        return dialect.delimiter
    except csv.Error:
        first_line = text.splitlines()[0] if text.splitlines() else ""
        return ";" if first_line.count(";") > first_line.count(",") else ","


def _read_csv_rows(text: str, delimiter: str) -> list[list[str]]:
    """Читает корректный CSV через стандартный парсер, сохраняя многострочные ячейки."""
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff")), delimiter=delimiter)
    rows: list[list[str]] = []
    for row in reader:
        cleaned = [cell.strip() for cell in row]
        if any(cleaned):
            rows.append(cleaned)
    return rows


def _resolve_column(headers: list[str], aliases: tuple[str, ...], *, allow_blank_first: bool = False) -> int | None:
    """Находит индекс колонки по списку допустимых названий."""
    normalized_headers = [_normalize_column_name(header) for header in headers]
    if allow_blank_first and normalized_headers and not normalized_headers[0]:
        return 0

    normalized_aliases = {_normalize_column_name(alias) for alias in aliases}
    for index, header in enumerate(normalized_headers):
        if header in normalized_aliases:
            return index
    return None


def _resolve_columns(headers: list[str], aliases: tuple[str, ...]) -> list[int]:
    """Находит все индексы колонок по списку допустимых названий.

    В паспортах программы встречаются повторяющиеся колонки с одним названием,
    например несколько колонок «Образовательные результаты». Для таких полей
    нужно сохранить все значения, а не только первое совпадение.
    """
    normalized_aliases = {_normalize_column_name(alias) for alias in aliases}
    return [
        index
        for index, header in enumerate(headers)
        if _normalize_column_name(header) in normalized_aliases
    ]


def _build_column_map(headers: list[str]) -> dict[str, int | None]:
    """Строит карту field -> column index для поддерживаемых схем УП."""
    return {
        field: _resolve_column(
            headers,
            aliases,
            allow_blank_first=field == "block_name",
        )
        for field, aliases in CURRICULUM_COLUMN_ALIASES.items()
    }


def _row_value(row: list[str], columns: dict[str, int | None], field: str) -> str:
    """Возвращает значение ячейки по логическому имени поля."""
    index = columns.get(field)
    if index is None or index >= len(row):
        return ""
    return row[index].strip()


def _row_values(row: list[str], indexes: list[int]) -> list[str]:
    """Возвращает непустые значения строки по набору индексов колонок."""
    values: list[str] = []
    for index in indexes:
        if index < len(row):
            value = row[index].strip()
            if value:
                values.append(value)
    return values


def _merge_unique(values: list[str], extra_values: list[str]) -> list[str]:
    """Добавляет значения без дублей, сохраняя исходный порядок."""
    merged = list(values)
    seen = set(values)
    for value in extra_values:
        if value and value not in seen:
            merged.append(value)
            seen.add(value)
    return merged


def _parse_project_format(value: str, group_size: str = "") -> str:
    """Нормализует формат проекта в контракт frontend/backend."""
    lowered = (value or "").lower()
    if "груп" in lowered or (not lowered and group_size.strip()):
        return "group"
    return "individual"


def _parse_group_size(value: str) -> int | None:
    """Извлекает верхнюю границу размера группы из значений вида '3-4'."""
    numbers = re.findall(r"\d+", value or "")
    return int(numbers[-1]) if numbers else None


def _direction_name(direction_code: str) -> str:
    """Возвращает человекочитаемое название направления."""
    direction_names = {
        "PjM": "Project Manager",
        "BSA": "Business Analytics",
        "Cb": "Cybersecurity",
        "DO": "DevOps",
        "QA": "Quality Assurance",
        "DS": "Data Science",
    }
    return direction_names.get(direction_code, "Unknown")


def _build_curriculum_plan(
    blocks_dict: dict[str, dict[str, Any]],
    direction_code: str,
) -> CurriculumPlan:
    """Собирает typed CurriculumPlan из промежуточного словаря блоков."""
    if direction_code == "UNK" and blocks_dict:
        first_block_name = next(iter(blocks_dict))
        direction_code = detect_direction_from_block_name(first_block_name)
        if direction_code == "UNK":
            first_projects = blocks_dict[first_block_name].get("projects", [])
            if first_projects:
                first_project = first_projects[0]
                direction_code = detect_direction_from_platform_name(
                    first_project.platform_name or first_project.title or "",
                )

    blocks = []
    for block_data in blocks_dict.values():
        code = direction_code
        if block_data["projects"]:
            first_project = block_data["projects"][0]
            platform_or_title = first_project.platform_name or first_project.title or ""
            detected = detect_direction_from_platform_name(platform_or_title)
            if detected != "UNK":
                code = detected

        blocks.append(ThematicBlock(
            name=block_data["name"],
            code=code,
            goals=block_data["goals"],
            projects=block_data["projects"],
        ))

    return CurriculumPlan(
        direction=_direction_name(direction_code),
        direction_code=direction_code,
        blocks=blocks,
    )


def _parse_header_curriculum(text: str) -> CurriculumPlan | None:
    """Парсит нормальный CSV по заголовкам, включая quoted multiline cells."""
    delimiter = _detect_csv_delimiter(text)
    rows = _read_csv_rows(text, delimiter)
    if len(rows) <= 1:
        return None

    headers = rows[0]
    columns = _build_column_map(headers)
    learning_outcome_indexes = _resolve_columns(
        headers,
        CURRICULUM_COLUMN_ALIASES["learning_outcomes"],
    )
    logger.info("CSV columns: %s", headers)

    if columns.get("title") is None:
        return None

    if any(len(row) != len(headers) for row in rows[1:]):
        logger.info("CSV rows do not match header width; falling back to legacy parser")
        return None

    blocks_dict: dict[str, dict[str, Any]] = {}
    current_block_name: str | None = None
    current_block_goals: list[str] = []
    direction_code = "UNK"
    inferred_order = 0

    for row in rows[1:]:
        order_str = _row_value(row, columns, "order")
        title = _row_value(row, columns, "title")
        normalized_title = _normalize_column_name(title)

        # В шаблонах УП часто есть строка-пояснение сразу после header.
        if not title or normalized_title in {"название проекта", "название контентной единицы"}:
            continue

        order = parse_int(order_str)
        if order is None:
            inferred_order += 1
            order = inferred_order
        else:
            inferred_order = max(inferred_order, order)

        block_name = _row_value(row, columns, "block_name")
        block_goals_text = _row_value(row, columns, "block_goals")
        parsed_goals = parse_block_goals(block_goals_text)

        if block_name:
            current_block_name = block_name
            current_block_goals = parsed_goals
        elif parsed_goals and current_block_name:
            current_block_goals = _merge_unique(current_block_goals, parsed_goals)

        if not current_block_name:
            continue

        format_raw = _row_value(row, columns, "format")
        group_size_raw = _row_value(row, columns, "group_size")
        project_format = _parse_project_format(format_raw, group_size_raw)
        group_size = _parse_group_size(group_size_raw) if project_format == "group" else None
        platform_name = _row_value(row, columns, "platform_name") or None

        project = CurriculumProject(
            block_name=current_block_name,
            block_goals=current_block_goals,
            order=order,
            title=title,
            description=_row_value(row, columns, "description"),
            learning_outcomes=parse_learning_outcomes(_join_multiline_parts(
                _row_values(row, learning_outcome_indexes),
            )),
            skills=parse_skills(_row_value(row, columns, "skills")),
            audience_level=_row_value(row, columns, "audience_level") or None,
            required_tools=parse_required_tools(_row_value(row, columns, "required_tools")),
            format=project_format,
            group_size=group_size,
            required_software=_row_value(row, columns, "required_software") or None,
            workload_hours=parse_float(_row_value(row, columns, "workload_hours")),
            workload_days=parse_float(_row_value(row, columns, "workload_days")),
            total_workload_days=parse_float(_row_value(row, columns, "total_workload_days")),
            xp=parse_int(_row_value(row, columns, "xp")),
            passing_threshold=_row_value(row, columns, "passing_threshold") or None,
            storytelling_type=_row_value(row, columns, "storytelling_type") or None,
            sjm=_row_value(row, columns, "sjm") or None,
            expert_notes=_row_value(row, columns, "expert_notes") or None,
            additional_materials=_row_value(row, columns, "additional_materials") or None,
            platform_name=platform_name,
            gitlab_link=_row_value(row, columns, "gitlab_link") or None,
        )

        if current_block_name not in blocks_dict:
            blocks_dict[current_block_name] = {
                "name": current_block_name,
                "goals": current_block_goals,
                "projects": [],
            }
        else:
            blocks_dict[current_block_name]["goals"] = current_block_goals

        if direction_code == "UNK":
            detected = detect_direction_from_platform_name(platform_name or title)
            if detected != "UNK":
                direction_code = detected

        blocks_dict[current_block_name]["projects"].append(project)

    if not blocks_dict:
        return None

    return _build_curriculum_plan(blocks_dict, direction_code)


def _join_multiline_parts(parts: list[str]) -> str:
    """Собирает многострочное значение ячейки, отбрасывая пустые фрагменты."""
    return "\n".join(part.strip() for part in parts if part and part.strip())


def _find_format_index(parts: list[str]) -> int | None:
    """Ищет позицию колонки формата в «хвосте» проектной строки."""
    for idx, part in enumerate(parts):
        lowered = part.strip().lower()
        if ("индивиду" in lowered or "груп" in lowered) and len(parts) - idx - 1 >= 10:
            return idx
    return None


def _is_project_chunk_complete(lines: list[str], delimiter: str) -> bool:
    """Определяет, закончился ли логический блок одного проекта."""
    if not lines:
        return False
    return _find_format_index(lines[-1].split(delimiter)) is not None


def _parse_project_chunk(
    chunk_lines: list[str],
    delimiter: str,
    current_block_name: str | None,
    current_block_goals: list[str],
) -> tuple[str | None, list[str], CurriculumProject | None]:
    """Разбирает один логический проект из нескольких физических строк CSV."""
    if not chunk_lines:
        return current_block_name, current_block_goals, None

    last_parts = [part.strip() for part in chunk_lines[-1].split(delimiter)]
    format_idx = _find_format_index(last_parts)
    if format_idx is None:
        return current_block_name, current_block_goals, None

    tail_prefix = last_parts[:format_idx]
    format_and_tail = last_parts[format_idx:]
    while len(format_and_tail) < 11:
        format_and_tail.append("")

    if len(chunk_lines) == 1:
        pre_format = tail_prefix
        if len(pre_format) < 7:
            return current_block_name, current_block_goals, None
        meta_parts = pre_format[:-5]
        order_str, title, description, expert_notes, learning_outcomes_head = pre_format[-5:]
        learning_outcomes_tail = ""
        sjm = None
    else:
        head_parts: list[str] = []
        for line in chunk_lines[:-1]:
            head_parts.extend(part.strip() for part in line.split(delimiter))

        if len(head_parts) < 5:
            return current_block_name, current_block_goals, None

        meta_parts = head_parts[:-5]
        order_str, title, description, expert_notes, learning_outcomes_head = head_parts[-5:]
        learning_outcomes_tail = tail_prefix[0].strip() if tail_prefix else ""
        sjm = _join_multiline_parts(tail_prefix[1:]) or None

    next_block_name = current_block_name
    next_block_goals = current_block_goals
    non_empty_meta = [part for part in meta_parts if part]

    if non_empty_meta and "блок" in non_empty_meta[0].lower():
        next_block_name = non_empty_meta[0]
        next_block_goals = parse_block_goals(_join_multiline_parts(non_empty_meta[1:]))
    elif non_empty_meta and current_block_name:
        goal_lines = [*current_block_goals, *non_empty_meta]
        next_block_goals = parse_block_goals(_join_multiline_parts(goal_lines))

    if not next_block_name or not order_str or not title:
        return next_block_name, next_block_goals, None

    try:
        order = int(order_str)
    except ValueError:
        return next_block_name, next_block_goals, None

    learning_outcomes_text = _join_multiline_parts([
        learning_outcomes_head,
        learning_outcomes_tail,
    ])

    project_format = "group" if "груп" in format_and_tail[0].lower() else "individual"
    group_size = None
    if project_format == "group" and format_and_tail[2]:
        parts = format_and_tail[2].split("-")
        last_part = parts[-1] if parts else format_and_tail[2]
        group_size = parse_int(last_part)

    project = CurriculumProject(
        block_name=next_block_name,
        block_goals=next_block_goals,
        order=order,
        title=title,
        description=description,
        learning_outcomes=parse_learning_outcomes(learning_outcomes_text),
        skills=parse_skills(""),
        audience_level=None,
        required_tools=[],
        format=project_format,
        group_size=group_size,
        required_software=format_and_tail[8] or None,
        workload_hours=parse_float(format_and_tail[3]),
        workload_days=parse_float(format_and_tail[4]),
        total_workload_days=parse_float(format_and_tail[5]),
        xp=parse_int(format_and_tail[6]),
        passing_threshold=format_and_tail[7] or None,
        sjm=sjm,
        expert_notes=expert_notes or None,
        additional_materials=format_and_tail[1] or None,
        platform_name=format_and_tail[9] or None,
        gitlab_link=format_and_tail[10] or None,
    )

    return next_block_name, next_block_goals, project


def parse_learning_outcomes(text: str) -> list[str]:
    """
    Парсит образовательные результаты из текста.

    Текст может содержать:
    - Переносы строк
    - Точки с заглавной буквы
    """
    if not text or not text.strip():
        return []

    # Разделяем по переносам строк и точкам
    outcomes = []
    lines = text.split('\n')
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Если строка заканчивается точкой, разделяем по точкам
        if '.' in line:
            parts = line.split('.')
            for part in parts:
                part = part.strip()
                if part:
                    outcomes.append(part)
        else:
            outcomes.append(line)

    return [o for o in outcomes if o]


def parse_skills(text: str) -> list[str]:
    """
    Парсит список навыков из текста.

    Текст может содержать:
    - Переносы строк
    - Запятые
    - Точки с запятой
    """
    if not text or not text.strip():
        return []

    skills = []

    # Сначала разделяем по переносам строк
    lines = text.split('\n')

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Убираем начальные маркеры списка
        line = re.sub(r'^[-•*]\s*', '', line)

        # Разделяем по запятым или точкам с запятой
        if ',' in line or ';' in line:
            # Разделяем по запятым и точкам с запятой
            parts = re.split(r'[,;]', line)
            for part in parts:
                part = part.strip()
                if part:
                    skills.append(part)
        else:
            if line:
                skills.append(line)

    return [s for s in skills if s]


def parse_required_tools(text: str) -> list[str]:
    """Парсит обязательные инструменты из отдельной колонки УП."""
    return parse_skills(text)


def parse_block_goals(text: str) -> list[str]:
    """Парсит цели блока."""
    if not text or not text.strip():
        return []

    lines = text.split('\n')
    goals = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Убираем начальные маркеры списка
        line = re.sub(r'^[-•*]\s*', '', line)
        if line:
            goals.append(line)

    return goals


def parse_float(value: str) -> float | None:
    """Парсит float значение из строки."""
    if not value or not value.strip():
        return None
    try:
        # Заменяем запятую на точку и убираем пробелы
        clean = value.strip().replace(',', '.').replace(' ', '')
        return float(clean) if clean else None
    except (ValueError, TypeError):
        return None


def parse_int(value: str) -> int | None:
    """Парсит int значение из строки."""
    if not value or not value.strip():
        return None
    try:
        # Убираем пробелы и нечисловые символы
        clean = re.sub(r'[^\d]', '', value.strip())
        return int(clean) if clean else None
    except (ValueError, TypeError):
        return None


def detect_direction_from_platform_name(platform_name: str) -> str:
    """
    Определяет код направления из названия проекта на платформе.

    Например: "PjM1_ProjPM" -> "PjM"
    """
    if not platform_name:
        return "UNK"

    match = re.match(r'^([A-Za-z]+)', platform_name)
    if match:
        return match.group(1)
    return "UNK"


def detect_direction_from_block_name(block_name: str) -> str:
    """
    Определяет код направления из названия блока.

    Известные маппинги для разных направлений.
    """
    block_lower = block_name.lower()

    # Project Manager
    if 'проект' in block_lower or 'менеджмент' in block_lower or 'управлен' in block_lower:
        return "PjM"
    # Business Analytics
    if 'бизнес' in block_lower or 'аналитик' in block_lower:
        return "BSA"
    # Cybersecurity
    if 'безопасност' in block_lower or 'кибер' in block_lower:
        return "Cb"
    # DevOps
    if 'devops' in block_lower or 'deploy' in block_lower:
        return "DO"
    # QA
    if 'тестирован' in block_lower or 'качеств' in block_lower or 'qa' in block_lower:
        return "QA"
    # Data Science
    if 'машинн' in block_lower or 'ml' in block_lower or 'data' in block_lower:
        return "DS"

    return "UNK"


@router.post("/upload", response_model=dict[str, Any])
async def upload_curriculum(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Загружает и парсит CSV файл учебного плана.

    Возвращает структуру для каскадных селекторов на фронтенде.
    """
    if not file.filename:
        raise HTTPException(400, "Файл не указан")

    if not file.filename.endswith('.csv'):
        raise HTTPException(400, "Поддерживается только CSV формат. Загрузите .csv файл.")

    try:
        validate_file(file)
        content = await read_upload_limited(file, max_size=MAX_FILE_SIZE)
        # Пробуем разные кодировки
        text = None
        for encoding in ['utf-8-sig', 'utf-8', 'cp1251', 'windows-1251']:
            try:
                text = content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue

        if text is None:
            raise HTTPException(400, "Не удалось определить кодировку файла")

        curriculum = _parse_header_curriculum(text)
        if curriculum and curriculum.blocks:
            logger.info(
                f"Загружен УП: {curriculum.direction} ({curriculum.direction_code}), "
                f"{len(curriculum.blocks)} блоков, "
                f"{sum(len(b.projects) for b in curriculum.blocks)} проектов"
            )
            return curriculum.to_dict_for_frontend()

        # Определяем разделитель (точка с запятой или запятая)
        first_line = text.split('\n')[0]
        delimiter = ';' if ';' in first_line else ','
        lines = [line.rstrip('\r') for line in text.splitlines() if line.strip()]
        if len(lines) <= 1:
            raise HTTPException(400, "Файл не содержит данных проектов")

        logger.info(f"CSV columns: {lines[0].split(delimiter)}")

        # Собираем блоки
        blocks_dict: dict[str, dict[str, Any]] = {}  # block_name -> block_data
        current_block_name = None
        current_block_goals: list[str] = []
        direction_code = "UNK"

        current_chunk: list[str] = []
        for line in lines[1:]:
            current_chunk.append(line)
            if not _is_project_chunk_complete(current_chunk, delimiter):
                continue

            current_block_name, current_block_goals, project = _parse_project_chunk(
                current_chunk,
                delimiter,
                current_block_name,
                current_block_goals,
            )
            current_chunk = []

            if not project or not current_block_name:
                continue

            if current_block_name not in blocks_dict:
                blocks_dict[current_block_name] = {
                    "name": current_block_name,
                    "goals": current_block_goals,
                    "projects": [],
                }
            else:
                blocks_dict[current_block_name]["goals"] = current_block_goals

            if project.platform_name and direction_code == "UNK":
                direction_code = detect_direction_from_platform_name(project.platform_name)

            blocks_dict[current_block_name]["projects"].append(project)

        if current_chunk:
            logger.warning("Необработанный хвост curriculum CSV: %s", current_chunk)

        curriculum = _build_curriculum_plan(blocks_dict, direction_code)
        if not curriculum.blocks:
            raise HTTPException(400, "Не удалось найти блоки и проекты в файле. Проверьте формат CSV.")

        logger.info(
            f"Загружен УП: {curriculum.direction} ({curriculum.direction_code}), "
            f"{len(curriculum.blocks)} блоков, "
            f"{sum(len(b.projects) for b in curriculum.blocks)} проектов"
        )

        return curriculum.to_dict_for_frontend()

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Ошибка парсинга CSV: {e}")
        raise HTTPException(500, "Ошибка парсинга файла") from e


@router.get("/plans", response_model=dict[str, Any])
async def list_persisted_curriculum_plans(
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """
    Возвращает УП, сохраненные в общей базе из контура справочника.

    Список сначала синхронизируется из SQLite справочника, чтобы созданные или
    отредактированные УП появлялись в генераторе без ручного импорта.
    """
    sync_result = sync_spravochnik_curriculum_plans(db)
    plans = (
        db.execute(
            text("SELECT * FROM catalog.curriculum_plan ORDER BY updated_at DESC, id DESC")
        )
        .mappings()
        .all()
    )
    return {
        "user_id": user.get("id"),
        "plans": [_mirror_plan_summary(dict(plan)) for plan in plans],
        "sync": sync_result,
    }


@router.post("/plans/sync", response_model=dict[str, Any])
async def sync_persisted_curriculum_plans(
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Принудительно синхронизирует УП справочника в общий каталог."""

    sync_result = sync_spravochnik_curriculum_plans(db)
    return {
        "user_id": user.get("id"),
        "sync": sync_result,
    }


@router.get("/plans/{source_id}", response_model=dict[str, Any])
async def get_persisted_curriculum_plan(
    source_id: str,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Возвращает один УП из общей базы в контракте генератора."""

    sync_spravochnik_curriculum_plans(db)
    try:
        plan_id = int(source_id)
    except (TypeError, ValueError):
        raise HTTPException(404, "Учебный план не найден в общей базе")

    plan = (
        db.execute(text("SELECT * FROM catalog.curriculum_plan WHERE id = :id"), {"id": plan_id})
        .mappings()
        .first()
    )
    if plan is None:
        raise HTTPException(404, "Учебный план не найден в общей базе")

    rows = (
        db.execute(
            text(
                "SELECT * FROM catalog.curriculum_plan_row WHERE plan_id = :id "
                "ORDER BY block_index, row_number, id"
            ),
            {"id": plan_id},
        )
        .mappings()
        .all()
    )
    plan_dict = dict(plan)
    curriculum = convert_spravochnik_plan_to_generator_curriculum(
        _assemble_plan_payload(plan_dict, [dict(row) for row in rows])
    )
    if not curriculum or not curriculum.get("blocks"):
        raise HTTPException(422, "Учебный план сохранен без структуры блоков и проектов")

    return {
        "user_id": user.get("id"),
        "plan": _mirror_plan_summary(plan_dict),
        "curriculum": curriculum,
    }


@router.post("/build-context")
async def build_curriculum_context(
    request: BuildCurriculumContextRequest,
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Строит контекст для генерации на основе выбранного проекта.

    Вызывается после выбора проекта в каскадном селекторе.
    """
    try:
        # Восстанавливаем CurriculumPlan из данных
        blocks = []
        for block_data in request.curriculum_data.get("blocks", []):
            projects = [
                CurriculumProject(
                    block_name=block_data["name"],
                    block_goals=block_data.get("goals", []),
                    **p,
                )
                for p in block_data.get("projects", [])
            ]
            blocks.append(ThematicBlock(
                name=block_data["name"],
                code=block_data.get("code", "UNK"),
                goals=block_data.get("goals", []),
                projects=projects
            ))

        curriculum = CurriculumPlan(
            direction=request.curriculum_data.get("direction", "Unknown"),
            direction_code=request.curriculum_data.get("direction_code", "UNK"),
            blocks=blocks
        )

        # Строим контекст
        context = curriculum.build_context(request.block_name, request.project_order)

        if not context:
            raise HTTPException(404, f"Проект #{request.project_order} в блоке '{request.block_name}' не найден")

        return context.model_dump()

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Ошибка построения контекста: {e}")
        raise HTTPException(500, "Ошибка построения контекста") from e
