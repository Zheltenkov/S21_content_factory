"""Перевод списка единиц: строгий контракт ``id → текст``, пакетами, с одним повтором.

Вынут из ``_translate_docx_units`` и очищен от привязки к DOCX — по нему теперь работают все
форматы. Поведение сохранено: тот же размер пакета, тот же порядок попыток, та же громкая ошибка
на недостающий фрагмент. Молчаливый пропуск здесь хуже отказа: пользователь получит файл, где
часть текста осталась непереведённой, и не узнает об этом.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Sequence

from content_factory.generation.agents.base.llm_client import LLMClientProtocol
from content_factory.generation.utils.translation_languages import get_translation_language_profile
from content_factory.translation.units import TextUnit

logger = logging.getLogger(__name__)

DEFAULT_BATCH_CHARS = int(os.getenv("MAX_DOCX_TRANSLATION_BATCH_CHARS", "9000"))
# Накладные расходы на один фрагмент в запросе: id, кавычки, запятые.
_UNIT_OVERHEAD_CHARS = 80


def extract_json_object(raw_text: str) -> dict:
    """Достаёт JSON-объект из ответа модели, даже если вокруг появились служебные фразы."""
    raw = (raw_text or "").strip()
    if not raw:
        raise ValueError("Пустой ответ модели")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(raw[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Модель вернула не объект")
    return parsed


def batch_units(units: Sequence[TextUnit], batch_chars: int = DEFAULT_BATCH_CHARS) -> list[list[TextUnit]]:
    """Группирует единицы так, чтобы один запрос к модели оставался управляемым."""
    batches: list[list[TextUnit]] = []
    current: list[TextUnit] = []
    current_chars = 0
    for unit in units:
        cost = len(unit.text) + _UNIT_OVERHEAD_CHARS
        if current and current_chars + cost > batch_chars:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(unit)
        current_chars += cost
    if current:
        batches.append(current)
    return batches


def translate_units(
    llm_client: LLMClientProtocol,
    units: Sequence[TextUnit],
    target_language: str,
    *,
    batch_chars: int = DEFAULT_BATCH_CHARS,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, str]:
    """Переводит единицы и возвращает ``unit_id → перевод``."""
    if not units:
        return {}

    profile = get_translation_language_profile(target_language)
    system_prompt = (
        "Ты переводишь текстовые фрагменты документа. "
        "Нужно вернуть только JSON без Markdown и пояснений. "
        "Сохраняй смысл, числа, имена файлов, пути, технические термины, формулы и сокращения. "
        f"Язык перевода: {profile.prompt_label}. "
        f"Письменность: {profile.script_instruction}."
    )

    translations: dict[str, str] = {}
    batches = batch_units(units, batch_chars)

    for batch_index, batch in enumerate(batches, 1):
        if progress_callback:
            progress_callback("translate")
        fragments = []
        for unit in batch:
            fragment: dict[str, str] = {"id": unit.unit_id, "text": unit.text}
            if unit.hint:
                fragment["hint"] = unit.hint
            fragments.append(fragment)
        payload = {
            "target_language": profile.prompt_label,
            "script_instruction": profile.script_instruction,
            "fragments": fragments,
        }
        user_prompt = (
            "Переведи каждый фрагмент отдельно и верни JSON строго такого вида:\n"
            '{"translations":{"0001":"перевод фрагмента"}}\n'
            "Не объединяй фрагменты, не меняй id, не добавляй новые ключи.\n"
            "Поле hint — подсказка о роли текста, переводить его не нужно.\n\n"
            f"Входные данные:\n{json.dumps(payload, ensure_ascii=False)}"
        )

        parsed = _complete_with_one_repair(llm_client, system_prompt, user_prompt)
        batch_translations = parsed.get("translations")
        if not isinstance(batch_translations, dict):
            raise RuntimeError("Модель вернула перевод без объекта translations")

        missing: list[str] = []
        for unit in batch:
            translated = batch_translations.get(unit.unit_id)
            if translated is None:
                missing.append(unit.unit_id)
                continue
            translations[unit.unit_id] = str(translated).strip()
        if missing:
            raise RuntimeError("Модель не вернула перевод для фрагментов: " + ", ".join(missing[:10]))

        logger.info(
            "translation batch done: batch=%s/%s units=%s", batch_index, len(batches), len(batch)
        )

    return translations


def _complete_with_one_repair(llm_client: LLMClientProtocol, system: str, user: str) -> dict:
    """Один запрос, при неудаче — один форматный повтор. Дальше ошибка идёт наверх."""
    try:
        raw = llm_client.complete(system=system, user=user, response_format="json_object", temperature=0.1)
    except Exception:  # noqa: BLE001 - не каждый шлюз принимает response_format
        raw = llm_client.complete(system=system, user=user, temperature=0.1)
    try:
        return extract_json_object(raw)
    except Exception:  # noqa: BLE001 - модель ответила не JSON: повторяем один раз
        raw = llm_client.complete(system=system, user=user, temperature=0.1)
        return extract_json_object(raw)
