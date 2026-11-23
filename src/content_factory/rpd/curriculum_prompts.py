"""Prompt construction for public, concise module names in section 3.1."""

from __future__ import annotations

import json

from .up_models import UpPlan


def curriculum_titles_prompt(up: UpPlan) -> tuple[str, str]:
    """Build a closed transformation prompt: wording may change, curriculum scope may not."""
    system = (
        "Ты редактор публичного учебного плана. Переформулируй технические названия тематических "
        "блоков в краткие названия модулей для официального документа. Сохраняй предметный смысл и "
        "порядок. Пиши по-русски; допустимы только устоявшиеся сокращения и термины вроде MVP, AI, "
        "CI/CD и unit economics. Не добавляй темы, которых нет во входе. Каждый title: одна строка, 2-12 слов, "
        "начинается с заглавной буквы, без стрелок, служебных слов «Модуль»/«Тематический блок», "
        "номеров и финальной точки. Все названия должны быть уникальны; соседние части одной темы "
        "различай по их проектам и смысловому фокусу. Верни ровно один элемент на каждый module_index."
    )
    modules = [
        {
            "module_index": index,
            "source_title": module.title,
            "goal": module.goals,
            "projects": [row.name for row in module.rows if row.name][:8],
        }
        for index, module in enumerate(up.modules, start=1)
    ]
    user = (
        f"Программа: {up.title or 'не указана'}\n"
        "Тематические блоки:\n"
        f"{json.dumps(modules, ensure_ascii=False, indent=2)}"
    )
    return system, user


__all__ = ["curriculum_titles_prompt"]
