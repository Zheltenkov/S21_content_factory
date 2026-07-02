"""
content_gen/utils/text_analysis.py

Ускоренный text_analysis:
- единый markdown-stripping
- предкомпилированные regex
- быстрый count_words
- оптимизированное извлечение определений
"""

import re

# --- PRE-COMPILED REGEX ---------------------------------------------

RE_CODE_BLOCK = re.compile(r"```[\s\S]*?```", re.MULTILINE)
RE_INLINE_CODE = re.compile(r"`[^`]+`")
RE_LINK = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
RE_IMG = re.compile(r"!\[[^\]]*\]\([^\)]+\)")
RE_TAG = re.compile(r"<[^>]+>")
RE_URL = re.compile(r"https?://\S+")
RE_EMAIL = re.compile(r"\S+@\S+")
RE_MARKDOWN_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
RE_MARKDOWN_TABLE_SEPARATOR = re.compile(r"^\s*\|?[\s:|\-]+\|?\s*$")
RE_MARKDOWN_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
RE_MERMAID_INIT = re.compile(r"^\s*%%\{.*\}\s*$")

# Words
RE_WORD_RU = re.compile(r"[А-Яа-яЁёA-Za-z]+")
RE_WORD_EN = re.compile(r"[A-Za-z]+")
RE_WORD_KG = re.compile(r"[А-Яа-яЁёӨөҮүҢңҚқҒғІіA-Za-z]+")

# Term definitions (pre-compiled)
# Обновлены паттерны для поиска определений с жирным выделением терминов
# Важно: паттерны должны находить определения с **термин** в начале
# Термин может начинаться с кириллицы (А-ЯЁ) или латиницы (A-Z)
# Термин может содержать LaTeX-символы: _ { } \ ^

# Общий паттерн для термина (поддерживает LaTeX-переменные)
TERM_PATTERN = r"[А-ЯЁA-Z][А-Яа-яЁёA-Za-z0-9_\s\-\{\}\\^]+?"

# Паттерны для поиска определений с жирным выделением терминов
RE_DEF_PATTERNS_BOLD = [
    re.compile(rf"\*\*{TERM_PATTERN}\*\*\s*[—\-]\s*это\s+[^.!?]+?[.!?]", re.IGNORECASE),
    re.compile(rf"\*\*{TERM_PATTERN}\*\*\s*[—\-]\s*это\s+[^.!?]+", re.IGNORECASE),  # Без точки в конце
    re.compile(rf"Определени[ея]\s+\*\*{TERM_PATTERN}\*\*[:\s]+это\s+[^.!?]+?[.!?]", re.IGNORECASE),
    re.compile(rf"Определени[ея]\s+\*\*{TERM_PATTERN}\*\*[:\s]+это\s+[^.!?]+", re.IGNORECASE),  # Без точки
    re.compile(rf"\*\*{TERM_PATTERN}\*\*\s*представляет собой\s+[^.!?]+?[.!?]", re.IGNORECASE),
    re.compile(rf"\*\*{TERM_PATTERN}\*\*\s*является\s+[^.!?]+?[.!?]", re.IGNORECASE),
    # Исправлено: понима[а-яё]* ловит "понимается", "понимаются", "понимают", "понимает"
    re.compile(rf"Под\s+\*\*{TERM_PATTERN}\*\*\s+понима[а-яё]*\s+[^.!?]+?[.!?]", re.IGNORECASE),
    # Добавлено: подразумевается
    re.compile(rf"Под\s+\*\*{TERM_PATTERN}\*\*\s+подразумевается\s+[^.!?]+?[.!?]", re.IGNORECASE),
    re.compile(rf"\*\*{TERM_PATTERN}\*\*\s*—\s*[^.!?]+?[.!?]", re.IGNORECASE),  # Просто тире без "это"
    re.compile(rf"\*\*{TERM_PATTERN}\*\*\s*[:\s]+—\s*[^.!?]+?[.!?]", re.IGNORECASE),  # С двоеточием и тире
]

# Паттерны для поиска определений БЕЗ жирного выделения (fallback)
RE_DEF_PATTERNS_NO_BOLD = [
    # Формат: "Термин — это определение."
    re.compile(rf"{TERM_PATTERN}[—\-]\s*это\s+[^.!?]+?[.!?]", re.IGNORECASE),
    re.compile(rf"{TERM_PATTERN}[—\-]\s*это\s+[^.!?]+", re.IGNORECASE),  # Без точки
    # Формат: "Определение термина: это определение."
    re.compile(rf"Определени[ея]\s+{TERM_PATTERN}[:\s]+это\s+[^.!?]+?[.!?]", re.IGNORECASE),
    re.compile(rf"Определени[ея]\s+{TERM_PATTERN}[:\s]+это\s+[^.!?]+", re.IGNORECASE),  # Без точки
    # Формат: "Термин представляет собой определение."
    re.compile(rf"{TERM_PATTERN}\s+представляет собой\s+[^.!?]+?[.!?]", re.IGNORECASE),
    # Формат: "Термин является определением."
    re.compile(rf"{TERM_PATTERN}\s+является\s+[^.!?]+?[.!?]", re.IGNORECASE),
    # Формат: "Под термином понимают/понимается определение."
    # Исправлено: понима[а-яё]* ловит "понимается", "понимаются", "понимают", "понимает"
    re.compile(rf"Под\s+{TERM_PATTERN}\s+понима[а-яё]*\s+[^.!?]+?[.!?]", re.IGNORECASE),
    # Добавлено: подразумевается
    re.compile(rf"Под\s+{TERM_PATTERN}\s+подразумевается\s+[^.!?]+?[.!?]", re.IGNORECASE),
    # Формат: "Термин — определение." (просто тире)
    re.compile(rf"{TERM_PATTERN}\s+—\s+[^.!?]+?[.!?]", re.IGNORECASE),
    # Дополнительные форматы для более гибкого поиска
    re.compile(rf"{TERM_PATTERN}\s*:\s*[^.!?]+?[.!?]", re.IGNORECASE),  # "Термин: определение."
    re.compile(rf"{TERM_PATTERN}\s+—\s+[^.!?]+", re.IGNORECASE),  # "Термин — определение" без точки
]

# Объединенный список для обратной совместимости
RE_DEF_PATTERNS_RU = RE_DEF_PATTERNS_BOLD + RE_DEF_PATTERNS_NO_BOLD

RE_PRACTICE_QUESTIONS = re.compile(
    r'\*\*Вопросы к практике[:\*]*\*\*|Вопросы к практике',
    re.IGNORECASE
)

RE_DEFINED_TERM_PATTERNS = [
    re.compile(rf"\*\*({TERM_PATTERN})\*\*\s*[—\-]\s*(?:это\s+)?[^.!?]+", re.IGNORECASE),
    re.compile(rf"\*\*({TERM_PATTERN})\*\*\s+(?:представляет собой|является)\s+[^.!?]+", re.IGNORECASE),
    re.compile(rf"Под\s+\*\*({TERM_PATTERN})\*\*\s+(?:понима[а-яё]*|подразумевается)\s+[^.!?]+", re.IGNORECASE),
    re.compile(rf"({TERM_PATTERN})\s*[—\-]\s*(?:это\s+)?[^.!?]+", re.IGNORECASE),
    re.compile(rf"({TERM_PATTERN})\s+(?:представляет собой|является)\s+[^.!?]+", re.IGNORECASE),
    re.compile(rf"Под\s+({TERM_PATTERN})\s+(?:понима[а-яё]*|подразумевается)\s+[^.!?]+", re.IGNORECASE),
]


# --- MARKDOWN CLEAN --------------------------------------------------

def clean_markdown_for_counting(text: str) -> str:
    """Очищает markdown для подсчета слов."""
    text = RE_CODE_BLOCK.sub(" ", text)
    text = RE_INLINE_CODE.sub(" ", text)
    text = RE_LINK.sub(r"\1", text)
    text = RE_IMG.sub(" ", text)
    text = RE_TAG.sub(" ", text)
    text = RE_URL.sub(" ", text)
    text = RE_EMAIL.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_markdown_prose_for_counting(text: str) -> str:
    """
    Очищает markdown так, чтобы осталась только учебная проза.

    Используется для критериев объема теории: таблицы, fenced blocks,
    подписи к визуализациям и Mermaid-служебные строки не являются
    повествовательным текстом и не должны раздувать word count.
    """
    text = RE_CODE_BLOCK.sub(" ", text)
    text = RE_INLINE_CODE.sub(" ", text)
    text = RE_LINK.sub(r"\1", text)
    text = RE_IMG.sub(" ", text)
    text = RE_TAG.sub(" ", text)
    text = RE_URL.sub(" ", text)
    text = RE_EMAIL.sub(" ", text)

    prose_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            prose_lines.append("")
            continue
        if RE_MARKDOWN_TABLE_ROW.match(stripped) or RE_MARKDOWN_TABLE_SEPARATOR.match(stripped):
            continue
        if RE_MARKDOWN_HEADING.match(stripped):
            continue
        if RE_MERMAID_INIT.match(stripped):
            continue
        caption_match = re.match(
            r"^\*(?:Таблица|Схема|Диаграмма)\b[^*]*\*\s*\.?\s*(.*)$",
            stripped,
            flags=re.IGNORECASE,
        )
        if caption_match:
            tail = caption_match.group(1).strip()
            if tail:
                prose_lines.append(tail)
            continue
        if re.match(r"^\*(?:Таблица|Схема|Диаграмма)\b", stripped, flags=re.IGNORECASE):
            continue
        prose_lines.append(line)

    cleaned = "\n".join(prose_lines)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


# --- COUNT WORDS -----------------------------------------------------

def count_words(text: str, language: str = "ru") -> int:
    """Подсчитывает количество слов с учетом языка."""
    cleaned = clean_markdown_for_counting(text)
    if not cleaned:
        return 0

    if language == "en":
        words = RE_WORD_EN.findall(cleaned)
    elif language == "kg":
        words = RE_WORD_KG.findall(cleaned)
    else:  # ru or default
        words = RE_WORD_RU.findall(cleaned)

    return len(words)


def count_prose_words(text: str, language: str = "ru") -> int:
    """Подсчитывает слова только в prose-части markdown-документа."""
    cleaned = clean_markdown_prose_for_counting(text)
    if not cleaned:
        return 0

    if language == "en":
        words = RE_WORD_EN.findall(cleaned)
    elif language == "kg":
        words = RE_WORD_KG.findall(cleaned)
    else:
        words = RE_WORD_RU.findall(cleaned)

    return len(words)


# --- TERM DEFINITIONS ------------------------------------------------

def has_term_definitions(text: str, language: str = "ru", min_definitions: int = 2, require_bold: bool = True) -> tuple[bool, list[str]]:
    """
    Проверяет наличие определений терминов.
    
    Args:
        text: Текст для проверки
        language: Язык текста
        min_definitions: Минимальное количество определений
        require_bold: Требовать ли жирное выделение терминов (**термин**)
    
    Returns:
        (найдено ли достаточно определений, список найденных определений)
    """
    # Не очищаем markdown полностью, чтобы сохранить ** для проверки жирного выделения
    cleaned = RE_CODE_BLOCK.sub(" ", text)
    cleaned = RE_INLINE_CODE.sub(" ", cleaned)
    definitions = []

    if language == "ru":
        # Сначала ищем определения с жирным выделением (приоритет)
        bold_definitions = []
        regular_definitions = []

        # Ищем определения с жирным выделением
        for pattern in RE_DEF_PATTERNS_BOLD:
            matches = pattern.findall(cleaned)
            for match in matches:
                # Проверяем, что в определении действительно есть жирное выделение
                if re.search(r'\*\*[А-ЯЁA-Z][^\*]+\*\*', match):
                    bold_definitions.append(match)

        # Ищем определения без жирного выделения (fallback)
        for pattern in RE_DEF_PATTERNS_NO_BOLD:
            matches = pattern.findall(cleaned)
            for match in matches:
                # Проверяем, что это не просто случайное совпадение
                # Определение должно содержать минимум 10 символов и не быть частью кода
                if len(match.strip()) >= 10 and '```' not in match:
                    regular_definitions.append(match)

        if require_bold:
            definitions = bold_definitions
        else:
            # Если не требуется жирное, берем все (с приоритетом жирных)
            definitions = bold_definitions + regular_definitions
    # TODO: Добавить паттерны для en и kg при необходимости

    # Улучшенная дедупликация: нормализуем определения перед сравнением
    seen = set()
    unique_definitions = []
    for d in definitions:
        if len(d.strip()) < 10:  # Слишком короткие определения пропускаем
            continue
        # Нормализуем: убираем лишние пробелы, markdown-разметку, приводим к нижнему регистру для сравнения
        normalized = re.sub(r'\s+', ' ', d.strip().lower())
        normalized = re.sub(r'\*\*', '', normalized)  # Убираем жирное выделение для сравнения
        normalized = re.sub(r'[^\w\s]', '', normalized)  # Убираем пунктуацию для более точного сравнения
        if normalized not in seen and len(normalized) > 20:  # Минимум 20 символов после нормализации
            seen.add(normalized)
            unique_definitions.append(d)

    return len(unique_definitions) >= min_definitions, unique_definitions


# --- PRACTICE QUESTIONS ----------------------------------------------

def has_practice_questions(text: str) -> bool:
    """Проверяет наличие блока 'Вопросы к практике'."""
    return bool(RE_PRACTICE_QUESTIONS.search(text))


def extract_defined_terms(text: str, language: str = "ru", limit: int | None = None) -> list[str]:
    """
    Извлекает названия терминов из явных определений.

    Нужен прежде всего для передачи конспекта теории в практику без повторного
    парсинга markdown по хрупким ad-hoc regex.
    """
    if language != "ru" or not text:
        return []

    cleaned = RE_CODE_BLOCK.sub(" ", text)
    cleaned = RE_INLINE_CODE.sub(" ", cleaned)

    terms: list[str] = []
    seen: set[str] = set()

    for pattern in RE_DEFINED_TERM_PATTERNS:
        for match in pattern.findall(cleaned):
            term = match.strip()
            term = re.sub(r"\s+", " ", term)
            term = term.strip(" -*_:.")
            if len(term) < 2:
                continue
            normalized = re.sub(r"\s+", " ", term.lower())
            if normalized in seen:
                continue
            seen.add(normalized)
            terms.append(term)
            if limit and len(terms) >= limit:
                return terms

    return terms


def readability_index(text: str, language: str = "ru") -> float:
    """Вычисляет индекс читаемости в диапазоне примерно 10-25 для учебного текста."""
    cleaned = clean_markdown_for_counting(text)
    if not cleaned:
        return 0.0

    sentences = [sentence.strip() for sentence in re.split(r"[.!?…]+", cleaned) if sentence.strip()]
    if not sentences:
        return 0.0

    if language == "en":
        words = RE_WORD_EN.findall(cleaned)
        vowels = set("aeiouyAEIOUY")
    else:
        words = RE_WORD_RU.findall(cleaned)
        vowels = set("аеёиоуыэюяАЕЁИОУЫЭЮЯ")

    if not words:
        return 0.0

    def _count_syllables(word: str) -> int:
        count = 0
        prev_is_vowel = False
        for char in word:
            is_vowel = char in vowels
            if is_vowel and not prev_is_vowel:
                count += 1
            prev_is_vowel = is_vowel
        return max(1, count)

    avg_sentence_length = len(words) / len(sentences)
    avg_syllables_per_word = sum(_count_syllables(word) for word in words) / len(words)

    raw_readability = 206.835 - 1.52 * avg_sentence_length - 65.14 * avg_syllables_per_word
    raw_readability = max(0.0, min(30.0, raw_readability))
    return 10.0 + (25.0 - 10.0) * (raw_readability / 30.0)


def extract_text_between_markers(
    text: str,
    start_marker: str,
    end_marker: str | None = None,
    exclude_markers: bool = True
) -> str:
    """
    Извлекает текст между маркерами (например, между "**Подход:**" и следующим "**").
    
    Args:
        text: Исходный текст
        start_marker: Начальный маркер
        end_marker: Конечный маркер (если None, ищет до следующего маркера или конца)
        exclude_markers: Исключать ли сами маркеры из результата
    
    Returns:
        Извлеченный текст
    """
    if end_marker:
        pattern = f'{re.escape(start_marker)}(.*?){re.escape(end_marker)}'
    else:
        # Ищем до следующего ** или конца текста
        pattern = f'{re.escape(start_marker)}(.*?)(?=\n\\*\\*|\\Z)'

    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""
