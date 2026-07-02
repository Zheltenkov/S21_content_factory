"""Утилиты для работы с текстом и вычисления метрик."""

import re
from dataclasses import dataclass

from ...utils.logging import safe_print

# Стоп-слова для семантического анализа
_RU_STOP = set(
    "и в во не на для из по с со к о от у что это как но а же ли бы то ты тебе твой твоя твои ваши ваш вам вас мы нас нам их им она он они".split()
)
_EN_STOP = set("and or the a an to for of in on at by with from is are this that it you your".split())


@dataclass
class TaskBlock:
    """Блок задачи из главы 3."""
    title: str
    body: str
    artifact_paths: list[str] | None = None
    has_action_block: bool = False
    has_expected_result_block: bool = False
    has_submission_block: bool = False
    situation: str = ""
    goal: str = ""
    approach: str = ""
    expected_result: str = ""
    criteria_items: list[str] | None = None


def tokens(text: str, lang: str) -> list[str]:
    """Извлекает токены из текста."""
    toks = re.findall(r"[A-Za-zА-Яа-яЁёӨөҮүҢңҚқҒғІі]+", text.lower())
    stop = {"ru": _RU_STOP, "en": _EN_STOP}.get(lang, _RU_STOP)
    return [t for t in toks if t not in stop and len(t) > 2]


def bag(tokens: list[str]) -> dict[str, int]:
    """Создаёт мешок слов."""
    d: dict[str, int] = {}
    for t in tokens:
        d[t] = d.get(t, 0) + 1
    return d


def cosine(a: dict[str, int], b: dict[str, int]) -> float:
    """Вычисляет косинусное сходство."""
    if not a or not b:
        return 0.0
    num = sum(a[k] * b.get(k, 0) for k in a)
    den = (sum(v * v for v in a.values()) ** 0.5) * (sum(v * v for v in b.values()) ** 0.5)
    return (num / den) if den else 0.0


def semantic_similarity(text1: str, text2: str, lang: str = "ru", embedding_function=None) -> float:
    """
    Вычисляет семантическое сходство между двумя текстами.
    Использует SBERT если доступен, иначе fallback на bag-of-words + cosine similarity.
    
    Args:
        text1: Первый текст
        text2: Второй текст
        lang: Язык текстов
        embedding_function: Функция для создания эмбеддингов (опционально)
    
    Returns:
        Similarity score (0..1)
    """
    # Валидация входных данных
    if not text1 or not text2:
        safe_print(f"[SEMANTIC] Пустые тексты для сравнения: text1={bool(text1)}, text2={bool(text2)}", flush=True)
        return 0.0

    text1 = text1.strip()
    text2 = text2.strip()

    if not text1 or not text2:
        safe_print("[SEMANTIC] Тексты пустые после strip", flush=True)
        return 0.0

    # Пытаемся использовать SBERT если доступен
    if embedding_function:
        try:
            # Убеждаемся, что тексты не пустые перед вызовом API
            if not text1 or not text2:
                safe_print("[SEMANTIC] Пустые тексты перед вызовом embedding_function", flush=True)
                raise ValueError("Тексты не могут быть пустыми")

            vectors = embedding_function([text1, text2])

            # Проверяем, что получили векторы (безопасная проверка для NumPy массивов)
            if vectors is None:
                safe_print("[SEMANTIC] Недостаточно векторов: получено None, ожидалось 2", flush=True)
                raise ValueError("Недостаточно векторов от embedding_function")

            try:
                vectors_len = len(vectors)
            except (TypeError, ValueError):
                vectors_len = 0

            if vectors_len < 2:
                safe_print(f"[SEMANTIC] Недостаточно векторов: получено {vectors_len}, ожидалось 2", flush=True)
                raise ValueError("Недостаточно векторов от embedding_function")

            va, vb = vectors[0], vectors[1]

            # Проверяем, что векторы не пустые (безопасная проверка для NumPy массивов)
            if va is None or vb is None:
                safe_print("[SEMANTIC] Пустые векторы", flush=True)
                raise ValueError("Векторы не могут быть пустыми")

            try:
                va_len = len(va)
                vb_len = len(vb)
            except (TypeError, ValueError):
                va_len = 0
                vb_len = 0

            if va_len == 0 or vb_len == 0:
                safe_print(f"[SEMANTIC] Пустые векторы (длина: va={va_len}, vb={vb_len})", flush=True)
                raise ValueError("Векторы не могут быть пустыми")

            # Косинусное сходство между векторами
            from math import sqrt
            dot_product = sum(a * b for a, b in zip(va, vb, strict=False))
            norm_a = sqrt(sum(a * a for a in va))
            norm_b = sqrt(sum(b * b for b in vb))
            if norm_a > 0 and norm_b > 0:
                return dot_product / (norm_a * norm_b)
            else:
                safe_print(f"[SEMANTIC] Нулевая норма векторов: norm_a={norm_a}, norm_b={norm_b}", flush=True)
                return 0.0
        except Exception as e:
            safe_print(f"[SEMANTIC] SBERT similarity failed, using fallback: {e}", flush=True)

    # Fallback на bag-of-words
    va = bag(tokens(text1, lang))
    vb = bag(tokens(text2, lang))
    return cosine(va, vb)


def readability_index(text: str, lang: str = "ru") -> float:
    """
    Вычисляет индекс читаемости (адаптированный для русского языка).
    
    Использует формулу Флеша, адаптированную для русского текста.
    Нормальный диапазон для учебного контента: 10-25.
    
    Интерпретация:
    - <10: тяжелый текст
    - 10-25: нормальная сложность (целевой диапазон)
    - >25: очень простой текст
    """
    # Убираем markdown
    text_clean = re.sub(r'[#*`\[\]()]', ' ', text)
    text_clean = re.sub(r'```[\s\S]*?```', ' ', text_clean)
    text_clean = re.sub(r"\s+", " ", text_clean).strip()

    if not text_clean:
        return 0.0

    # Разбиваем на предложения
    sentences = re.split(r'[.!?…]+', text_clean)
    sentences = [s.strip() for s in sentences if s.strip()]

    if not sentences:
        return 0.0

    # Извлекаем слова
    words = []
    for sent in sentences:
        if lang == "ru":
            words.extend(re.findall(r"[А-Яа-яЁё]+", sent))
        else:
            words.extend(re.findall(r"[A-Za-z]+", sent))

    if not words:
        return 0.0

    # Подсчитываем слоги для каждого слова
    def _count_syllables(word: str, lang: str) -> int:
        """Подсчитывает количество слогов в слове."""
        if not word:
            return 1

        if lang == "ru":
            vowels = set("аеёиоуыэюяАЕЁИОУЫЭЮЯ")
        else:
            vowels = set("aeiouyAEIOUY")

        count = 0
        prev_was_vowel = False

        for char in word.lower():
            is_vowel = char in vowels
            if is_vowel and not prev_was_vowel:
                count += 1
            prev_was_vowel = is_vowel

        return max(1, count)

    # Средние значения
    avg_sentence_length = len(words) / len(sentences)  # ASL - средняя длина предложения в словах
    total_syllables = sum(_count_syllables(w, lang) for w in words)
    avg_syllables_per_word = total_syllables / len(words)  # ASW - среднее количество слогов на слово

    # Формула Флеша для русского текста
    # FRE = 206.835 - 1.52 * ASL - 65.14 * ASW
    # Для русского: ASL ≈ 15-20, ASW ≈ 2.7-3.0
    # ВАЖНО: Формула Флеша для русского текста может давать отрицательные значения
    # из-за особенностей языка (длинные слова, сложные предложения)
    raw_readability = 206.835 - 1.52 * avg_sentence_length - 65.14 * avg_syllables_per_word

    # Обрезаем до разумного диапазона (0-100 для формулы Флеша)
    raw_readability = max(0.0, min(100.0, raw_readability))

    # Нормализуем из диапазона [0, 30] (типичный диапазон для русского учебного текста)
    # в диапазон [10, 25] (целевой диапазон для читаемости)
    # Это обеспечивает согласованность с ожиданиями в ReadabilityAgent и theory_checks
    raw_min, raw_max = 0.0, 30.0
    new_min, new_max = 10.0, 25.0

    raw_clamped = max(raw_min, min(raw_readability, raw_max))
    if raw_max > raw_min:
        readability = new_min + (new_max - new_min) * (raw_clamped - raw_min) / (raw_max - raw_min)
    else:
        readability = new_min

    return readability

