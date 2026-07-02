"""Prompt construction helpers for the theory agent."""

from __future__ import annotations

from typing import Any

from ..domain_contracts import render_narrative_contract_section
from ..models.schemas import ProjectSeed


def build_theory_questions_prompt(part_data: dict[str, Any], seed: ProjectSeed) -> str:
    """Build a focused prompt for missing bridge questions."""
    return f"""Для части теории нужно сгенерировать вопросы к практике.

Название части: {part_data['title']}
Основной текст части:
{part_data['main_body'][:600]}...

Контекст проекта:
- Описание: {seed.project_description}
- Инструменты: {', '.join(seed.required_tools) or '—'}
- Навыки: {', '.join(seed.skills) or '—'}
- Результаты обучения: {', '.join(seed.learning_outcomes) or '—'}

Сгенерируй 1-2 вопроса, которые:
- привязаны к проекту (упоминают инструмент/артефакт/результат)
- средней сложности (без долгого ресерча)
- не являются пересказом теории
- формулируются простым языком

Формат вывода (строго):
**Вопросы к практике:**
- <вопрос 1>
- <вопрос 2 (опционально)>
"""


def build_theory_curriculum_context_section(
    seed: ProjectSeed,
    section_context: dict[str, Any] | None = None,
) -> str:
    """Build the curriculum context prompt section for Chapter 2."""
    ctx = (section_context or {}).get("curriculum_context")
    if not isinstance(ctx, dict):
        ctx = getattr(seed, "curriculum_context", None)
    if not ctx:
        return "Контекст из учебного плана не предоставлен."

    lines: list[str] = []
    narrative_payload = (section_context or {}).get("narrative_contract") or ctx.get("narrative_contract")
    narrative_contract_section = render_narrative_contract_section(narrative_payload)
    if narrative_contract_section:
        lines.append(narrative_contract_section)
        lines.append("")

    if ctx.get("block_name"):
        lines.append(f"Тематический блок УП: {ctx['block_name']}")

    if ctx.get("block_goals"):
        goals = ctx["block_goals"]
        if isinstance(goals, list):
            lines.append(f"Цели блока: {'; '.join(goals)}")

    if ctx.get("current_project_order"):
        lines.append(f"Номер проекта в блоке: {ctx['current_project_order']}")

    if ctx.get("current_project_description"):
        lines.append(f"Краткое описание проекта (из УП): {ctx['current_project_description']}")

    if ctx.get("current_project_skills"):
        skills = ctx["current_project_skills"]
        if isinstance(skills, list):
            lines.append(f"Список навыков проекта: {'; '.join(skills)}")

    if ctx.get("current_project_required_software"):
        lines.append(f"Необходимое ПО проекта: {ctx['current_project_required_software']}")

    prev_projects = ctx.get("previous_projects", [])
    if prev_projects:
        lines.append("")
        lines.append("ПРЕДЫДУЩИЕ ПРОЕКТЫ В БЛОКЕ (что студент уже изучил):")
        for project in prev_projects[-3:]:
            lo_str = "; ".join(project.get("learning_outcomes", [])[:3])
            lines.append(f"  - Проект {project.get('order')}: <<{project.get('title')}>>")
            if lo_str:
                lines.append(f"    LO: {lo_str}")
        lines.append("НЕ ПОВТОРЯЙ материал из предыдущих проектов! Ссылайся на него как на уже известное.")

    next_projects = ctx.get("next_projects", [])
    if next_projects:
        lines.append("")
        lines.append("СЛЕДУЮЩИЕ ПРОЕКТЫ В БЛОКЕ (ГРАНИЦЫ - НЕ ТРОГАТЬ ЭТИ ТЕМЫ!):")
        for project in next_projects[:2]:
            lines.append(f"  - Проект {project.get('order')}: <<{project.get('title')}>>")
        lines.extend(
            [
                "",
                "КРИТИЧЕСКИ ВАЖНО:",
                "НЕ вводи термины и концепции из следующих проектов!",
                "Теория ТОЛЬКО по темам ТЕКУЩЕГО проекта (см. LO и описание выше).",
                "Если концепция относится к следующему проекту — НЕ объясняй её!",
            ]
        )

    prev_block_projects = ctx.get("previous_block_projects", [])
    if prev_block_projects:
        lines.append("")
        lines.append("ПОСЛЕДНИЕ ПРОЕКТЫ ПРЕДЫДУЩЕГО БЛОКА (для связности):")
        for project in prev_block_projects[-2:]:
            lines.append(f"  - <<{project.get('title')}>> (блок: {project.get('block_name', 'предыдущий')})")

    return "\n".join(lines) if lines else "Контекст из учебного плана не предоставлен."


def build_theory_sjm_section(
    seed: ProjectSeed,
    section_context: dict[str, Any] | None = None,
) -> str:
    """Build the story/case prompt section for Chapter 2."""
    sjm = (section_context or {}).get("sjm_context") or getattr(seed, "sjm", None)
    storytelling_type = (
        (section_context or {}).get("storytelling_type")
        or getattr(seed, "storytelling_type", "sjm")
        or "sjm"
    )
    if str(storytelling_type).strip().lower() == "none":
        return (
            "Сторителлинг/кейс отключен типом сторителлинга none. "
            "Не добавляй отдельный сюжет; опирайся на описание проекта и образовательные результаты."
        )
    ctx = (section_context or {}).get("curriculum_context")
    if not isinstance(ctx, dict):
        ctx = getattr(seed, "curriculum_context", None)

    if not sjm and ctx:
        sjm = ctx.get("sjm_context")

    if not sjm:
        return (
            "Сторителлинг/кейс не предоставлен. "
            "Сгенерируй минимальный рабочий контекст для практической части по описанию проекта (роль, ситуация, 2–4 предложения). "
            "В теории используй его умеренно: как мост к заданиям, а не как отдельный сюжет каждого раздела. "
            "Избегай сухого перечисления «по полочкам» — тон живой, вовлекающий."
        )

    lines = [
        f"Тип сторителлинга: {storytelling_type}.",
        (
            "Режим SJM по умолчанию является практико-ориентированным: основной эффект сторителлинга должен быть "
            "в практических задачах, артефактах и критериях; теория только поддерживает контекст и делает мосты к практике."
            if str(storytelling_type).strip().lower() == "sjm"
            else "Выбранный тип сторителлинга задаёт рамку, но теория не должна подменять практические задания сюжетом."
        ),
        "Если в контексте передан SJM (кейс/история) — используй его в теории как короткие привязки и вопросы к практике.",
        "Блок **Пример:** оставляй как внешний реальный кейс компаний/продуктов, не подменяй его SJM-сюжетом проекта.",
        "В каждом внешнем примере покажи не только проблему, но и решение/управленческое действие, которое связано с темой текущей части.",
        "После внешнего примера верни фокус к SJM: роли, заказчику, ограничениям, продукту или артефакту текущего проекта.",
        "",
        str(sjm),
        "",
        "Привязывай теорию и вопросы к практике к этому кейсу. Тон: живой, вовлекающий; избегай сухого перечисления без контекста.",
    ]
    return "\n".join(lines)


def determine_theory_content_type(seed: ProjectSeed) -> str:
    """Classify content profile for formula/code restrictions."""
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


def build_theory_content_type_section(content_type: str) -> str:
    """Build prompt constraints for the detected theory content type."""
    if content_type == "hard_code":
        return """ТИП: ТЕХНИЧЕСКИЙ (hard code)
Это проект для РАЗРАБОТЧИКОВ. Разрешено:
- Примеры кода (с комментариями)
- Формулы (с пояснениями)
- Технические диаграммы
- Алгоритмы и структуры данных
Тон: профессиональный, технический, но понятный."""

    if content_type == "low_code":
        return """ТИП: ТЕХНИЧЕСКИЙ С ОГРАНИЧЕНИЯМИ (low code)
Это проект для технических специалистов (DS, DevOps, QA). Разрешено:
- Минимум кода (1-2 коротких примера на весь раздел)
- Максимум 1-2 ПРОСТЫЕ формулы (только если критически нужны)
- Диаграммы и схемы
- Таблицы сравнения
ЗАПРЕЩЕНО: сложные формулы, много кода, глубокие технические детали.
Тон: объясняющий, с практическими примерами из жизни."""

    return """ТИП: ГУМАНИТАРНЫЙ (no code)
Это проект для МЕНЕДЖЕРОВ, АНАЛИТИКОВ, ДИЗАЙНЕРОВ (PjM, BSA, UX, КБ).

СТРОГО ЗАПРЕЩЕНО:
- Любой код (даже псевдокод!)
- Любые формулы (даже простые типа P = Q/T!)
- Технические диаграммы с кодом

РАЗРЕШЕНО:
- Таблицы (сравнения, чек-листы, матрицы решений)
- Блок-схемы процессов (flowchart в mermaid, БЕЗ кода)
- Mermaid без ручных тем и цветов: не используй %%{init...}%%, classDef, class, style, linkStyle, fill/stroke/color/background
- Примеры из бизнеса и реальной жизни

ТОН: разговорный, с историями и кейсами.
Объясняй всё так, будто рассказываешь коллеге за чашкой кофе.
Используй аналогии из повседневной жизни."""


def build_theory_formulas_requirements(seed: ProjectSeed, content_type: str) -> str:
    """Build formula/code constraints for the theory prompt."""
    if content_type == "no_code":
        return """ФОРМУЛЫ И КОД: ПОЛНОСТЬЮ ЗАПРЕЩЕНЫ!
Даже если методолог указал include_formulas=True — НЕ ДОБАВЛЯЙ ФОРМУЛЫ.
Для этого направления (менеджмент/аналитика) формулы неуместны.
Если нужно показать расчёт — опиши словами или покажи таблицу с числами.

ВМЕСТО ФОРМУЛЫ:
- Плохо: $$P_{db} = \\frac{Q}{T}$$
- Хорошо: <<Производительность = количество задач / время. Например, если команда закрыла 20 задач за 5 дней, производительность = 4 задачи в день.>>"""

    if content_type == "low_code":
        if not seed.include_formulas:
            return """ФОРМУЛЫ: ЗАПРЕЩЕНЫ методологом.
КОД: Максимум 1-2 коротких примера (до 10 строк) за весь раздел.
Код должен быть КРИТИЧЕСКИ необходим для понимания."""
        return """ФОРМУЛЫ: Разрешены, но МАКСИМУМ 1-2 на весь раздел.
Каждая формула ОБЯЗАТЕЛЬНО должна иметь:
1. Пояснение ПЕРЕД формулой (зачем она нужна)
2. Расшифровку ВСЕХ переменных
3. Пример с КОНКРЕТНЫМИ числами

КОД: Максимум 1-2 коротких примера (до 10 строк)."""

    if not seed.include_formulas:
        return """ФОРМУЛЫ: ЗАПРЕЩЕНЫ методологом.
КОД: Разрешён. Используй примеры кода там, где они помогают понять концепцию.
Код должен быть с комментариями."""
    return """ФОРМУЛЫ: Разрешены там, где они РЕАЛЬНО помогают.
Каждая формула должна иметь расшифровку параметров и пример.
КОД: Разрешён и приветствуется. Код должен быть с комментариями."""
