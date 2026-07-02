"""
content_gen/agents/readability_agent.py

Агент для проверки и улучшения читаемости текста.

Проверяет индекс читаемости (10-25) и улучшает текст при необходимости,
упрощая предложения и разбивая длинные конструкции.
"""

import re

from .base.llm_client import LLMClientProtocol
from ..models.schemas import ProjectSeed, TheoryPart
from ..utils.logging import safe_print
from ..validators.rubric import _readability_index

SYSTEM = """Ты — эксперт по улучшению читаемости образовательного контента.
Твоя задача — упрощать текст, делая его более понятным, сохраняя смысл и определения терминов.

КРИТИЧЕСКИ ВАЖНО:
- ОБЯЗАТЕЛЬНО используй елочки « » для кавычек вместо прямых кавычек " "
- Сохраняй обращение на «ты» (не «вы», не «участник»)
- ОБЯЗАТЕЛЬНО сохраняй ВСЕ определения терминов в формате: **термин** — определение
- Используй ОЧЕНЬ КОРОТКИЕ предложения (в среднем 6-10 слов, максимум 12 слов, НЕ БОЛЬШЕ!)
- Избегай сложных конструкций, канцеляризмов, длинных причастных и деепричастных оборотов
- НЕ используй сложные предложения с несколькими придаточными - разбивай их на простые
- Объясняй сложные термины простыми словами
- Структурируй текст короткими абзацами (по 2-3 предложения, не больше!)
- Разбивай длинные предложения на короткие - это критически важно для читаемости!
- Используй простые слова вместо сложных терминов, где возможно
- Избегай длинных слов и сложных конструкций
- Индекс читаемости должен быть в диапазоне 10-25 (нормальная сложность для учебного контента)

Язык: {language}.
"""

IMPROVE_TMPL = """Текст части теории имеет низкий индекс читаемости. Упрости его, сделав более понятным.

**Название части:** {title}

**Текущий текст:**
{body}

**Текущий индекс читаемости:** {current_index:.1f} (целевой диапазон: 10-25)

**КРИТИЧЕСКИ ВАЖНО - ОБЯЗАТЕЛЬНО:**
- Сохрани ВСЕ существующие определения терминов (формат: **термин** — определение)
- Сохрани смысл и основные идеи
- Используй ОЧЕНЬ КОРОТКИЕ предложения (в среднем 6-10 слов, максимум 12 слов)
- Разбивай длинные предложения на короткие
- Избегай сложных конструкций, канцеляризмов, длинных причастных и деепричастных оборотов
- НЕ используй сложные предложения с несколькими придаточными - разбивай их на простые
- Объясняй сложные термины простыми словами
- Структурируй текст короткими абзацами (по 2-3 предложения, не больше!)
- Используй простые слова вместо сложных терминов, где возможно
- Избегай длинных слов и сложных конструкций
- Пиши на «ты», дружелюбно, без директив
- Сохрани таблицы и формулы, если они есть
- Убедись, что все формулы на отдельных строках (блочный формат $$...$$)

**Примеры упрощения:**
- Было: "Проект, который был создан для решения сложных задач управления, требует тщательного планирования."
- Стало: "Проект решает сложные задачи управления. Он требует тщательного планирования."

- Было: "Управление проектом, которое включает в себя множество различных процессов и методов, является важной частью современной деятельности."
- Стало: "Управление проектом включает множество процессов. Оно использует разные методы. Это важная часть современной деятельности."

Верни только переписанный текст БЕЗ заголовка и БЕЗ блоков **Пример:** и **Вопросы к практике:**.
"""


class ReadabilityAgent:
    """
    Агент для проверки и улучшения читаемости текста.
    
    Проверяет индекс читаемости (10-25) и улучшает текст при необходимости,
    упрощая предложения и разбивая длинные конструкции.
    """

    def __init__(self, llm: LLMClientProtocol):
        """
        Инициализация агента.
        
        Args:
            llm: LLM клиент для генерации
        """
        self.llm = llm

    def check_readability(self, part: TheoryPart, seed: ProjectSeed) -> tuple[bool, float]:
        """
        Проверяет индекс читаемости части теории.
        
        Args:
            part: Часть теории для проверки
            seed: Входные данные проекта
        
        Returns:
            (соответствует ли индекс диапазону 10-25, значение индекса)
        """
        index = _readability_index(part.body, seed.language)
        is_ok = 10.0 <= index <= 25.0
        return is_ok, index

    def improve_readability(
        self,
        part: TheoryPart,
        seed: ProjectSeed,
        max_attempts: int = 2
    ) -> TheoryPart:
        """
        Улучшает читаемость части теории, упрощая текст.
        
        Args:
            part: Часть теории для улучшения
            seed: Входные данные проекта
            max_attempts: Максимальное количество попыток улучшения
        
        Returns:
            Часть теории с улучшенной читаемостью
        """
        is_ok, current_index = self.check_readability(part, seed)

        if is_ok:
            safe_print(
                f"  ✅ Часть '{part.title[:50]}...': индекс читаемости {current_index:.1f} (в диапазоне 10-25)",
                flush=True
            )
            return part

        safe_print(
            f"  ⚠️ Часть '{part.title[:50]}...': индекс читаемости {current_index:.1f} "
            f"({'<' if current_index < 10 else '>'} {'10' if current_index < 10 else '25'}). "
            f"Улучшение...",
            flush=True
        )

        for attempt in range(1, max_attempts + 1):
            try:
                system_prompt = SYSTEM.format(language=seed.language)
                user_prompt = IMPROVE_TMPL.format(
                    title=part.title,
                    body=part.body,
                    current_index=current_index
                )

                improved_body = self.llm.complete(
                    system=system_prompt,
                    user=user_prompt,
                    temperature=0.2
                )

                improved_body = improved_body.strip()

                # Удаляем канонический заголовок раздела, если LLM его добавил.
                improved_body = re.sub(r'^###\s+2\.\d+\.\s*[^\n]+\n+', '', improved_body, flags=re.M)
                improved_body = improved_body.strip()

                # Проверяем, что результат не пустой
                if not improved_body or len(improved_body) < 50:
                    safe_print("  ⚠️ Генерация вернула слишком короткий текст, пробуем еще раз...", flush=True)
                    continue

                new_index = _readability_index(improved_body, seed.language)

                # Проверяем результат
                if 10.0 <= new_index <= 25.0:
                    safe_print(
                        f"  ✅ Улучшено: индекс читаемости {new_index:.1f} (было {current_index:.1f})",
                        flush=True
                    )
                    return TheoryPart(
                        title=part.title,
                        body=improved_body,
                        example=part.example,
                        bridge_questions=part.bridge_questions,
                        covers_outcomes=part.covers_outcomes,
                        references=part.references.copy() if part.references else []
                    )
                else:
                    safe_print(
                        f"  ⚠️ После улучшения: индекс {new_index:.1f} "
                        f"(ожидается 10-25), пробуем еще раз...",
                        flush=True
                    )
                    current_index = new_index
                    part = TheoryPart(
                        title=part.title,
                        body=improved_body,
                        example=part.example,
                        bridge_questions=part.bridge_questions,
                        covers_outcomes=part.covers_outcomes,
                        references=part.references.copy() if part.references else []
                    )

            except Exception as e:
                import traceback
                safe_print(f"  ⚠️ Ошибка при улучшении читаемости: {str(e)}", flush=True)
                safe_print(f"     Детали: {traceback.format_exc()[:500]}", flush=True)
                continue

        safe_print(
            f"  ⚠️ Не удалось улучшить читаемость после {max_attempts} попыток, "
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
        Обрабатывает все части теории, улучшая читаемость при необходимости.
        
        Args:
            parts: Список частей теории
            seed: Входные данные проекта
        
        Returns:
            Список частей теории с улучшенной читаемостью
        """
        if not parts:
            return []

        safe_print(f"  📖 Проверка читаемости в {len(parts)} частях теории...", flush=True)

        processed_parts = []
        for i, part in enumerate(parts, 1):
            safe_print(f"  📖 Проверка части {i}/{len(parts)}: {part.title[:50]}...", flush=True)
            processed_part = self.improve_readability(part, seed)
            processed_parts.append(processed_part)

        safe_print("  ✅ Проверка читаемости завершена", flush=True)

        return processed_parts

