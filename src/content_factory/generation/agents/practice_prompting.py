"""Prompt construction helpers for the practice agent."""

from __future__ import annotations

from typing import Any

from ..artifact_chain import is_generic_repo_path_template
from ..domain_contracts import render_narrative_contract_section
from ..models.schemas import ProjectSeed


def build_practice_repo_info(seed: ProjectSeed) -> str:
    """Build repository hints for generated deliverable locations."""
    repo_info = ""
    repo_path_template = seed.repo_path_template if not is_generic_repo_path_template(seed.repo_path_template) else ""
    if seed.repo_base_url or repo_path_template:
        repo_info = "\nИнформация о репозитории для результатов:"
        if seed.repo_base_url:
            repo_info += f"\n- Базовый URL: {seed.repo_base_url}"
        if repo_path_template:
            repo_info += f"\n- Шаблон пути: {repo_path_template}"
        repo_info += "\nИспользуй эту информацию при указании локации ожидаемого результата."
    return repo_info


def build_reference_practice_section(seed: ProjectSeed) -> str:
    """Build optional reference-practice section for the prompt."""
    reference_hint = getattr(seed, "reference_practice_hint", None)
    if not (reference_hint and reference_hint.strip()):
        return ""
    return (
        "\n=== ЭТАЛОН (ОРИЕНТИРУЙСЯ НА СТРУКТУРУ И СТИЛЬ ЗАДАНИЙ) ===\n"
        "Ниже — фрагмент эталонного README с заданиями. Сохраняй похожую структуру формулировок "
        "и типы заданий, адаптируя под описание и LO текущего проекта.\n\n"
        + reference_hint.strip()
    )


def build_practice_curriculum_context_section(
    seed: ProjectSeed,
    section_context: dict[str, Any] | None = None,
) -> str:
    """Build the curriculum context prompt section for Chapter 3."""
    ctx = (section_context or {}).get("curriculum_context")
    if not isinstance(ctx, dict):
        ctx = getattr(seed, "curriculum_context", None)
    if not ctx:
        return (
            "Контекст из учебного плана не предоставлен. "
            "Опирайся строго на описание проекта и LO выше; избегай общих формулировок — практика должна отражать специфику ЭТОГО проекта."
        )

    lines = [
        "Приоритет: описание проекта и LO из УП задают тематику и границы заданий; не подменяй их общими формулировками. Задания должны максимально соответствовать этому проекту."
    ]
    narrative_payload = (section_context or {}).get("narrative_contract") or ctx.get("narrative_contract")
    narrative_contract_section = render_narrative_contract_section(narrative_payload)
    if narrative_contract_section:
        lines.extend(["", narrative_contract_section])

    if ctx.get("block_name"):
        lines.append(f"Тематический блок УП: {ctx['block_name']}")

    if ctx.get("block_goals"):
        goals = ctx["block_goals"]
        if isinstance(goals, list):
            lines.append(f"Цели блока: {'; '.join(goals[:3])}")

    if ctx.get("current_project_required_software"):
        lines.append(f"Необходимое ПО проекта: {ctx['current_project_required_software']}")

    prev_projects = ctx.get("previous_projects", [])
    if prev_projects:
        lines.append("")
        lines.append("ПРАКТИЧЕСКИЕ ЗАДАЧИ ИЗ ПРЕДЫДУЩИХ ПРОЕКТОВ БЛОКА:")
        for project in prev_projects[-2:]:
            lines.append(f"  - Проект <<{project.get('title')}>>: {project.get('description', '')[:100]}...")
        lines.append("НЕ ПОВТОРЯЙ типы заданий из предыдущих проектов! Создавай новые, более сложные.")

    next_projects = ctx.get("next_projects", [])
    if next_projects:
        lines.append("")
        lines.append("СЛЕДУЮЩИЕ ПРОЕКТЫ В БЛОКЕ (ГРАНИЦЫ - НЕ ТРОГАТЬ ЭТИ ТЕМЫ!):")
        for project in next_projects[:2]:
            lines.append(f"  - <<{project.get('title')}>>")
        lines.extend(
            [
                "",
                "КРИТИЧЕСКИ ВАЖНО:",
                "НЕ вводи новые доменные термины и концепции из следующих проектов!",
                "Задания ТОЛЬКО по темам ТЕКУЩЕГО проекта (см. описание и LO выше).",
                "Можно развивать общие навыки (структурирование, ясность, проверяемость), но НЕ новые специальные темы.",
            ]
        )

    return "\n".join(lines) if lines else "Контекст из учебного плана не предоставлен."


def build_practice_sjm_section(
    seed: ProjectSeed,
    section_context: dict[str, Any] | None = None,
) -> str:
    """Build the story/case prompt section for Chapter 3."""
    sjm = (section_context or {}).get("sjm_context") or getattr(seed, "sjm", None)
    storytelling_type = (
        (section_context or {}).get("storytelling_type")
        or getattr(seed, "storytelling_type", "sjm")
        or "sjm"
    )
    if str(storytelling_type).strip().lower() == "none":
        return (
            "Сторителлинг/кейс отключен типом сторителлинга none. "
            "Не добавляй отдельный сюжет; задачи должны опираться на описание проекта и образовательные результаты."
        )
    ctx = (section_context or {}).get("curriculum_context")
    if not isinstance(ctx, dict):
        ctx = getattr(seed, "curriculum_context", None)

    if not sjm and ctx:
        sjm = ctx.get("sjm_context")

    if not sjm:
        lines = [
            "Сторителлинг/кейс не предоставлен (колонка SJM в УП пуста).",
            "",
            "Сгенерируй минимальный рабочий контекст по описанию проекта (роль, компания/проект, ситуация; 4–6 строк) и привяжи к нему ВСЕ задания.",
            "Входные данные задач должны ссылаться на этот контекст. Опирайся на описание проекта и LO из УП, избегай общих мест.",
        ]
        return "\n".join(lines)

    lines = [
        f"Тип сторителлинга: {storytelling_type}.",
        (
            "Режим SJM по умолчанию применяется прежде всего к практической части: каждая задача должна опираться "
            "на ситуацию, роль, ограничения, артефакт и критерии из этого контекста."
            if str(storytelling_type).strip().lower() == "sjm"
            else "Выбранный тип сторителлинга должен быть явно виден в формулировках практических задач."
        ),
        "КРИТИЧЕСКИ ВАЖНО: Задания должны быть ПРИВЯЗАНЫ к этому кейсу/истории!",
        "",
        str(sjm),
        "",
        "ОБЯЗАТЕЛЬНО:",
        "- Студент должен решать задачи В КОНТЕКСТЕ этого кейса",
        "- Входные данные каждой задачи должны ссылаться на персонажей/компании/ситуации из кейса",
        "- Все задания - как будто студент действительно работает над ЭТОЙ конкретной проблемой",
        "- Все задания должны быть этапами одной цепочки работы над одним результатом, а не разными учебными направлениями",
        "- Если в SJM есть заказчик, слово «заказчик» должно появиться в задаче 1 и минимум ещё в одной задаче",
        "- Не заменяй роль из SJM на продакта, руководителя или абстрактную команду, если такой роли нет в кейсе",
    ]
    return "\n".join(lines)


def determine_practice_content_type(seed: ProjectSeed) -> str:
    """Classify practice prompt profile from project direction."""
    explicit_type = getattr(seed, "project_content_type", None)
    if explicit_type in {"hard_code", "low_code", "no_code"}:
        return explicit_type

    direction = (getattr(seed, "direction", "") or seed.thematic_block or "").upper()
    hard_code_directions = {
        "C",
        "CPP",
        "C++",
        "JAVA",
        "GO",
        "RUST",
        "BACKEND",
        "MOBILE",
        "WEB",
        "FRONTEND",
        "FULLSTACK",
        "DEV",
        "SWE",
    }
    low_code_directions = {
        "DS",
        "DO",
        "QA",
        "BIO",
        "BIOINF",
        "DEVOPS",
        "DATA",
        "ML",
        "AI",
        "TESTING",
        "AUTOMATION",
    }
    no_code_directions = {
        "PJM",
        "UX",
        "CB",
        "KB",
        "BSA",
        "BA",
        "PM",
        "CYBER",
        "SECURITY",
        "PRODUCT",
        "DESIGN",
        "MANAGEMENT",
        "ANALYST",
    }

    if direction in hard_code_directions:
        return "hard_code"
    if direction in low_code_directions:
        return "low_code"
    if direction in no_code_directions:
        return "no_code"
    return "low_code"


def build_practice_content_type_section(content_type: str) -> str:
    """Build prompt instructions for the detected practice content profile."""
    if content_type == "hard_code":
        return """ТИП: ТЕХНИЧЕСКИЙ (hard code)
Это проект для РАЗРАБОТЧИКОВ. Типы и порядок заданий — по теории и LO: как в реальной разработке по этой теме (например: требования → проектирование → реализация → проверка → документация). Разнообразие по типу деятельности, не один шаблон для всех проектов.
Разрешено: код, алгоритмы, структуры данных, конфиги, скрипты. Тон: профессиональный, технический."""

    if content_type == "low_code":
        return """ТИП: ТЕХНИЧЕСКИЙ С ОГРАНИЧЕНИЯМИ (low code)
Это проект для DS, DevOps, QA. Типы заданий — по теории и LO (минимум кода: конфиги, простые скрипты; данные, пайплайны, тесты; таблицы, схемы). ОГРАНИЧЕНО: сложный код. Тон: практический, с примерами."""

    return """ТИП: ГУМАНИТАРНЫЙ (no code)
Это проект для МЕНЕДЖЕРОВ, АНАЛИТИКОВ (PjM, BSA, UX, КБ).

ЗАДАНИЯ БЕЗ КОДА. Типы заданий выбирай по теории и LO проекта (примеры: анализ и решение, документы, коммуникация, оценка/приоритизация, схемы процессов). Последовательность — как в реальной работе по этой теме, не шаблонная.

АРТЕФАКТЫ: документы, таблицы, схемы, презентации — НЕ код!
ТОН: бизнесовый, реальные кейсы."""


def build_practice_formulas_requirements(seed: ProjectSeed, content_type: str) -> str:
    """Build formula/code constraints for the practice prompt."""
    if content_type == "no_code":
        return """ФОРМУЛЫ И КОД В ЗАДАНИЯХ: ПОЛНОСТЬЮ ЗАПРЕЩЕНЫ!
Задания должны быть выполнимы БЕЗ программирования.
Артефакты: документы, таблицы, схемы, презентации.
НЕ проси писать код, скрипты или формулы."""

    if content_type == "low_code":
        return """КОД В ЗАДАНИЯХ: Минимально, только если критически нужен.
Максимум 1-2 задания с простым кодом (конфиги, скрипты до 20 строк).
Остальные задания — без кода (анализ, документация, схемы)."""

    return """КОД В ЗАДАНИЯХ: Разрешён.
Типы и порядок заданий — по теории и LO: как в реальной разработке по этой теме. Разнообразие по типу деятельности (кодирование, рефакторинг, тестирование, документирование и т.д.), не один шаблон для всех проектов."""
