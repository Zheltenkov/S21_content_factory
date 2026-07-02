"""
content_gen/agents/code_example.py

Агент для генерации примеров кода и заданий на программирование.

Используется TheoryEnhancementManager для улучшения частей теории.
Генерирует примеры кода и задания, если навыки включают программирование.
"""

import json
import re

from ..config.thresholds import CODE_EXAMPLE_CONFIG
from .base.llm_client import LLMClientProtocol
from ..models.enhancement_models import CodeExample, CodeGenerationResult, CodeTask
from ..models.schemas import ProjectSeed
from ..utils.logging import safe_print

SYSTEM = """Ты — эксперт по генерации образовательных примеров кода и заданий на программирование.
Твоя задача — создавать качественные, понятные примеры кода и практические задания для студентов.
Язык: {language}.
"""

USER_TMPL = """Ты — эксперт по генерации образовательных примеров кода для учебных проектов Школы 21.

КОНТЕКСТ:
- Тема части теории: "{topic}"
- Навыки проекта: {skills}
- Язык документации: {language}
- Язык программирования: {programming_language}

ТЕКСТ ЧАСТИ ТЕОРИИ (первые 500 символов для контекста):
{context_preview}

ЗАДАЧА:
Сгенерируй примеры кода и задания на программирование, которые:
1. Демонстрируют ключевые концепции из темы
2. Релевантны контексту части теории выше
3. Соответствуют навыкам проекта
4. Используют язык программирования: {programming_language}

ТРЕБОВАНИЯ К ПРИМЕРАМ КОДА (1-3 штуки):
- Короткие, понятные примеры (до 20-30 строк кода)
- Демонстрируют ключевые концепции темы
- С комментариями на языке {language}
- Объяснение (explanation) должно быть КРАТКИМ: максимум 1-3 предложения
- Примеры должны быть рабочими и корректными

ТРЕБОВАНИЯ К ЗАДАНИЯМ НА ПРОГРАММИРОВАНИЕ (2-5 штук, только если тема касается разработки):
- Практические задачи, которые решаются кодом
- Разные уровни сложности (beginner, intermediate, advanced)
- Заготовки кода с TODO комментариями для студента
- Подсказки для студентов (опционально, но желательно)
- Используй язык программирования: {programming_language}

ФОРМАТ ОТВЕТА (строго JSON):
{{
  "examples": [
    {{
      "label": "Краткое название примера (до 50 символов)",
      "code": "код здесь (до 30 строк, рабочий и корректный)",
      "language": "{programming_language}",
      "explanation": "Краткое объяснение (1-3 предложения максимум)"
    }}
  ],
  "tasks": [
    {{
      "title": "Название задания (действие + результат)",
      "difficulty": "beginner|intermediate|advanced",
      "code_stub": "заготовка кода с TODO комментариями",
      "language": "{programming_language}",
      "hint": "Подсказка для студента (опционально)"
    }}
  ]
}}

КРИТИЧЕСКИ ВАЖНО:
- Все примеры кода должны быть рабочими и корректными
- Объяснения (explanation) должны быть КРАТКИМИ (1-3 предложения)
- Примеры должны быть релевантны теме и контексту части теории
- Задания должны быть практичными и полезными для обучения
- Используй ТОЛЬКО язык программирования: {programming_language}
"""


class CodeExampleAgent:
    """Агент для генерации примеров кода и заданий на программирование."""

    def __init__(self, llm: LLMClientProtocol):
        self.llm = llm

    def generate(
        self,
        topic: str,
        skills: list[str],
        seed: ProjectSeed,
        context: str = ""
    ) -> CodeGenerationResult:
        """
        Генерирует примеры кода и задания на программирование.
        
        Args:
            topic: Тема (название части теории или задачи)
            skills: Список навыков
            seed: Входные данные проекта
            context: Дополнительный контекст (текст части теории или задачи)
        
        Returns:
            CodeGenerationResult с примерами и заданиями
        """
        system_prompt = SYSTEM.format(language=seed.language)

        # Определяем язык программирования из навыков
        programming_language = self._detect_language(skills)

        # Используем контекст для улучшения релевантности примеров
        context_preview = context[:500] if context else ""

        user_prompt = USER_TMPL.format(
            topic=topic,
            skills=", ".join(skills) if skills else "общие навыки программирования",
            language=seed.language,
            programming_language=programming_language,
            context_preview=context_preview or "Контекст не предоставлен"
        )

        try:
            safe_print(f"  💻 Генерация примеров кода для темы: {topic[:50]}...", flush=True)

            response = self.llm.complete(
                system=system_prompt,
                user=user_prompt,
                response_format="json_object",
                temperature=0.3
            )

            if not response or not response.strip():
                safe_print("  ⚠️ LLM вернул пустой ответ", flush=True)
                return CodeGenerationResult()

            # Парсим JSON
            response_clean = response.strip()
            json_start = response_clean.find("{")
            json_end = response_clean.rfind("}") + 1

            if json_start == -1 or json_end <= json_start:
                safe_print("  ⚠️ В ответе LLM не найден JSON объект", flush=True)
                return CodeGenerationResult()

            json_str = response_clean[json_start:json_end]
            data = json.loads(json_str)

            # Валидируем и создаем объекты
            examples = []
            for ex in data.get("examples", []):
                try:
                    # Валидация примера кода
                    if CODE_EXAMPLE_CONFIG["enable_code_example_validation"]:
                        ex = self._validate_example(ex)
                    examples.append(CodeExample(**ex))
                except Exception as e:
                    safe_print(f"  ⚠️ Ошибка валидации примера: {str(e)}", flush=True)

            tasks = []
            # Генерируем задания только если включено в config
            if CODE_EXAMPLE_CONFIG["enable_code_tasks_in_practice"]:
                for task in data.get("tasks", []):
                    try:
                        tasks.append(CodeTask(**task))
                    except Exception as e:
                        safe_print(f"  ⚠️ Ошибка валидации задания: {str(e)}", flush=True)

            safe_print(f"  ✅ Сгенерировано примеров: {len(examples)}, заданий: {len(tasks)}", flush=True)

            return CodeGenerationResult(examples=examples, tasks=tasks)

        except json.JSONDecodeError as e:
            safe_print(f"  ⚠️ Ошибка парсинга JSON: {str(e)}", flush=True)
            return CodeGenerationResult()
        except Exception as e:
            safe_print(f"  ⚠️ Ошибка генерации примеров кода: {str(e)}", flush=True)
            return CodeGenerationResult()

    def _detect_language(self, skills: list[str]) -> str:
        """Определяет язык программирования из навыков."""
        skills_lower = [s.lower() for s in skills]

        # Приоритет языков
        if any("javascript" in s or "js" in s or "node" in s for s in skills_lower):
            return "javascript"
        if any("java" in s for s in skills_lower):
            return "java"
        if any("go" in s or "golang" in s for s in skills_lower):
            return "go"
        if any("rust" in s for s in skills_lower):
            return "rust"
        if any("cpp" in s or "c++" in s or "c plus" in s for s in skills_lower):
            return "cpp"
        if any("c " in s and "c++" not in s and "cpp" not in s for s in skills_lower):
            return "c"
        if any("bash" in s or "shell" in s for s in skills_lower):
            return "bash"
        if any("sql" in s for s in skills_lower):
            return "sql"

        # Python по умолчанию
        return "python"

    def _validate_example(self, example_dict: dict) -> dict:
        """
        Валидирует пример кода согласно критерию 6 (≤ 3 предложения в explanation).
        
        Args:
            example_dict: Словарь с данными примера
        
        Returns:
            Валидированный словарь
        """
        if "explanation" in example_dict and example_dict["explanation"]:
            explanation = example_dict["explanation"]
            # Подсчитываем предложения (по точкам, восклицательным и вопросительным знакам)
            sentences = re.split(r'[.!?]+', explanation)
            sentences = [s.strip() for s in sentences if s.strip()]

            if len(sentences) > 3:
                # Обрезаем до 3 предложений
                explanation = '. '.join(sentences[:3])
                if not explanation.endswith(('.', '!', '?')):
                    explanation += '.'
                example_dict["explanation"] = explanation
                safe_print(f"  ⚠️ Объяснение примера обрезано до 3 предложений (было {len(sentences)})", flush=True)

        # Проверяем, что код не пустой
        if "code" in example_dict and not example_dict["code"].strip():
            raise ValueError("Код примера не может быть пустым")

        return example_dict

    def embed_example_in_text(self, text: str, example: CodeExample) -> str:
        """
        Встраивает пример кода в текст согласно критерию 6 (в конце части перед блоком "Пример:").
        
        Args:
            text: Исходный текст
            example: Пример кода
        
        Returns:
            Текст с встроенным примером
        """
        # Формируем блок примера кода
        code_block = f"""**{example.label}**

```{example.language}
{example.code}
```

"""
        if example.explanation:
            code_block += f"{example.explanation}\n\n"

        # Согласно критерию 6, пример должен быть в конце части перед блоком "Пример:"
        # Ищем блок "**Пример:**" и вставляем перед ним
        if "**Пример:**" in text:
            # Вставляем перед блоком "Пример:" (чтобы пример кода был перед текстовым примером)
            text = text.replace("**Пример:**", f"{code_block}**Пример:**", 1)
        else:
            # Если блока "Пример:" нет, добавляем в конец текста
            text += f"\n\n{code_block}"

        return text

