"""
content_gen/utils/patch_format.py

Модуль для работы с patch-форматом перегенерации README.

Patch-формат позволяет LLM возвращать только изменения (патчи) вместо полного README,
что экономит токены и повышает стабильность метрик.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Patch:
    """Одно изменение (патч) в README."""

    location_hint: str  # Краткое описание места изменения
    old_text: str  # Точный фрагмент из original_md для замены
    new_text: str  # Новый текст вместо old_text


@dataclass
class PatchResult:
    """Результат применения патчей."""

    success: bool
    applied_patches: list[Patch]  # Успешно применённые патчи
    failed_patches: list[Patch]  # Патчи, которые не удалось применить
    result_md: str  # Результирующий Markdown
    errors: list[str]  # Список ошибок


LineRange = tuple[int, int, str]


def parse_patches_from_response(response: str) -> list[Patch] | None:
    """
    Парсит патчи из ответа LLM в формате JSON.

    Ожидаемый формат:
    {
        "changes": [
            {
                "location_hint": "краткое описание места",
                "old_text": "точный фрагмент из original_md",
                "new_text": "новый текст"
            }
        ]
    }

    Args:
        response: Ответ LLM (может содержать JSON или текст)

    Returns:
        Список Patch или None, если не удалось распарсить
    """
    # Пытаемся найти JSON в ответе
    json_match = re.search(r'\{[\s\S]*"changes"[\s\S]*\}', response)
    if not json_match:
        return None

    try:
        data = json.loads(json_match.group(0))
        if "changes" not in data or not isinstance(data["changes"], list):
            return None

        patches = []
        for change in data["changes"]:
            if not isinstance(change, dict):
                continue
            if "old_text" not in change or "new_text" not in change:
                continue

            patches.append(
                Patch(
                    location_hint=change.get("location_hint", ""),
                    old_text=change["old_text"],
                    new_text=change["new_text"],
                )
            )

        return patches
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"Ошибка парсинга патчей из ответа LLM: {e}")
        return None


def _find_text_with_fuzzy_match(text: str, pattern: str, max_distance: int = 10) -> tuple[int, int] | None:
    """
    Находит текст в документе с учётом вариаций пробелов и переносов строк.
    
    Args:
        text: Текст для поиска
        pattern: Паттерн для поиска
        max_distance: Максимальное расстояние для fuzzy match (в символах)
        
    Returns:
        Tuple (start, end) позиций найденного текста или None
    """
    # Точное совпадение
    if pattern in text:
        start = text.find(pattern)
        return (start, start + len(pattern))

    # Нормализуем пробелы для поиска - заменяем множественные пробелы на \s+
    pattern_escaped = re.escape(pattern.strip())
    # Заменяем пробелы в экранированном паттерне на \s+
    pattern_escaped = re.sub(r'\\ ', r'\\s+', pattern_escaped)
    # Добавляем опциональные пробелы в начале и конце
    normalized_pattern = f"\\s*{pattern_escaped}\\s*"

    match = re.search(normalized_pattern, text, re.MULTILINE | re.DOTALL)
    if match:
        return (match.start(), match.end())

    # Пытаемся найти частичное совпадение (первые N слов)
    pattern_words = pattern.strip().split()
    if len(pattern_words) >= 2:
        # Ищем первые 2 слова с учётом вариаций пробелов
        partial_pattern_words = pattern_words[:2]
        # Создаём паттерн для поиска этих слов с любым количеством пробелов между ними
        partial_pattern = r'\s+'.join(re.escape(word) for word in partial_pattern_words)
        partial_pattern = f"\\s*{partial_pattern}\\s*"

        match = re.search(partial_pattern, text, re.MULTILINE | re.DOTALL)
        if match:
            # Расширяем найденный фрагмент, чтобы попытаться найти полный паттерн
            start = match.start()
            end = match.end()

            # Расширяем в обе стороны
            for expand in range(max_distance):
                if start - expand >= 0 and end + expand <= len(text):
                    candidate = text[start - expand:end + expand]
                    # Нормализуем пробелы для сравнения
                    candidate_normalized = re.sub(r'\s+', ' ', candidate.strip())
                    pattern_normalized = re.sub(r'\s+', ' ', pattern.strip())
                    if pattern_normalized in candidate_normalized or candidate_normalized in pattern_normalized:
                        return (start - expand, end + expand)

    return None


def _validate_patch(patch: Patch, original_md: str) -> tuple[bool, str | None]:
    """
    Валидирует патч перед применением.
    
    Args:
        patch: Патч для валидации
        original_md: Исходный README
        
    Returns:
        Tuple (is_valid, error_message)
    """
    if not patch.old_text or not patch.old_text.strip():
        return (False, "old_text пуст")

    if patch.new_text is None:
        return (False, "new_text отсутствует")

    # Проверяем, что old_text не содержит защищённые блоки
    if "[[[BLOCK_" in patch.old_text:
        return (False, "old_text содержит защищённые блоки (маркеры)")

    if "```mermaid" in patch.old_text or "```" in patch.old_text:
        return (False, "old_text содержит блоки кода/диаграмм (должен содержать только обычный текст)")

    if "$$" in patch.old_text:
        return (False, "old_text содержит формулы (должен содержать только обычный текст)")

    # Проверяем минимальную длину old_text (должен быть достаточно уникальным)
    if len(patch.old_text.strip()) < 10 and not patch.old_text.strip().startswith("#"):
        return (False, "old_text слишком короткий (минимум 10 символов)")

    return (True, None)


def _line_start_offsets(text: str) -> list[int]:
    starts = [0]
    for match in re.finditer(r"\n", text or ""):
        starts.append(match.end())
    return starts


def _normalize_line_ranges(text: str, ranges: Sequence[LineRange] | None) -> list[LineRange]:
    if not ranges:
        return []
    line_count = max(1, len(_line_start_offsets(text)))
    normalized: list[LineRange] = []
    for start_line, end_line, label in ranges:
        start = max(1, min(int(start_line), line_count))
        end = max(start, min(int(end_line), line_count))
        normalized.append((start, end, label or f"строки {start}-{end}"))
    return normalized


def _line_range_slice(text: str, line_range: LineRange) -> tuple[str, int]:
    starts = _line_start_offsets(text)
    start_line, end_line, _label = line_range
    start_offset = starts[start_line - 1] if start_line - 1 < len(starts) else len(text)
    end_offset = starts[end_line] if end_line < len(starts) else len(text)
    return text[start_offset:end_offset], start_offset


def _find_text_within_line_ranges(
    text: str,
    pattern: str,
    allowed_line_ranges: Sequence[LineRange],
) -> tuple[int, int, str] | None:
    for line_range in allowed_line_ranges:
        scope_text, offset = _line_range_slice(text, line_range)
        if pattern in scope_text:
            start = scope_text.find(pattern)
            return (offset + start, offset + start + len(pattern), line_range[2])

        fuzzy_match = _find_text_with_fuzzy_match(scope_text, pattern)
        if fuzzy_match:
            start, end = fuzzy_match
            return (offset + start, offset + end, line_range[2])
    return None


def _apply_scoped_patches(
    original_md: str,
    patches: list[Patch],
    allowed_line_ranges: Sequence[LineRange],
) -> PatchResult:
    result_md = original_md
    applied_patches: list[Patch] = []
    failed_patches: list[Patch] = []
    errors: list[str] = []
    normalized_ranges = _normalize_line_ranges(original_md, allowed_line_ranges)
    resolved: list[tuple[int, Patch]] = []

    for patch in patches:
        is_valid, validation_error = _validate_patch(patch, original_md)
        if not is_valid:
            failed_patches.append(patch)
            errors.append(f"Патч '{patch.location_hint}': {validation_error}")
            continue

        match_pos = _find_text_within_line_ranges(original_md, patch.old_text, normalized_ranges)
        if not match_pos:
            failed_patches.append(patch)
            errors.append(
                f"Патч '{patch.location_hint}': old_text не найден в разрешённых диапазонах "
                "или находится вне выбранных частей README"
            )
            continue

        start, _end, _label = match_pos
        resolved.append((start, patch))

    for _source_start, patch in sorted(resolved, key=lambda item: item[0], reverse=True):
        current_ranges = _normalize_line_ranges(result_md, normalized_ranges)
        match_pos = _find_text_within_line_ranges(result_md, patch.old_text, current_ranges)
        if not match_pos:
            failed_patches.append(patch)
            errors.append(
                f"Патч '{patch.location_hint}': old_text был найден в исходном scope, "
                "но не найден при применении"
            )
            continue

        start, end, label = match_pos
        result_md = result_md[:start] + patch.new_text + result_md[end:]
        applied_patches.append(patch)
        logger.info("Патч '%s' применён в разрешённой области '%s'", patch.location_hint, label)

    return PatchResult(
        success=len(failed_patches) == 0,
        applied_patches=applied_patches,
        failed_patches=failed_patches,
        result_md=result_md,
        errors=errors,
    )


def apply_patches(
    original_md: str,
    patches: list[Patch],
    *,
    allowed_line_ranges: Sequence[LineRange] | None = None,
) -> PatchResult:
    """
    Применяет патчи к оригинальному README с улучшенным поиском и валидацией.

    Args:
        original_md: Исходный README
        patches: Список патчей для применения

    Returns:
        PatchResult с результатами применения
    """
    if allowed_line_ranges:
        return _apply_scoped_patches(original_md, patches, allowed_line_ranges)

    result_md = original_md
    applied_patches: list[Patch] = []
    failed_patches: list[Patch] = []
    errors: list[str] = []

    for patch in patches:
        # Валидируем патч перед применением
        is_valid, validation_error = _validate_patch(patch, result_md)
        if not is_valid:
            failed_patches.append(patch)
            errors.append(f"Патч '{patch.location_hint}': {validation_error}")
            continue

        # Ищем old_text в текущем результате
        if patch.old_text not in result_md:
            # Пытаемся найти с учётом вариаций пробелов
            match_pos = _find_text_with_fuzzy_match(result_md, patch.old_text)
            if match_pos:
                start, end = match_pos
                # Извлекаем найденный фрагмент
                found_text = result_md[start:end]
                # Применяем патч с найденным текстом
                result_md = result_md[:start] + patch.new_text + result_md[end:]
                applied_patches.append(patch)
                logger.info(
                    f"Патч '{patch.location_hint}' применён с fuzzy match "
                    f"(найден текст длиной {len(found_text)} символов)"
                )
                continue
            else:
                failed_patches.append(patch)
                errors.append(
                    f"Патч '{patch.location_hint}': old_text не найден в документе "
                    f"(длина old_text: {len(patch.old_text)} символов)"
                )
                continue

        # Применяем патч (заменяем только первое вхождение)
        result_md = result_md.replace(patch.old_text, patch.new_text, 1)
        applied_patches.append(patch)
        logger.info(f"Патч '{patch.location_hint}' успешно применён")

    success = len(failed_patches) == 0

    return PatchResult(
        success=success,
        applied_patches=applied_patches,
        failed_patches=failed_patches,
        result_md=result_md,
        errors=errors,
    )


def create_patch_prompt_template() -> str:
    """
    Создаёт шаблон промпта для запроса патчей в JSON-формате.

    Returns:
        Шаблон промпта
    """
    return """Верни ответ строго в JSON формате:

{
  "changes": [
    {
      "location_hint": "краткое описание места, где менять (например, заголовок раздела)",
      "old_text": "ТОЧНЫЙ фрагмент из original_md, который надо заменить",
      "new_text": "Новый текст вместо old_text"
    }
  ]
}

ТРЕБОВАНИЯ:
- В "old_text" копируй фрагмент ИЗ original_md без изменений.
- Не включай в "old_text" блоки кода, формул, диаграмм — редактируй только обычный текст вокруг.
- Не придумывай изменения, которых нет в комментариях.
- Каждый патч должен быть минимальным и точечным.
- Если нужно изменить несколько мест — создай несколько патчей.
"""

