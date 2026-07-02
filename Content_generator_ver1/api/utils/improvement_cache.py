"""
Кэш для хранения данных улучшения README.

Хранит исходный README, извлеченные данные и улучшенный README
для последующего сравнения и генерации diff.
"""

import difflib
from datetime import datetime, timedelta

from content_gen.reverse_extraction.models import ClassificationResult, PartialProjectSeed

# In-memory кэш (в production можно использовать Redis)
_improvement_cache: dict[str, dict] = {}
# Связь между extract_request_id и generation_request_id
_extract_to_generation: dict[str, str] = {}
_generation_to_extract: dict[str, str] = {}
_improvement_owners: dict[str, str] = {}


def store_original_readme(request_id: str, readme_text: str, user_id: str | None = None) -> None:
    """
    Сохраняет исходный README в кэш.
    
    Args:
        request_id: ID запроса
        readme_text: Текст исходного README
    """
    if request_id not in _improvement_cache:
        _improvement_cache[request_id] = {}

    _improvement_cache[request_id]["original_readme"] = readme_text
    _improvement_cache[request_id]["created_at"] = datetime.now()
    if user_id:
        _improvement_owners[request_id] = user_id


def store_extracted_data(
    request_id: str,
    partial_seed: PartialProjectSeed,
    classification: ClassificationResult
) -> None:
    """
    Сохраняет извлеченные данные в кэш.
    
    Args:
        request_id: ID запроса
        partial_seed: Извлеченный PartialProjectSeed
        classification: Результат классификации
    """
    if request_id not in _improvement_cache:
        _improvement_cache[request_id] = {}

    _improvement_cache[request_id]["partial_seed"] = partial_seed
    _improvement_cache[request_id]["classification"] = classification
    _improvement_cache[request_id]["created_at"] = datetime.now()


def store_improved_readme(request_id: str, improved_readme: str) -> None:
    """
    Сохраняет улучшенный README в кэш.
    
    Args:
        request_id: ID запроса
        improved_readme: Текст улучшенного README
    """
    if request_id not in _improvement_cache:
        _improvement_cache[request_id] = {}

    _improvement_cache[request_id]["improved_readme"] = improved_readme
    _improvement_cache[request_id]["updated_at"] = datetime.now()


def get_original_readme(request_id: str) -> str | None:
    """Получает исходный README из кэша."""
    return _improvement_cache.get(request_id, {}).get("original_readme")


def get_extracted_data(
    request_id: str
) -> tuple[PartialProjectSeed, ClassificationResult] | None:
    """Получает извлеченные данные из кэша."""
    cache = _improvement_cache.get(request_id, {})
    partial_seed = cache.get("partial_seed")
    classification = cache.get("classification")

    if partial_seed and classification:
        return partial_seed, classification
    return None


def get_improved_readme(request_id: str) -> str | None:
    """Получает улучшенный README из кэша."""
    return _improvement_cache.get(request_id, {}).get("improved_readme")


def generate_diff(request_id: str) -> dict[str, any] | None:
    """
    Генерирует diff между исходным и улучшенным README.
    
    Args:
        request_id: ID запроса
        
    Returns:
        Словарь с diff данными или None если данных нет
    """
    original = get_original_readme(request_id)
    improved = get_improved_readme(request_id)

    if not original or not improved:
        return None

    # Генерируем unified diff
    original_lines = original.splitlines(keepends=True)
    improved_lines = improved.splitlines(keepends=True)

    diff = list(difflib.unified_diff(
        original_lines,
        improved_lines,
        fromfile="Исходный README",
        tofile="Улучшенный README",
        lineterm=""
    ))

    # Также генерируем side-by-side diff для удобного отображения
    diff_obj = difflib.SequenceMatcher(None, original_lines, improved_lines)
    opcodes = diff_obj.get_opcodes()

    side_by_side = []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            # Одинаковые строки
            for line in original_lines[i1:i2]:
                side_by_side.append({
                    "type": "equal",
                    "original": line.rstrip("\n"),
                    "improved": line.rstrip("\n")
                })
        elif tag == "delete":
            # Удаленные строки
            for line in original_lines[i1:i2]:
                side_by_side.append({
                    "type": "delete",
                    "original": line.rstrip("\n"),
                    "improved": None
                })
        elif tag == "insert":
            # Добавленные строки
            for line in improved_lines[j1:j2]:
                side_by_side.append({
                    "type": "insert",
                    "original": None,
                    "improved": line.rstrip("\n")
                })
        elif tag == "replace":
            # Замененные строки
            orig_lines = original_lines[i1:i2]
            impr_lines = improved_lines[j1:j2]
            max_len = max(len(orig_lines), len(impr_lines))

            for i in range(max_len):
                orig_line = orig_lines[i].rstrip("\n") if i < len(orig_lines) else None
                impr_line = impr_lines[i].rstrip("\n") if i < len(impr_lines) else None
                side_by_side.append({
                    "type": "replace",
                    "original": orig_line,
                    "improved": impr_line
                })

    return {
        "unified_diff": "".join(diff),
        "side_by_side": side_by_side,
        "stats": {
            "original_lines": len(original_lines),
            "improved_lines": len(improved_lines),
            "added": sum(1 for op in opcodes if op[0] == "insert"),
            "deleted": sum(1 for op in opcodes if op[0] == "delete"),
            "modified": sum(1 for op in opcodes if op[0] == "replace")
        }
    }


def cleanup_old_cache(max_age_hours: int = 24) -> None:
    """
    Очищает старые записи из кэша.
    
    Args:
        max_age_hours: Максимальный возраст записи в часах
    """
    now = datetime.now()
    max_age = timedelta(hours=max_age_hours)

    to_remove = []
    for request_id, data in _improvement_cache.items():
        created_at = data.get("created_at")
        if created_at and (now - created_at) > max_age:
            to_remove.append(request_id)

    for request_id in to_remove:
        del _improvement_cache[request_id]


def link_generation_request(extract_request_id: str, generation_request_id: str) -> None:
    """
    Связывает extract_request_id с generation_request_id.
    
    Args:
        extract_request_id: ID запроса извлечения
        generation_request_id: ID запроса генерации
    """
    _extract_to_generation[extract_request_id] = generation_request_id
    _generation_to_extract[generation_request_id] = extract_request_id
    if owner := _improvement_owners.get(extract_request_id):
        _improvement_owners[generation_request_id] = owner


def get_improvement_owner(request_id: str) -> str | None:
    """Возвращает владельца extract/generation request для README improvement."""
    return _improvement_owners.get(request_id)


def get_generation_request_id(extract_request_id: str) -> str | None:
    """Получает generation_request_id по extract_request_id."""
    return _extract_to_generation.get(extract_request_id)


def get_extract_request_id(generation_request_id: str) -> str | None:
    """Получает extract_request_id по generation_request_id."""
    return _generation_to_extract.get(generation_request_id)


def clear_cache(request_id: str | None = None) -> None:
    """
    Очищает кэш.
    
    Args:
        request_id: Если указан, очищает только этот запрос, иначе весь кэш
    """
    if request_id:
        _improvement_cache.pop(request_id, None)
        _improvement_owners.pop(request_id, None)
        generation_id = _extract_to_generation.pop(request_id, None)
        if generation_id:
            _generation_to_extract.pop(generation_id, None)
            _improvement_owners.pop(generation_id, None)
    else:
        _improvement_cache.clear()
        _extract_to_generation.clear()
        _generation_to_extract.clear()
        _improvement_owners.clear()

