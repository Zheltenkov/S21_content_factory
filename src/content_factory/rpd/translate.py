"""Русификация данных УП: англоязычные названия тем/модулей/результатов → грамотный русский.

Часть УП может прийти на английском (напр. англо-курс: «Тематический блок» на английском). Для
РПД нужен русский. Переводим ОДНИМ батч-вызовом LLM только значения с латиницей; термины, названия
инструментов/технологий, аббревиатуры, имена собственные и файлы просим оставить как есть.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .up_models import UpModule, UpPlan

_LATIN = re.compile(r"[A-Za-z]{3,}")
_SYSTEM = (
    "Ты переводчик учебной документации. Переведи значения на грамотный русский язык. "
    "Названия инструментов и технологий, аббревиатуры, имена собственные, названия файлов и "
    "общепринятые термины оставляй как есть. Верни СТРОГО JSON-объект вида {\"оригинал\": \"перевод\"} "
    "без пояснений и без markdown."
)
PROMPT_VERSION = "rpd-translate-v1"


def russify_up(up: UpPlan, client: Any) -> UpPlan:
    """Возвращает УП с переведёнными на русский англоязычными названиями; без клиента — как есть."""
    if client is None:
        return up
    values = sorted(_translatable_values(up))
    if not values:
        return up
    mapping = _translate(values, client)
    if not mapping:
        return up
    return _apply(up, mapping)


def _translatable_values(up: UpPlan) -> set[str]:
    values: set[str] = set()
    for module in up.modules:
        for text in (module.title, module.goals):
            if text and _LATIN.search(text):
                values.add(text)
        for row in module.rows:
            for text in (row.name, row.description, row.outcomes_know, row.outcomes_can, row.outcomes_skills):
                if text and _LATIN.search(text):
                    values.add(text)
    return values


def _translate(values: list[str], client: Any) -> dict[str, str]:
    try:
        raw = str(
            client.complete(
                _SYSTEM,
                json.dumps(values, ensure_ascii=False),
                llm_role="rpd_translate",
                prompt_version=PROMPT_VERSION,
            )
        )
        data = json.loads(_extract_json(raw))
    except Exception:  # noqa: BLE001 - перевод best-effort; при сбое оставляем оригинал
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str) and v.strip()}


def _extract_json(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    return text[start : end + 1] if start >= 0 and end > start else text


def _apply(up: UpPlan, mapping: dict[str, str]) -> UpPlan:
    def tr(value: str) -> str:
        return mapping.get(value, value)

    modules = [
        UpModule(
            title=tr(module.title),
            goals=tr(module.goals),
            rows=[
                row.model_copy(
                    update={
                        "block_title": tr(row.block_title),
                        "name": tr(row.name),
                        "description": tr(row.description),
                        "outcomes_know": tr(row.outcomes_know),
                        "outcomes_can": tr(row.outcomes_can),
                        "outcomes_skills": tr(row.outcomes_skills),
                    }
                )
                for row in module.rows
            ],
        )
        for module in up.modules
    ]
    return up.model_copy(update={"modules": modules})


__all__ = ["russify_up"]
