"""Генерация прозаических разделов РПД (актуальность, цели, паспорт) через LLM.

Разделы-проза не переносятся из УП — их генерирует LLM по теме и содержанию УП, с учётом правил из
комментариев методиста (актуальность — развёрнуто, цели — поверхностно/общей фразой). Без клиента или
при ошибке возвращаем пусто → рендер подставит плейсхолдер.
"""

from __future__ import annotations

from typing import Any

from .up_models import UpPlan

_SYSTEM = "Ты методист ДПО. Пишешь официальные формулировки для рабочей программы дисциплины на русском. Без воды, по делу."

# (matcher-substring, инструкция) — правила из комментариев к шаблону.
_PROMPTS: list[tuple[str, str]] = [
    ("актуальн", "Напиши развёрнутый текст «Актуальность программы» (2–3 абзаца): почему тема востребована, кому и зачем нужна программа."),
    ("паспорт", "Напиши краткую аннотацию-паспорт программы (3–5 предложений): о чём программа, для кого, какой результат."),
    ("характеристика", "Кратко опиши область и виды профессиональной деятельности, к которым готовит программа (1 абзац)."),
    ("цель", "Сформулируй кратко: одну общую фразу цели программы и 3–4 задачи (списком). Поверхностно, без деталей."),
    ("цели", "Сформулируй кратко: одну общую фразу цели программы и 3–4 задачи (списком). Поверхностно, без деталей."),
    ("задач", "Сформулируй кратко: одну общую фразу цели программы и 3–4 задачи (списком)."),
]


def generate_prose(section_title: str, up: UpPlan, client: Any) -> list[str]:
    """Возвращает абзацы сгенерированного текста для раздела-прозы; пусто → рендер даст плейсхолдер."""
    instruction = _instruction_for(section_title)
    if instruction is None or client is None:
        return []
    user = f"{instruction}\n\n{_up_context(up)}"
    try:
        text = str(client.complete(_SYSTEM, user))
    except Exception:  # noqa: BLE001 - генерация best-effort; при сбое падаем в плейсхолдер
        return []
    return [_strip_markdown(line) for line in text.splitlines() if line.strip()]


def _strip_markdown(line: str) -> str:
    """Убирает markdown-разметку (**жирный**, ##, списочные маркеры) — в DOCX она показалась бы текстом."""
    cleaned = line.strip().lstrip("#").strip()
    cleaned = cleaned.replace("**", "").replace("__", "")
    if cleaned[:2] in {"- ", "* "}:
        cleaned = "• " + cleaned[2:]
    return cleaned.strip()


def _instruction_for(title: str) -> str | None:
    low = title.lower()
    for needle, instruction in _PROMPTS:
        if needle in low:
            return instruction
    return None


def _up_context(up: UpPlan) -> str:
    modules = "\n".join(f"- {module.title}" for module in up.modules[:15])
    outcomes = "; ".join(up.all_outcomes()[:10])
    return (
        f"Тема программы: {up.title or 'не указана'}\n"
        f"Объём: {up.total_hours or '—'} академических часов\n"
        f"Модули:\n{modules}\n"
        f"Ключевые результаты: {outcomes}"
    )
