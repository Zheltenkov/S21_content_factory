"""
Вспомогательные функции для работы с Markdown.
Устраняет дублирование логики вставки/замены глав.
"""

import re


def replace_chapter_content(md: str, chapter_number: int, new_body: str, language: str = "ru") -> str:
    """
    Заменяет содержимое главы, сохраняя заголовок.
    
    Args:
        md: Markdown документ
        chapter_number: Номер главы (1, 2, 3)
        new_body: Новое содержимое главы (без заголовка)
        language: Язык (ru, en, ky)
        
    Returns:
        Обновленный Markdown
    """
    # Паттерны для заголовков глав в зависимости от языка
    chapter_patterns = {
        "ru": rf"^##\s+Глава\s+{chapter_number}[^\n]*\n",
        "en": rf"^##\s+Chapter\s+{chapter_number}[^\n]*\n",
        "ky": rf"^##\s+{chapter_number}-Бөлүм[^\n]*\n",
    }

    pattern = chapter_patterns.get(language, chapter_patterns["ru"])

    # Находим начало следующей главы или конец документа
    next_chapter_pattern = {
        "ru": rf"^##\s+Глава\s+{chapter_number + 1}[^\n]*\n",
        "en": rf"^##\s+Chapter\s+{chapter_number + 1}[^\n]*\n",
        "ky": rf"^##\s+{chapter_number + 1}-Бөлүм[^\n]*\n",
    }
    next_pattern = next_chapter_pattern.get(language, next_chapter_pattern["ru"])

    # Ищем главу
    match = re.search(pattern, md, re.MULTILINE)
    if not match:
        # Если не нашли, пробуем более гибкий паттерн
        flexible_pattern = rf"^##\s+.*?{chapter_number}[^\n]*\n"
        match = re.search(flexible_pattern, md, re.MULTILINE)
        if not match:
            return md

    header_start = match.start()  # Начало заголовка
    header_end = match.end()  # Конец заголовка (после \n)

    # Ищем конец главы (начало следующей главы или конец документа)
    next_match = re.search(next_pattern, md[header_end:], re.MULTILINE)
    if next_match:
        end_pos = header_end + next_match.start()
    else:
        # Ищем конец документа или раздел "Бонус"
        bonus_match = re.search(r"^##\s+Бонус[^\n]*\n", md[header_end:], re.MULTILINE)
        if bonus_match:
            end_pos = header_end + bonus_match.start()
        else:
            end_pos = len(md)

    # Заменяем содержимое, сохраняя заголовок
    # Важно: не добавляем заголовок в new_content, так как он уже есть в md
    header = match.group(0)
    new_content = "\n" + new_body.strip() + "\n"

    # Если следующая глава - добавляем перенос строки перед ней
    if next_match or bonus_match:
        new_content = new_content.rstrip() + "\n"

    # Собираем: все до заголовка + заголовок + новое содержимое + все после конца главы
    result = md[:header_end] + new_content + md[end_pos:]

    # КРИТИЧЕСКИ ВАЖНО: Удаляем жирные заголовки глав, которые могут появиться сразу после основного заголовка
    # Например: ## Глава 2. Теория\n**Глава 2. Теория**\n...
    if chapter_number == 2:
        # Удаляем **Глава 2. Теория** или **Глава 2** сразу после ## Глава 2
        result = re.sub(
            r'(##\s+Глава\s+2[^\n]*\n)\s*\n*\s*\*\*Глава\s+2[^\*]+\*\*\s*\n+',
            r'\1',
            result,
            flags=re.MULTILINE
        )
        result = re.sub(
            r'(##\s+Глава\s+2[^\n]*\n)\s*\n*\s*\*\*Глава\s+2\*\*\s*\n+',
            r'\1',
            result,
            flags=re.MULTILINE
        )

    # Убеждаемся, что перед следующей главой есть перенос строки
    if chapter_number == 2:  # После Главы 2 должна быть Глава 3
        result = re.sub(
            r'(\n)(##\s+Глава\s+3[^\n]*\n)',
            r'\1\n\2',
            result,
            flags=re.MULTILINE
        )

    return result


def extract_chapter_content(md: str, chapter_number: int, language: str = "ru") -> tuple[str, str] | None:
    """
    Извлекает заголовок и содержимое главы.
    
    Args:
        md: Markdown документ
        chapter_number: Номер главы
        language: Язык
        
    Returns:
        Кортеж (заголовок, содержимое) или None
    """
    chapter_patterns = {
        "ru": rf"^##\s+Глава\s+{chapter_number}[^\n]*\n",
        "en": rf"^##\s+Chapter\s+{chapter_number}[^\n]*\n",
        "ky": rf"^##\s+{chapter_number}-Бөлүм[^\n]*\n",
    }

    pattern = chapter_patterns.get(language, chapter_patterns["ru"])
    match = re.search(pattern, md, re.MULTILINE)

    if not match:
        return None

    header = match.group(0).strip()
    start_pos = match.end()

    # Ищем конец главы
    next_chapter_pattern = {
        "ru": rf"^##\s+Глава\s+{chapter_number + 1}[^\n]*\n",
        "en": rf"^##\s+Chapter\s+{chapter_number + 1}[^\n]*\n",
        "ky": rf"^##\s+{chapter_number + 1}-Бөлүм[^\n]*\n",
    }
    next_pattern = next_chapter_pattern.get(language, next_chapter_pattern["ru"])

    next_match = re.search(next_pattern, md[start_pos:], re.MULTILINE)
    if next_match:
        content = md[start_pos:start_pos + next_match.start()].strip()
    else:
        bonus_match = re.search(r"^##\s+Бонус[^\n]*\n", md[start_pos:], re.MULTILINE)
        if bonus_match:
            content = md[start_pos:start_pos + bonus_match.start()].strip()
        else:
            content = md[start_pos:].strip()

    return (header, content)


def has_duplicate_chapter_headers(md: str, language: str = "ru") -> bool:
    """
    Проверяет наличие дублирующихся заголовков глав.
    
    Args:
        md: Markdown документ
        language: Язык
        
    Returns:
        True если есть дубликаты
    """
    chapter_patterns = {
        "ru": r"^##\s+Глава\s+(\d+)[^\n]*\n",
        "en": r"^##\s+Chapter\s+(\d+)[^\n]*\n",
        "ky": r"^##\s+(\d+)-Бөлүм[^\n]*\n",
    }

    pattern = chapter_patterns.get(language, chapter_patterns["ru"])
    matches = list(re.finditer(pattern, md, re.MULTILINE))

    # Проверяем дубликаты
    seen_chapters = set()
    for match in matches:
        chapter_num = match.group(1)
        if chapter_num in seen_chapters:
            return True
        seen_chapters.add(chapter_num)

    return False


def clean_duplicate_chapter_headers(md: str, language: str = "ru") -> str:
    """
    Удаляет дублирующиеся заголовки глав (оставляет только первый).
    Также удаляет жирные дубликаты типа **Глава 2. Теория** в любом месте после основного заголовка.
    
    Args:
        md: Markdown документ
        language: Язык
        
    Returns:
        Очищенный Markdown
    """
    chapter_patterns = {
        "ru": r"^##\s+Глава\s+(\d+)[^\n]*\n",
        "en": r"^##\s+Chapter\s+(\d+)[^\n]*\n",
        "ky": r"^##\s+(\d+)-Бөлүм[^\n]*\n",
    }

    pattern = chapter_patterns.get(language, chapter_patterns["ru"])
    lines = md.split('\n')
    cleaned_lines = []
    seen_chapters = set()

    i = 0
    while i < len(lines):
        line = lines[i]
        match = re.match(pattern, line)

        if match:
            chapter_num = match.group(1)
            if chapter_num in seen_chapters:
                # Пропускаем дубликат
                i += 1
                continue
            seen_chapters.add(chapter_num)

        # Удаляем жирные дубликаты заголовков глав (например, **Глава 2. Теория**)
        # Проверяем как в начале строки, так и с пробелами
        bold_patterns = {
            "ru": r"^\s*\*\*Глава\s+(\d+)[^\*]+\*\*\s*$",
            "en": r"^\s*\*\*Chapter\s+(\d+)[^\*]+\*\*\s*$",
            "ky": r"^\s*\*\*(\d+)-Бөлүм[^\*]+\*\*\s*$",
        }
        bold_pattern = bold_patterns.get(language, bold_patterns["ru"])
        bold_match = re.match(bold_pattern, line)
        if bold_match:
            chapter_num = bold_match.group(1)
            if chapter_num in seen_chapters:
                # Пропускаем жирный дубликат
                i += 1
                continue

        cleaned_lines.append(line)
        i += 1

    # Дополнительная очистка: удаляем жирные заголовки глав, которые могут быть в тексте
    # после основного заголовка главы (например, сразу после ## Глава 2)
    result = '\n'.join(cleaned_lines)

    # Удаляем жирные заголовки глав, которые идут сразу после основного заголовка
    bold_cleanup_patterns = {
        "ru": [
            r"(##\s+Глава\s+\d+[^\n]*\n)\s*\*\*Глава\s+\d+[^\*]+\*\*\s*\n+",
            r"(##\s+Глава\s+\d+[^\n]*\n)\s*\n+\s*\*\*Глава\s+\d+[^\*]+\*\*\s*\n+",
        ],
        "en": [
            r"(##\s+Chapter\s+\d+[^\n]*\n)\s*\*\*Chapter\s+\d+[^\*]+\*\*\s*\n+",
            r"(##\s+Chapter\s+\d+[^\n]*\n)\s*\n+\s*\*\*Chapter\s+\d+[^\*]+\*\*\s*\n+",
        ],
        "ky": [
            r"(##\s+\d+-Бөлүм[^\n]*\n)\s*\*\*\d+-Бөлүм[^\*]+\*\*\s*\n+",
            r"(##\s+\d+-Бөлүм[^\n]*\n)\s*\n+\s*\*\*\d+-Бөлүм[^\*]+\*\*\s*\n+",
        ],
    }

    cleanup_patterns = bold_cleanup_patterns.get(language, bold_cleanup_patterns["ru"])
    for cleanup_pattern in cleanup_patterns:
        result = re.sub(cleanup_pattern, r'\1', result, flags=re.MULTILINE)

    return result

