"""
content_gen/agents/length_agent.py

Агент для проверки и исправления длины частей теории.

Проверяет соответствие длины части диапазону из THRESHOLDS и перегенерирует
текст при необходимости, ОБЯЗАТЕЛЬНО сохраняя все определения терминов.
"""

import re

from ..config.thresholds import THRESHOLDS
from .base.llm_client import LLMClientProtocol
from ..models.schemas import ProjectSeed, TheoryPart
from ..utils.logging import safe_print
from ..utils.text_analysis import count_words
from ..repair.style_guard import StyleGuardRepair

SYSTEM = """Ты — эксперт по редактированию образовательного контента.
Твоя задача — корректировать длину текста, сохраняя все определения терминов и смысл.

КРИТИЧЕСКИ ВАЖНО:
- ОБЯЗАТЕЛЬНО используй елочки « » для кавычек вместо прямых кавычек " "
- Сохраняй обращение на «ты» (не «вы», не «участник»)
- ОБЯЗАТЕЛЬНО сохраняй ВСЕ определения терминов в формате: **термин** — определение
- Определения терминов — это приоритет! Их нельзя удалять или изменять
- Сохраняй таблицы и формулы, если они есть
- Пиши простым, понятным языком

Язык: {language}.
"""

EXTEND_TMPL = """Текст части теории слишком короткий. Расширь его до примерно {target_words} слов.

**Название части:** {title}

**Текущий текст:**
{body}

**Текущая длина:** {current_words} слов (требуется минимум {min_words} слов)

**КРИТИЧЕСКИ ВАЖНО - ОБЯЗАТЕЛЬНО:**
- Сохрани ВСЕ существующие определения терминов (формат: **термин** — определение)
- Расширь объяснения, добавь больше контекста и примеров
- Добавь детали, но не меняй смысл
- Текст должен быть примерно {target_words} слов
- Пиши на «ты», дружелюбно, без директив
- Сохрани таблицы и формулы, если они есть
- Если нужны математические выражения, используй формулы в LaTeX: ВСЕ формулы должны быть блочными $$формула$$ с новой строки (по центру) с обязательными определениями параметров
- ВСЕ формулы должны быть на отдельной строке (пустая строка до и после формулы)

Верни только переписанный текст БЕЗ заголовка и БЕЗ блоков **Пример:** и **Вопросы к практике:**.
"""

SHORTEN_TMPL = """Текст части теории слишком длинный. Сократи его до примерно {target_words} слов.

**Название части:** {title}

**Текущий текст:**
{body}

**Текущая длина:** {current_words} слов (максимум {max_words} слов)

**КРИТИЧЕСКИ ВАЖНО - ОБЯЗАТЕЛЬНО:**
- Сохрани ВСЕ существующие определения терминов (формат: **термин** — определение)
- Определения терминов — это приоритет! Их нельзя удалять при сокращении
- Сохрани смысл и основные идеи
- Сократи текст до примерно {target_words} слов
- Оставь только самое важное: определения терминов и ключевые концепции
- Пиши на «ты», дружелюбно, без директив
- Сохрани таблицы и формулы, если они есть - они важны для понимания
- Если таблицы слишком большие, можешь их упростить, но не удаляй полностью
- Сохрани определения параметров формул, если они есть
- Убедись, что все формулы на отдельных строках (блочный формат $$...$$)

Верни только переписанный текст БЕЗ заголовка и БЕЗ блоков **Пример:** и **Вопросы к практике:**.
"""


class LengthAgent:
    """
    Агент для проверки и исправления длины частей теории.
    
    Проверяет соответствие длины части диапазону из THRESHOLDS и перегенерирует
    текст при необходимости, ОБЯЗАТЕЛЬНО сохраняя все определения терминов.
    """

    def __init__(self, llm: LLMClientProtocol):
        """
        Инициализация агента.
        
        Args:
            llm: LLM клиент для генерации
        """
        self.llm = llm
        self.style = StyleGuardRepair()

    def check_length(self, part: TheoryPart, seed: ProjectSeed) -> tuple[bool, int, str]:
        """
        Проверяет длину части теории.
        
        Args:
            part: Часть теории для проверки
            seed: Входные данные проекта
        
        Returns:
            (соответствует ли длина диапазону, количество слов, направление корректировки: "ok" | "longer" | "shorter")
        """
        lo, hi = THRESHOLDS["theory_words_per_part"]
        words = count_words(part.body, seed.language)

        if words < lo:
            return False, words, "longer"
        elif words > hi:
            return False, words, "shorter"
        else:
            return True, words, "ok"

    def fix_length(
        self,
        part: TheoryPart,
        seed: ProjectSeed,
        max_attempts: int = 2
    ) -> TheoryPart:
        """
        Исправляет длину части теории, сохраняя все определения терминов.
        
        Args:
            part: Часть теории для исправления
            seed: Входные данные проекта
            max_attempts: Максимальное количество попыток исправления
        
        Returns:
            Часть теории с исправленной длиной
        """
        lo, hi = THRESHOLDS["theory_words_per_part"]
        words = count_words(part.body, seed.language)

        if lo <= words <= hi:
            safe_print(f"  ✅ Часть '{part.title[:50]}...': {words} слов (в диапазоне {lo}-{hi})", flush=True)
            return part

        direction = "longer" if words < lo else "shorter"
        target_words = (lo + hi) // 2

        safe_print(
            f"  ⚠️ Часть '{part.title[:50]}...': {words} слов "
            f"({'<' if direction == 'longer' else '>'} {lo if direction == 'longer' else hi}). "
            f"Исправление...",
            flush=True
        )

        for attempt in range(1, max_attempts + 1):
            try:
                system_prompt = SYSTEM.format(language=seed.language)

                if direction == "longer":
                    user_prompt = EXTEND_TMPL.format(
                        title=part.title,
                        body=part.body,
                        current_words=words,
                        min_words=lo,
                        target_words=target_words
                    )
                else:  # shorter
                    user_prompt = SHORTEN_TMPL.format(
                        title=part.title,
                        body=part.body,
                        current_words=words,
                        max_words=hi,
                        target_words=target_words
                    )

                regenerated_body = self.llm.complete(
                    system=system_prompt,
                    user=user_prompt,
                    temperature=0.2
                )

                regenerated_body = regenerated_body.strip()

                # Удаляем канонический заголовок раздела, если LLM его добавил.
                regenerated_body = re.sub(r'^###\s+2\.\d+\.\s*[^\n]+\n+', '', regenerated_body, flags=re.M)
                regenerated_body = regenerated_body.strip()

                # Применяем StyleGuard
                regenerated_body = self.style.rewrite(regenerated_body, seed.language)

                # Проверяем, что результат не пустой
                if not regenerated_body or len(regenerated_body) < 50:
                    safe_print("  ⚠️ Генерация вернула слишком короткий текст, пробуем еще раз...", flush=True)
                    continue

                new_words = count_words(regenerated_body, seed.language)

                # Если все еще не в диапазоне и это последняя попытка, применяем принудительное сокращение
                if direction == "shorter" and new_words > hi and attempt == max_attempts:
                    safe_print(
                        f"  ⚠️ После перегенерации все еще {new_words} слов. Принудительное сокращение...",
                        flush=True
                    )
                    # Разбиваем на предложения и берем первые до нужного количества слов
                    sentences = re.split(r'(?<=[.!?])\s+', regenerated_body)
                    shortened = []
                    current_words = 0
                    for sent in sentences:
                        sent_words = count_words(sent, seed.language)
                        if current_words + sent_words <= hi:
                            shortened.append(sent)
                            current_words += sent_words
                        else:
                            break
                    regenerated_body = " ".join(shortened)
                    new_words = count_words(regenerated_body, seed.language)
                    safe_print(f"  ✅ Принудительно сокращено до {new_words} слов", flush=True)

                # Проверяем результат
                if lo <= new_words <= hi:
                    safe_print(f"  ✅ Исправлено: {new_words} слов (было {words})", flush=True)
                    return TheoryPart(
                        title=part.title,
                        body=regenerated_body,
                        example=part.example,
                        bridge_questions=part.bridge_questions,
                        covers_outcomes=part.covers_outcomes,
                        references=part.references.copy() if part.references else []
                    )
                else:
                    safe_print(
                        f"  ⚠️ После исправления: {new_words} слов "
                        f"(ожидается {lo}-{hi}), пробуем еще раз...",
                        flush=True
                    )
                    words = new_words
                    part = TheoryPart(
                        title=part.title,
                        body=regenerated_body,
                        example=part.example,
                        bridge_questions=part.bridge_questions,
                        covers_outcomes=part.covers_outcomes,
                        references=part.references.copy() if part.references else []
                    )

            except Exception as e:
                import traceback
                safe_print(f"  ⚠️ Ошибка при исправлении длины: {str(e)}", flush=True)
                safe_print(f"     Детали: {traceback.format_exc()[:500]}", flush=True)
                continue

        safe_print(
            f"  ⚠️ Не удалось исправить длину после {max_attempts} попыток, "
            f"возвращаем исходную часть",
            flush=True
        )
        return part

    def process_parts(
        self,
        parts: list[TheoryPart],
        seed: ProjectSeed
    ) -> list[TheoryPart]:
        """
        Обрабатывает все части теории, исправляя длину при необходимости.
        
        Args:
            parts: Список частей теории
            seed: Входные данные проекта
        
        Returns:
            Список частей теории с исправленной длиной
        """
        if not parts:
            return []

        safe_print(f"  📏 Проверка длины в {len(parts)} частях теории...", flush=True)

        processed_parts = []
        for i, part in enumerate(parts, 1):
            safe_print(f"  📏 Проверка части {i}/{len(parts)}: {part.title[:50]}...", flush=True)
            processed_part = self.fix_length(part, seed)
            processed_parts.append(processed_part)

        safe_print("  ✅ Проверка длины завершена", flush=True)

        return processed_parts
