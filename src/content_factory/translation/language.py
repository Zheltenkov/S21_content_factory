"""Язык наполнения файла — дёшево, по буквам, без модели.

Нужно в двух местах. По нему решается, трогать ли файл с данными: английский пример остаётся
как есть, иначе код учащегося, читающий колонку по имени, перестанет работать. И по нему же в
описи архива видно, что документы проекта английские, а комментарии в его скриптах русские —
в настоящем проекте Школы 21 бывает именно так, и переводить такой файл «на русский» бессмысленно.

Определение намеренно грубое: доля кириллицы среди букв. Различать языки одной письменности
по буквам нельзя, поэтому всё, кроме русского, называется ``other`` — для принятого правила
этого достаточно, а гадать дороже и без пользы.
"""

from __future__ import annotations

from collections.abc import Iterable

# Ниже этой доли кириллицы среди букв текст русским не считается: отдельное слово в английской
# строке веса не имеет.
_RUSSIAN_SHARE = 0.5


def looks_russian(text: str) -> bool:
    """Русский ли это текст. Строка без букв русской не считается."""
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return False
    cyrillic = sum(1 for char in letters if "Ѐ" <= char <= "ӿ")
    return cyrillic / len(letters) >= _RUSSIAN_SHARE


def dominant_language(values: Iterable[str]) -> str:
    """``ru``, ``other`` или ``unknown`` — по большинству строк, в которых вообще есть буквы."""
    russian = 0
    other = 0
    for value in values:
        if not any(char.isalpha() for char in value):
            continue
        if looks_russian(value):
            russian += 1
        else:
            other += 1
    if not russian and not other:
        return "unknown"
    return "ru" if russian >= other else "other"
