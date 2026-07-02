"""
content_gen/agents/definitions_agent.py

Агент для проверки и генерации определений терминов в частях теории.

Проверяет наличие минимум 1 определения в каждой части с форматом:
**термин** — определение

Генерирует определения, если их недостаточно.
"""

import re
import time

from ..exceptions import LLMTimeoutError
from .base.llm_client import LLMClientProtocol
from ..models.schemas import ProjectSeed, TheoryPart
from ..utils.logging import safe_print
from ..utils.text_analysis import has_term_definitions

SYSTEM = """Ты — эксперт по созданию определений терминов для образовательного контента.
Твоя задача — создавать четкие, понятные определения терминов в формате: **термин** — определение.

КРИТИЧЕСКИ ВАЖНО:
- ОБЯЗАТЕЛЬНО используй елочки « » для кавычек вместо прямых кавычек " "
- Сохраняй обращение на «ты» (не «вы», не «участник»)
- Определения должны быть простыми и понятными
- Используй жирное выделение для терминов: **термин**

Язык: {language}.
"""

CHECK_TMPL = """Проверь текст части теории на наличие определений терминов.

**Название части:** {title}

**Текст части:**
{body}

**Требования:**
- Минимум 1 определение термина в формате: **термин** — определение
- Термин должен быть выделен жирным (**термин**)
- После термина должен быть дефис (— или -) и определение
- Определения должны быть полными предложениями с точкой

**Примеры правильных определений:**
- **Проект** — это временное предприятие, направленное на создание уникального продукта.
- **Управление проектом** — это применение знаний, навыков и методов для достижения целей проекта.
- **Артефакт** — это проверяемый результат работы, который можно показать ревьюеру или использовать в следующем шаге.

Верни только JSON:
{{"has_enough_definitions": true/false, "found_count": число, "missing_count": число, "found_definitions": ["список найденных определений"], "reason": "краткое объяснение"}}
"""

GENERATE_TMPL = """Текст части теории не содержит достаточного количества определений терминов. 
Требуется минимум 1 определение в формате: **термин** — определение.

**Название части:** {title}

**Текущий текст части:**
{body}

**Найдено определений:** {found_count} (требуется минимум 1)

**КРИТИЧЕСКИ ВАЖНО - ОБЯЗАТЕЛЬНО:**
- Сохрани весь существующий текст и смысл
- ОБЯЗАТЕЛЬНО добавь МИНИМУМ {missing_count} определение(й) терминов (чтобы было минимум 1)
- ПРЕДПОЧТИТЕЛЬНЫЙ ФОРМАТ (используй его в первую очередь):
  * "**Термин** — это [описание термина]."
- ДОПОЛНИТЕЛЬНЫЕ ФОРМАТЫ (если нужна вариативность):
  * "**Термин** представляет собой [описание]."
  * "**Термин** является [описанием]."
  * "Под **термином** понимают [описание]."
- Определения должны быть полными предложениями с точкой в конце
- Определения должны быть естественно вплетены в текст, не в конце
- Определения должны быть в основном тексте, не в примере
- Каждое определение должно быть явным и понятным
- Термин в определении должен начинаться с заглавной буквы (например: "**Проект** — это...", "**Управление** — это...")
- Постарайся добавить хотя бы 1 ясное определение, но не ломай связность текста ради количества.
- Пиши на «ты», дружелюбно, без директив
- Сохрани таблицы и формулы, если они есть в исходном тексте

**Примеры правильных определений:**
- "**Проект** — это временное предприятие, направленное на создание уникального продукта."
- "**Управление проектом** представляет собой применение знаний, навыков и методов для достижения целей проекта."
- "Под **артефактом** понимают проверяемый результат работы, который можно использовать в следующем шаге проекта."

Верни только переписанный текст части БЕЗ заголовка. Убедись, что присутствует хотя бы 1 явное определение и его легко найти. Каждое определение должно быть полным предложением с точкой.
"""


class DefinitionsAgent:
    """
    Агент для проверки и генерации определений терминов в частях теории.
    
    Проверяет наличие минимум 1 определения в каждой части с форматом:
    **термин** — определение
    
    Генерирует определения, если их недостаточно.
    """

    def __init__(self, llm: LLMClientProtocol):
        """
        Инициализация агента.
        
        Args:
            llm: LLM клиент для генерации
        """
        self.llm = llm

    def check_definitions(self, part: TheoryPart, seed: ProjectSeed) -> tuple[bool, int, list[str]]:
        """
        Проверяет наличие определений в части теории.
        
        Args:
            part: Часть теории для проверки
            seed: Входные данные проекта
        
        Returns:
            (есть ли достаточно определений, количество найденных, список найденных определений)
        """
        has_defs, definitions_found = has_term_definitions(
            part.body,
            seed.language,
            min_definitions=1,
            require_bold=True
        )

        return has_defs, len(definitions_found), definitions_found

    def ensure_definitions(self, part: TheoryPart, seed: ProjectSeed, max_attempts: int = 2) -> TheoryPart:
        """
        Обеспечивает наличие минимум 1 определения в части теории.
        
        Если определений недостаточно, генерирует их через LLM.
        
        Args:
            part: Часть теории для проверки и улучшения
            seed: Входные данные проекта
            max_attempts: Максимальное количество попыток генерации
        
        Returns:
            Часть теории с достаточным количеством определений
        """
        has_defs, found_count, definitions_found = self.check_definitions(part, seed)

        if has_defs:
            safe_print(f"  ✅ Часть '{part.title[:50]}...': найдено {found_count} определений", flush=True)
            return part

        safe_print(f"  ⚠️ Часть '{part.title[:50]}...': найдено только {found_count} определений (требуется минимум 1)", flush=True)

        # Пытаемся сгенерировать определения
        for attempt in range(1, max_attempts + 1):
            safe_print(f"  🔄 Попытка {attempt}/{max_attempts}: генерация определений...", flush=True)

            try:
                missing_count = max(1, 1 - found_count)

                system_prompt = SYSTEM.format(language=seed.language)
                user_prompt = GENERATE_TMPL.format(
                    title=part.title,
                    body=part.body,
                    found_count=found_count,
                    missing_count=missing_count
                )

                generated_body = self.llm.complete(
                    system=system_prompt,
                    user=user_prompt,
                    temperature=0.2
                )

                generated_body = generated_body.strip()

                # Удаляем канонический заголовок раздела, если LLM его добавил.
                generated_body = re.sub(r'^###\s+2\.\d+\.\s*[^\n]+\n+', '', generated_body, flags=re.M)
                generated_body = generated_body.strip()

                # Проверяем, что результат не пустой
                if not generated_body or len(generated_body) < 50:
                    safe_print("  ⚠️ Генерация вернула слишком короткий текст, пробуем еще раз...", flush=True)
                    continue

                # Проверяем, появились ли определения
                new_part = TheoryPart(
                    title=part.title,
                    body=generated_body,
                    example=part.example,
                    bridge_questions=part.bridge_questions,
                    covers_outcomes=part.covers_outcomes,
                    references=part.references.copy() if part.references else []
                )

                has_defs_new, found_count_new, definitions_found_new = self.check_definitions(new_part, seed)

                if has_defs_new:
                    safe_print(f"  ✅ Определения добавлены: найдено {found_count_new} определений", flush=True)
                    return new_part
                else:
                    safe_print(f"  ⚠️ После генерации найдено только {found_count_new} определений, пробуем еще раз...", flush=True)
                    part = new_part  # Используем новый текст для следующей попытки
                    found_count = found_count_new

            except LLMTimeoutError as e:
                # Специальная обработка таймаутов с retry и задержкой
                safe_print(f"  ⚠️ Таймаут при генерации определений (попытка {attempt}/{max_attempts}): {str(e)}", flush=True)
                if attempt < max_attempts:
                    # Экспоненциальная задержка: 2, 4, 8 секунд
                    delay = 2.0 * (2 ** (attempt - 1))
                    safe_print(f"  ⏳ Ожидание {delay:.1f} секунд перед повторной попыткой...", flush=True)
                    time.sleep(delay)
                    continue
                else:
                    safe_print("  ⚠️ Все попытки исчерпаны из-за таймаутов, возвращаем исходную часть", flush=True)
                    break
            except Exception as e:
                import traceback
                safe_print(f"  ⚠️ Ошибка при генерации определений: {str(e)}", flush=True)
                safe_print(f"     Детали: {traceback.format_exc()[:500]}", flush=True)
                # Для других ошибок тоже делаем задержку перед retry
                if attempt < max_attempts:
                    delay = 1.0 * (2 ** (attempt - 1))
                    time.sleep(delay)
                continue

        safe_print(f"  ⚠️ Не удалось добавить явное определение после {max_attempts} попыток", flush=True)
        return part

    def process_parts(self, parts: list[TheoryPart], seed: ProjectSeed) -> list[TheoryPart]:
        """
        Обрабатывает все части теории, обеспечивая наличие определений.
        
        Args:
            parts: Список частей теории
            seed: Входные данные проекта
        
        Returns:
            Список частей теории с достаточным количеством определений
        """
        if not parts:
            return []

        safe_print(f"  📝 Проверка определений в {len(parts)} частях теории...", flush=True)

        processed_parts = []
        for i, part in enumerate(parts, 1):
            safe_print(f"  📝 Проверка части {i}/{len(parts)}: {part.title[:50]}...", flush=True)
            processed_part = self.ensure_definitions(part, seed)
            processed_parts.append(processed_part)

        safe_print("  ✅ Проверка определений завершена", flush=True)

        return processed_parts
