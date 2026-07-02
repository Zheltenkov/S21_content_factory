"""
content_gen/agents/formula_table.py

Агент для анализа необходимости и генерации формул, таблиц и визуализаций.

Анализирует части теории и генерирует:
- Математические формулы (LaTeX)
- Таблицы (Markdown)
- Mermaid диаграммы (flowchart, sequenceDiagram, stateDiagram)

Используется TheoryEnhancementManager для улучшения контента теории.

Архитектура:
1. analyze() - определяет необходимость формул/таблиц/диаграмм
2. _generate() - генерирует элементы через LLM
3. embed_in_text() - встраивает элементы в текст с центрированием
4. _normalize_mermaid() - нормализует синтаксис Mermaid (критично!)
5. _check_mermaid_syntax() - проверяет валидность синтаксиса
"""

import json
import re
from typing import Any

from .base.llm_client import LLMClientProtocol
from ..models.enhancement_models import FormulaItem, FormulaTableResult, GenerationResponse, TableItem, VisualItem
from ..models.schemas import ProjectSeed
from ..utils.logging import safe_print
from ..utils.markdown_renderer import (
    render_formula,
    render_mermaid,
    render_table,
)

SYSTEM = """Ты — эксперт по анализу образовательного контента и генерации математических формул, таблиц и визуализаций.
Твоя задача — определить, нужны ли для темы формулы, таблицы или диаграммы, и сгенерировать их при необходимости.

КРИТИЧЕСКИ ВАЖНО:
- ОБЯЗАТЕЛЬНО используй елочки « » для кавычек вместо прямых кавычек " "

Язык: {language}.
"""

ANALYSIS_TMPL = """Проанализируй тему "{topic}" и текст теории:

{theory_text}

{existing_enhancements_context}

Определи:
1. Нужны ли математические формулы для объяснения темы?
2. Нужны ли таблицы для структурирования информации?
3. Нужны ли визуализации (Mermaid диаграммы) для лучшего понимания?

Верни строго JSON объект:
{{
  "needs_formulas": true/false,
  "needs_tables": true/false,
  "needs_visuals": true/false,
  "reasoning": "Обоснование решения (2-3 предложения)"
}}

Формулы нужны, если:
- Есть математические выражения, метрики, расчеты
- Нужно показать алгоритмическую сложность
- Есть статистические или вероятностные концепции
- Есть параметры, коэффициенты, зависимости между величинами
ВАЖНО: Формулы должны быть строго релевантны и давать ясную пользу. Если нет явной пользы — формулы не добавляй.

Таблицы нужны, если:
- Нужно сравнить СЛОЖНЫЕ концепции/подходы (не простое перечисление!)
- Есть структурированные данные для представления СЛОЖНЫХ взаимосвязей
- Нужна классификация или категоризация СЛОЖНЫХ понятий
- НЕ используй таблицы для простого перечисления элементов - это можно сделать в тексте

Визуализации нужны, если:
- Нужно показать СЛОЖНЫЙ процесс, алгоритм или workflow (flowchart) - не простой линейный список!
- Есть СЛОЖНАЯ иерархия или структура данных (graph) - не простой список элементов!
- Нужна диаграмма последовательности взаимодействий между участниками (sequenceDiagram) - для СЛОЖНЫХ взаимодействий
- Нужна диаграмма состояний (stateDiagram) - для СЛОЖНЫХ переходов состояний
- НЕ используй диаграммы для простых списков или перечислений - это можно сделать в тексте

ВАЖНО - ВАРЬИРОВАНИЕ И ПРЕДОТВРАЩЕНИЕ ДУБЛИРОВАНИЯ:
Варьирование применяется ТОЛЬКО к таблицам и диаграммам:
- Если в предыдущих частях уже использовались таблицы, можно предпочесть диаграммы (и наоборот)
- Не добавляй визуальный элемент, если он не дает понятной пользы

КРИТИЧЕСКИ ВАЖНО - НЕ ДУБЛИРУЙ ИНФОРМАЦИЮ:
- НЕ генерируй и таблицу, и диаграмму для ОДНОЙ И ТОЙ ЖЕ концепции/процесса/структуры
- Если нужно показать этапы процесса - выбери ЛИБО таблицу (для сравнения), ЛИБО диаграмму (для процесса), НЕ ОБА
- Если нужно показать роли/классификацию - выбери ЛИБО таблицу (для сравнения), ЛИБО диаграмму (для структуры), НЕ ОБА
- Таблица и диаграмма должны показывать РАЗНУЮ информацию, не дублировать друг друга

ЯВНЫЙ ЗАПРОС ТАБЛИЦЫ (приоритет над диаграммой):
- Если в тексте теории явно сказано «таблица сравнивает», «сравнительная таблица», «таблица сравнения» — выбирай ТОЛЬКО таблицу: needs_tables=true, needs_visuals=false. Диаграмму не предлагай.
"""

GENERATION_TMPL = """Для темы "{topic}" сгенерируй формулы, таблицы или визуализации.

Текст теории:
{theory_text}

Навыки: {skills}

{existing_context}

КРИТИЧЕСКИ ВАЖНО - ГЕНЕРИРУЙ ТОЛЬКО ДЛЯ СЛОЖНЫХ КОНЦЕПЦИЙ:
- НЕ генерируй таблицы для простого перечисления элементов (это можно сделать в тексте)
- НЕ генерируй диаграммы для простых списков или линейных процессов (это можно сделать в тексте)
- Генерируй таблицы ТОЛЬКО для сравнения СЛОЖНЫХ концепций, классификации, структурирования сложной информации
- Генерируй диаграммы ТОЛЬКО для визуализации СЛОЖНЫХ процессов, алгоритмов, workflow, иерархий, взаимодействий
- Формулы генерируй всегда, если они нужны по теме (метрики, расчеты, параметры)

Сгенерируй:

{generation_instructions}

Верни строго JSON объект:
{{
  "formulas": [
    {{
      "label": "Название формулы",
      "latex": "E = mc^2",
      "parameters": [
        {{"symbol": "E", "description": "энергия"}},
        {{"symbol": "m", "description": "масса"}},
        {{"symbol": "c", "description": "скорость света"}}
      ]
    }}
  ],
  "tables": [
    {{
      "label": "Название таблицы",
      "md_table": "| Колонка 1 | Колонка 2 |\\n|-----------|-----------|\\n| Значение 1 | Значение 2 |",
      "description": "Описание таблицы"
    }}
  ],
  "visuals": [
    {{
      "label": "Последовательность взаимодействий",
      "mermaid": "sequenceDiagram\\n    participant A as Клиент\\n    participant B as Сервис\\n    A->>B: Запрос\\n    B-->>A: Ответ",
      "description": "Диаграмма последовательности"
    }}
  ]
}}

ВАЖНО:

1. Все выводимые данные должны быть строго в формате JSON.
2. Mermaid-диаграммы должны быть записаны ТОЛЬКО строкой внутри поля "mermaid".
3. НЕЛЬЗЯ выводить Mermaid:
   - как отдельный markdown-блок
   - с ```mermaid
   - вне JSON-структуры
   - с тройными кавычками
4. Mermaid должен быть единой строкой с экранированными переносами строк (\\n):
   "mermaid": "sequenceDiagram\\n    participant A as Участник A\\n    participant B as Участник B\\n    A->>B: Сообщение\\n    B-->>A: Ответ"
5. Не добавляй в Mermaid визуальные директивы: %%{{init...}}%%, classDef, class, style, linkStyle, fill, stroke, color, background.
   Оформление задаёт приложение. Твоя задача — только структура, подписи и связи.
6. Используем только следующие допустимые типы Mermaid:
   - flowchart TD, flowchart LR - для процессов и workflow
   - graph TD, graph LR - для графов и иерархий
   - sequenceDiagram - для диаграмм последовательности взаимодействий между участниками
   - stateDiagram - для диаграмм состояний
7. Формулы в LaTeX (только содержимое, без $$)
8. Таблицы в Markdown формате (полный блок)
9. НЕ используй старый синтаксис "graph" без типа направления
10. Все должно быть релевантно теме
11. НЕ дублируй формулы/таблицы, которые уже есть в других частях (см. выше)
12. Весь ответ — строго один JSON-объект без текста до/после него.

Пример корректного вывода:

{{
  "visuals": [
    {{
      "label": "Последовательность взаимодействий",
      "mermaid": "sequenceDiagram\\n    participant A as Клиент\\n    participant B as Сервис\\n    A->>B: Запрос\\n    B-->>A: Ответ",
      "description": "Диаграмма последовательности"
    }}
  ]
}}
"""


class FormulaTableAgent:
    """Агент для анализа и генерации формул, таблиц и визуализаций."""

    def __init__(self, llm: LLMClientProtocol):
        self.llm = llm

    def _safe_json_extract(self, text: str) -> dict[str, Any] | None:
        """
        Надежное извлечение JSON из текста с множественными попытками.
        
        Args:
            text: Текст, содержащий JSON
        
        Returns:
            Распарсенный JSON словарь или None
        """
        if not text or not text.strip():
            return None

        text_clean = text.strip()

        # Попытка 1: Прямой парсинг
        try:
            return json.loads(text_clean)
        except json.JSONDecodeError:
            pass

        # Попытка 2: Извлечение JSON из текста (поиск первой { и последней })
        json_start = text_clean.find("{")
        json_end = text_clean.rfind("}")

        if json_start != -1 and json_end > json_start:
            json_str = text_clean[json_start:json_end + 1]
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

        # Попытка 3: Поиск JSON между markdown-блоками кода
        code_block_pattern = r'```(?:json)?\s*(\{.*?\})\s*```'
        matches = re.findall(code_block_pattern, text_clean, re.DOTALL | re.IGNORECASE)
        for match in matches:
            try:
                return json.loads(match.strip())
            except json.JSONDecodeError:
                continue

        # Попытка 4: Поиск JSON в многострочном формате (удаление комментариев)
        lines = text_clean.split('\n')
        json_lines = []
        in_json = False
        for line in lines:
            if '{' in line:
                in_json = True
            if in_json:
                # Пропускаем комментарии
                if not line.strip().startswith('//') and not line.strip().startswith('#'):
                    json_lines.append(line)
            if '}' in line and in_json:
                break

        if json_lines:
            json_str = '\n'.join(json_lines)
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

        # Попытка 5: Исправление распространенных ошибок
        # Удаляем markdown-блоки из строковых значений
        text_fixed = re.sub(r'```mermaid\s*([^`]+)```', r'\1', text_clean, flags=re.DOTALL | re.IGNORECASE)
        text_fixed = re.sub(r'```\s*([^`]+)```', r'\1', text_fixed, flags=re.DOTALL | re.IGNORECASE)

        json_start = text_fixed.find("{")
        json_end = text_fixed.rfind("}")

        if json_start != -1 and json_end > json_start:
            json_str = text_fixed[json_start:json_end + 1]
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

        return None

    def _clean_and_validate_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Очищает и валидирует данные перед передачей в Pydantic.
        Удаляет некорректные элементы из списков.
        
        Args:
            data: Словарь с данными из JSON
            
        Returns:
            Очищенный словарь
        """
        if not isinstance(data, dict):
            safe_print(f"  ⚠️ Данные не являются словарем: {type(data)}", flush=True)
            return {"formulas": [], "tables": [], "visuals": []}

        cleaned = {
            "formulas": [],
            "tables": [],
            "visuals": []
        }

        # Очистка формул
        if "formulas" in data and isinstance(data["formulas"], list):
            for idx, item in enumerate(data["formulas"]):
                if isinstance(item, dict):
                    # Проверяем обязательные поля
                    if "label" not in item or "latex" not in item:
                        safe_print(f"  ⚠️ Пропущена формула {idx+1}: отсутствуют обязательные поля", flush=True)
                        continue

                    # Проверяем, что parameters - это список словарей
                    if "parameters" in item:
                        if isinstance(item["parameters"], list):
                            cleaned_params = []
                            for param_idx, param in enumerate(item["parameters"]):
                                if isinstance(param, dict):
                                    # Проверяем, что словарь содержит нужные ключи
                                    if "symbol" in param or "description" in param:
                                        cleaned_params.append(param)
                                    else:
                                        safe_print(f"  ⚠️ Пропущен параметр {param_idx+1} формулы {idx+1}: некорректная структура", flush=True)
                                elif isinstance(param, (str, int, float)):
                                    # Если параметр - примитив, пропускаем
                                    safe_print(f"  ⚠️ Пропущен параметр {param_idx+1} формулы {idx+1}: примитивный тип", flush=True)
                                    continue
                            item["parameters"] = cleaned_params
                        else:
                            # Если parameters не список, создаем пустой список
                            item["parameters"] = []

                    cleaned["formulas"].append(item)
                elif isinstance(item, str):
                    # Пропускаем строки
                    safe_print(f"  ⚠️ Пропущена формула-строка {idx+1}: {item[:50]}...", flush=True)
                    continue
                else:
                    safe_print(f"  ⚠️ Пропущена формула {idx+1}: неожиданный тип {type(item)}", flush=True)
                    continue

        # Очистка таблиц
        if "tables" in data and isinstance(data["tables"], list):
            for idx, item in enumerate(data["tables"]):
                if isinstance(item, dict):
                    # Проверяем обязательные поля
                    if "label" not in item or "md_table" not in item:
                        safe_print(f"  ⚠️ Пропущена таблица {idx+1}: отсутствуют обязательные поля", flush=True)
                        continue
                    cleaned["tables"].append(item)
                elif isinstance(item, str):
                    safe_print(f"  ⚠️ Пропущена таблица-строка {idx+1}: {item[:50]}...", flush=True)
                    continue
                else:
                    safe_print(f"  ⚠️ Пропущена таблица {idx+1}: неожиданный тип {type(item)}", flush=True)
                    continue

        # Очистка визуализаций
        if "visuals" in data and isinstance(data["visuals"], list):
            for idx, item in enumerate(data["visuals"]):
                if isinstance(item, dict):
                    # Проверяем обязательные поля
                    if "label" not in item or "mermaid" not in item:
                        safe_print(f"  ⚠️ Пропущена визуализация {idx+1}: отсутствуют обязательные поля", flush=True)
                        continue
                    cleaned["visuals"].append(item)
                elif isinstance(item, str):
                    safe_print(f"  ⚠️ Пропущена визуализация-строка {idx+1}: {item[:50]}...", flush=True)
                    continue
                else:
                    safe_print(f"  ⚠️ Пропущена визуализация {idx+1}: неожиданный тип {type(item)}", flush=True)
                    continue

        return cleaned

    def analyze(
        self,
        topic: str,
        theory_text: str,
        seed: ProjectSeed,
        existing_formulas: list[FormulaItem] = None,
        existing_tables: list[TableItem] = None,
        existing_enhancements: dict = None
    ) -> FormulaTableResult:
        """
        Анализирует, нужны ли формулы, таблицы или визуализации.
        
        Args:
            topic: Тема (название части теории)
            theory_text: Текст части теории
            seed: Входные данные проекта
            existing_formulas: Уже сгенерированные формулы (для дедупликации)
            existing_tables: Уже сгенерированные таблицы (для дедупликации)
            existing_enhancements: Словарь с информацией о том, что уже использовано в предыдущих частях
                Формат: {"tables": 2, "diagrams": 1, "formulas": 0}
        
        Returns:
            FormulaTableResult с решением и сгенерированными элементами
        """
        # Уважение настроек методолога: если всё отключено, ничего не генерируем
        if not (seed.include_formulas or seed.include_tables or seed.include_diagrams):
            return FormulaTableResult(
                needs_formulas=False,
                needs_tables=False,
                needs_visuals=False,
                reasoning="Методолог отключил формулы/таблицы/диаграммы"
            )
        # Формируем контекст о существующих улучшениях
        existing_context = ""
        if existing_enhancements:
            used_types = []
            if existing_enhancements.get("tables", 0) > 0:
                used_types.append(f"таблицы ({existing_enhancements['tables']} раз)")
            if existing_enhancements.get("diagrams", 0) > 0:
                used_types.append(f"диаграммы ({existing_enhancements['diagrams']} раз)")
            if existing_enhancements.get("formulas", 0) > 0:
                used_types.append(f"формулы ({existing_enhancements['formulas']} раз)")

            if used_types:
                existing_context = f"""
**Контекст варьирования:**
В предыдущих частях теории уже использовались: {', '.join(used_types)}.

ВАЖНО - ПРАВИЛА ВАРЬИРОВАНИЯ:
1. Формулы генерируются НЕЗАВИСИМО - если они нужны по теме, всегда включай needs_formulas = true
2. Варьирование применяется ТОЛЬКО к таблицам и диаграммам:
   - Если уже были таблицы, ПРЕДПОЧТИ диаграммы (needs_visuals = true, needs_tables = false)
   - Если уже были диаграммы, ПРЕДПОЧТИ таблицы (needs_tables = true, needs_visuals = false)
   - Формулы НЕ участвуют в варьировании

Например: если уже были таблицы, лучше использовать диаграммы в этой части, но формулы добавляй всегда, если они нужны.
"""
            else:
                existing_context = "\n**Контекст варьирования:** Это первая часть теории. Можно использовать любой подходящий тип визуализации.\n"
        else:
            existing_context = "\n**Контекст варьирования:** Это первая часть теории. Можно использовать любой подходящий тип визуализации.\n"

        system_prompt = SYSTEM.format(language=seed.language)
        user_prompt = ANALYSIS_TMPL.format(
            topic=topic,
            theory_text=theory_text[:2000],  # Ограничиваем длину
            existing_enhancements_context=existing_context
        )

        try:
            safe_print(f"  📊 Анализ необходимости формул/таблиц для темы: {topic[:50]}...", flush=True)

            response = self.llm.complete(
                system=system_prompt,
                user=user_prompt,
                response_format="json_object",
                temperature=0.1
            )

            if not response or not response.strip():
                safe_print("  ⚠️ LLM вернул пустой ответ", flush=True)
                return FormulaTableResult(
                    needs_formulas=False,
                    needs_tables=False,
                    needs_visuals=False,
                    reasoning="Не удалось проанализировать"
                )

            # Надежное извлечение JSON
            data = self._safe_json_extract(response)

            if data is None:
                safe_print("  ⚠️ В ответе LLM не найден JSON объект", flush=True)
                return FormulaTableResult(
                    needs_formulas=False,
                    needs_tables=False,
                    needs_visuals=False,
                    reasoning="Ошибка парсинга JSON"
                )

            needs_formulas = data.get("needs_formulas", False)
            needs_tables = data.get("needs_tables", False)
            needs_visuals = data.get("needs_visuals", False)
            reasoning = data.get("reasoning", "")

            # При явном запросе «таблица сравнивает» / «сравнительная таблица» — только таблица, не диаграмма
            _theory_lower = (theory_text or "").lower()
            if any(
                phrase in _theory_lower
                for phrase in (
                    "таблица сравнивает",
                    "сравнительная таблица",
                    "таблица сравнения",
                )
            ):
                needs_visuals = False
                needs_tables = True
                safe_print("  [FORMULA_TABLE] В тексте запрошена таблица сравнения — выбран режим таблицы, диаграмма отключена", flush=True)

            # Учитываем явные настройки методолога
            if not seed.include_formulas:
                needs_formulas = False
            if not seed.include_tables:
                needs_tables = False
            if not seed.include_diagrams:
                needs_visuals = False

            # Если что-то нужно, генерируем
            formulas = []
            tables = []
            visuals = []

            if needs_formulas or needs_tables or needs_visuals:
                generation_result = self._generate(
                    topic=topic,
                    theory_text=theory_text,
                    skills=seed.skills,
                    seed=seed,
                    needs_formulas=needs_formulas,
                    needs_tables=needs_tables,
                    needs_visuals=needs_visuals,
                    existing_formulas=existing_formulas or [],
                    existing_tables=existing_tables or []
                )
                formulas = generation_result.get("formulas", [])
                tables = generation_result.get("tables", [])
                visuals = generation_result.get("visuals", [])

            safe_print(f"  ✅ Анализ завершен: формулы={needs_formulas}, таблицы={needs_tables}, визуализации={needs_visuals}", flush=True)

            return FormulaTableResult(
                needs_formulas=needs_formulas,
                needs_tables=needs_tables,
                needs_visuals=needs_visuals,
                formulas=[FormulaItem(**f) for f in formulas],
                tables=[TableItem(**t) for t in tables],
                visuals=[VisualItem(**v) for v in visuals],
                reasoning=reasoning
            )

        except json.JSONDecodeError as e:
            safe_print(f"  ⚠️ Ошибка парсинга JSON: {str(e)}", flush=True)
            return FormulaTableResult(
                needs_formulas=False,
                needs_tables=False,
                needs_visuals=False,
                reasoning="Ошибка парсинга JSON"
            )
        except Exception as e:
            safe_print(f"  ⚠️ Ошибка анализа: {str(e)}", flush=True)
            return FormulaTableResult(
                needs_formulas=False,
                needs_tables=False,
                needs_visuals=False,
                reasoning=f"Ошибка: {str(e)}"
            )

    def _generate(
        self,
        topic: str,
        theory_text: str,
        skills: list[str],
        seed: ProjectSeed,
        needs_formulas: bool,
        needs_tables: bool,
        needs_visuals: bool,
        existing_formulas: list[FormulaItem] = None,
        existing_tables: list[TableItem] = None
    ) -> dict:
        """Генерирует формулы, таблицы или визуализации."""
        instructions = []
        if needs_formulas:
            instructions.append("- 1-3 математические формулы в LaTeX с определениями параметров")
        if needs_tables:
            instructions.append("- максимум 1 таблица в Markdown формате для структурирования информации (в одной части допускается только одна таблица)")
        if needs_visuals:
            instructions.append("- максимум 1 Mermaid диаграмма (flowchart TD/LR, graph TD/LR, sequenceDiagram, stateDiagram) (в одной части допускается только одна диаграмма)")

        # Добавляем предупреждение о дублировании
        if needs_tables and needs_visuals:
            instructions.append("\nКРИТИЧЕСКИ ВАЖНО: Если генерируешь и таблицу, и диаграмму - убедись, что они показывают РАЗНУЮ информацию. НЕ дублируй одну и ту же концепцию/процесс/структуру в таблице и диаграмме одновременно!")

        generation_instructions = "\n".join(instructions)

        # Формируем контекст уже существующих формул/таблиц для избежания дубликатов
        existing_context = ""
        if existing_formulas or existing_tables:
            existing_context = "\nВНИМАНИЕ: В других частях теории уже есть следующие элементы. НЕ ДУБЛИРУЙ их:\n\n"
            if existing_formulas:
                existing_context += "Уже существующие формулы:\n"
                for f in existing_formulas[:5]:  # Показываем максимум 5
                    existing_context += f"- {f.label}: {f.latex}\n"
            if existing_tables:
                existing_context += "\nУже существующие таблицы:\n"
                for t in existing_tables[:5]:  # Показываем максимум 5
                    existing_context += f"- {t.label}\n"
            existing_context += "\nСгенерируй ТОЛЬКО новые, уникальные элементы, которые не дублируют существующие.\n"

        system_prompt = SYSTEM.format(language=seed.language)
        user_prompt = GENERATION_TMPL.format(
            topic=topic,
            theory_text=theory_text[:2000],
            skills=", ".join(skills) if skills else "общие навыки",
            existing_context=existing_context,
            generation_instructions=generation_instructions
        )

        try:
            response = self.llm.complete(
                system=system_prompt,
                user=user_prompt,
                response_format="json_object",
                temperature=0.2
            )

            if not response or not response.strip():
                safe_print("  ⚠️ LLM вернул пустой ответ при генерации", flush=True)
                return {"formulas": [], "tables": [], "visuals": []}

            # Надежное извлечение JSON
            data = self._safe_json_extract(response)

            if data is None:
                safe_print("  ⚠️ Не удалось извлечь JSON из ответа LLM", flush=True)
                return {"formulas": [], "tables": [], "visuals": []}

            # Очистка и валидация данных перед передачей в Pydantic
            data = self._clean_and_validate_data(data)

            # Валидация через Pydantic
            try:
                validated = GenerationResponse(**data)

                # Конвертируем обратно в dict для совместимости
                result = {
                    "formulas": [f.dict() for f in validated.formulas],
                    "tables": [t.dict() for t in validated.tables],
                    "visuals": [v.dict() for v in validated.visuals]
                }

                safe_print(f"  ✅ Генерация завершена: {len(result['formulas'])} формул, {len(result['tables'])} таблиц, {len(result['visuals'])} визуализаций", flush=True)
                return result

            except Exception as validation_error:
                safe_print(f"  ⚠️ Ошибка валидации Pydantic: {str(validation_error)}", flush=True)
                safe_print(f"  🔍 Данные после очистки: formulas={len(data.get('formulas', []))}, tables={len(data.get('tables', []))}, visuals={len(data.get('visuals', []))}", flush=True)

                # Дополнительная очистка перед fallback
                cleaned_data = self._clean_and_validate_data(data)

                # Фильтруем только валидные словари
                result = {
                    "formulas": [f for f in cleaned_data.get("formulas", []) if isinstance(f, dict)],
                    "tables": [t for t in cleaned_data.get("tables", []) if isinstance(t, dict)],
                    "visuals": [v for v in cleaned_data.get("visuals", []) if isinstance(v, dict)]
                }

                safe_print(f"  ✅ Fallback: {len(result['formulas'])} формул, {len(result['tables'])} таблиц, {len(result['visuals'])} визуализаций", flush=True)
                return result

        except Exception as e:
            safe_print(f"  ⚠️ Ошибка генерации: {str(e)}", flush=True)
            return {"formulas": [], "tables": [], "visuals": []}

    def embed_in_text(
        self,
        text: str,
        formulas: list[FormulaItem],
        tables: list[TableItem],
        visuals: list[VisualItem]
    ) -> str:
        """
        Встраивает формулы, таблицы и визуализации в текст с центрированием.
        В рамках одной части допускается одна таблица или одна диаграмма.
        
        Args:
            text: Исходный текст
            formulas: Список формул
            tables: Список таблиц
            visuals: Список визуализаций
        
        Returns:
            Текст с встроенными элементами
        """
        blocks = []

        # FORMULAS (всегда все)
        for formula in formulas:
            formula_block = render_formula(
                label=formula.label,
                latex=formula.latex,
                parameters=formula.parameters,
                description=None
            )
            blocks.append(formula_block)

        # Ограничение: в одной части - максимум 1 таблица и максимум 1 диаграмма
        # Они могут быть вместе, если нужно по контенту
        if len(tables) > 1:
            # Если таблиц больше одной - оставляем только первую
            tables = tables[:1]
        if len(visuals) > 1:
            # Если диаграмм больше одной - оставляем только первую
            visuals = visuals[:1]

        # TABLES (максимум одна)
        for table in tables:
            table_block = render_table(
                label=table.label,
                md_table=table.md_table,
                description=table.description
            )
            blocks.append(table_block)

        # VISUALS (Mermaid) (максимум одна)
        for visual in visuals:
            safe_print(f"     🔍 Обработка визуализации: '{visual.label or 'без названия'}'", flush=True)
            # Нормализуем Mermaid код
            normalized_mermaid = self._normalize_mermaid(visual.mermaid)

            if not normalized_mermaid or not normalized_mermaid.strip():
                safe_print("     ⚠️ Mermaid код пуст после нормализации", flush=True)
                continue

            # Проверяем валидность синтаксиса; при неуспехе не вставляем сломанный блок, подставляем заглушку
            if not self._check_mermaid_syntax(normalized_mermaid):
                safe_print(f"     ⚠️ Mermaid код не прошел проверку синтаксиса. Первые 100 символов: {normalized_mermaid[:100]}", flush=True)
                blocks.append("\n\n*(Диаграмма не была сгенерирована из-за ошибки формата.)*\n\n")
                continue

            safe_print("     ✅ Mermaid код валиден, встраиваем диаграмму", flush=True)
            # Используем render_mermaid для центрирования (label будет снизу курсивом)
            visual_block = render_mermaid(
                label=None,  # Label не нужен сверху, description будет снизу
                code=normalized_mermaid,
                description=visual.label or visual.description  # Подпись снизу курсивом
            )
            blocks.append(visual_block)

        # Вставляем блоки перед "Пример:" или "Вопросы к практике", или в конец
        if blocks:
            blocks_text = "\n".join(blocks)

            if "**Пример:**" in text:
                text = text.replace("**Пример:**", f"{blocks_text}\n\n**Пример:**", 1)
            elif "**Вопросы к практике:**" in text:
                text = text.replace("**Вопросы к практике:**", f"{blocks_text}**Вопросы к практике:**", 1)
            else:
                text += "\n\n" + blocks_text

        return text

    def _normalize_mermaid(self, mermaid_code: str) -> str:
        """
        Нормализует синтаксис Mermaid диаграммы.
        
        КРИТИЧНО: Этот метод выполняет полную нормализацию Mermaid кода:
        - Обрабатывает экранированные переносы строк (\\n, \n)
        - Удаляет markdown-блоки (```mermaid)
        - Удаляет тройные кавычки (двойные и одинарные)
        - Исправляет переносы строк внутри меток узлов
        - Очищает пробелы в начале/конце строк
        - Исправляет отсутствие направления (graph -> flowchart TD)
        - Исправляет неправильные стрелки (-> -> -->)
        - Добавляет пробелы вокруг стрелок
        
        Args:
            mermaid_code: Исходный код Mermaid (может содержать ошибки)
        
        Returns:
            Нормализованный код Mermaid, готовый к использованию
        """
        if not mermaid_code or not mermaid_code.strip():
            return mermaid_code

        code = mermaid_code

        # ШАГ 0: Подписи на стрелках --|"текст"| и -->|"текст"| — кириллица/кавычки ломают парсер (ожидается EDGE_TEXT, не STR)
        def _sanitize_edge_label(match: re.Match) -> str:
            prefix = match.group(1)   # --| или -->|
            content = match.group(2)  # текст внутри кавычек
            suffix = match.group(3)   # |
            if content and not content.isascii():
                return prefix + "label" + suffix
            return match.group(0)
        code = re.sub(r'(-->\|)"([^"]*)"(\|)', _sanitize_edge_label, code)
        code = re.sub(r'(--\|)"([^"]*)"(\|)', _sanitize_edge_label, code)

        # ШАГ 1: Обрабатываем экранированные переносы строк
        code = code.replace("\\\\n", "\n")  # \\n -> \n
        code = code.replace("\\n", "\n")     # \n -> реальный перенос

        # ШАГ 2: Удаляем markdown-блоки полностью
        code = re.sub(r'```mermaid\s*', '', code, flags=re.MULTILINE | re.IGNORECASE)
        code = re.sub(r'```\s*', '', code, flags=re.MULTILINE)

        # ШАГ 3: Удаляем только тройные кавычки (Python строки), но сохраняем обычные кавычки
        code = re.sub(r'"""', '', code)
        # Используем chr() для избежания проблем с парсингом
        triple_single_quotes = chr(39) + chr(39) + chr(39)
        code = code.replace(triple_single_quotes, "")

        # ШАГ 4: Исправляем переносы строк внутри меток узлов (КРИТИЧНО!)
        # В Mermaid метки узлов должны быть на одной строке
        if not code.startswith("sequenceDiagram") and not code.startswith("stateDiagram"):
            # Сначала исправляем узлы без ID: [текст] -> A[текст]
            # Находим все узлы вида [текст] или (текст) без ID перед ними
            # Используем построчную обработку вместо look-behind с переменной шириной
            node_counter = 0
            def add_node_id_for_square(match):
                """Добавляет ID для узла в квадратных скобках."""
                nonlocal node_counter
                prefix = match.group(1)  # Префикс (начало строки, пробел или стрелка)
                bracket = match.group(2)  # [
                label_content = match.group(3)  # содержимое
                node_counter += 1
                node_id = f"N{node_counter}"
                return f"{prefix}{node_id}{bracket}{label_content}]"

            def add_node_id_for_round(match):
                """Добавляет ID для узла в круглых скобках."""
                nonlocal node_counter
                prefix = match.group(1)  # Префикс (начало строки, пробел или стрелка)
                bracket = match.group(2)  # (
                label_content = match.group(3)  # содержимое
                node_counter += 1
                node_id = f"N{node_counter}"
                return f"{prefix}{node_id}{bracket}{label_content})"

            # Обрабатываем построчно, чтобы избежать проблем с look-behind переменной ширины
            lines = code.split('\n')
            fixed_lines = []
            for line in lines:
                # Ищем узлы без ID: в начале строки, после пробела или после стрелок
                # Захватываем префикс и узел, затем заменяем
                line = re.sub(r'(^|\s|-->|--|->)\s*(\[)([^\]]+)(\])', add_node_id_for_square, line)
                line = re.sub(r'(^|\s|-->|--|->)\s*(\()([^)]+)(\))', add_node_id_for_round, line)
                fixed_lines.append(line)
            code = '\n'.join(fixed_lines)

            def fix_label_newlines(match):
                node_id = match.group(1)
                bracket_type = match.group(2)  # [ или (
                label_content = match.group(3)
                # Заменяем переносы строк на пробелы внутри метки
                fixed_content = re.sub(r'\s*\n\s+', ' ', label_content).strip()

                # КРИТИЧНО: Если в метке есть специальные символы Mermaid, оборачиваем в кавычки
                # Специальные символы, которые ломают парсинг: ], [, |, --, ->, -->, <--, <-
                # Также проверяем множественные дефисы (----), которые могут быть интерпретированы как стрелки
                special_chars = [']', '[', '|', '--', '->', '-->', '<--', '<-']
                has_special = any(char in fixed_content for char in special_chars)

                # Также проверяем множественные дефисы (3+ подряд)
                has_multiple_dashes = re.search(r'-{3,}', fixed_content) is not None

                # Также оборачиваем в кавычки, если есть круглые скобки в квадратных скобках
                needs_quotes = has_special or has_multiple_dashes or (bracket_type == '[' and ('(' in fixed_content or ')' in fixed_content))

                if needs_quotes:
                    # Если метка уже в кавычках, не добавляем еще
                    if not (fixed_content.startswith('"') and fixed_content.endswith('"')):
                        # Экранируем внутренние кавычки
                        escaped_content = fixed_content.replace('"', '\\"')
                        fixed_content = f'"{escaped_content}"'

                return f"{node_id}{bracket_type}{fixed_content}{']' if bracket_type == '[' else ')'}"

            # Исправляем метки в квадратных скобках: Node[текст\nтекст]
            code = re.sub(r'(\w+)(\[)([^\]]*(?:\n[^\]]*)*)(\])', fix_label_newlines, code)
            # Исправляем метки в круглых скобках: Node(текст\nтекст)
            code = re.sub(r'(\w+)(\()([^)]*(?:\n[^)]*)*)(\))', fix_label_newlines, code)

            # Исправляем незавершенные строки (обрывающиеся на середине)
            # Находим строки, которые заканчиваются на незавершенный узел или стрелку
            lines = code.split('\n')
            fixed_lines = []
            for i, line in enumerate(lines):
                line = line.strip()
                if not line:
                    fixed_lines.append(line)
                    continue
                # Если строка заканчивается на незавершенный узел (например, "h_2" без закрывающей скобки)
                if re.search(r'[A-Z]\w*\[[^\]]*$', line) or re.search(r'[A-Z]\w*\([^)]*$', line):
                    # Пытаемся найти продолжение на следующей строке
                    if i + 1 < len(lines):
                        next_line = lines[i + 1].strip()
                        # Объединяем строки
                        line = line + ' ' + next_line
                        # Пропускаем следующую строку, так как мы её объединили
                        if i + 1 < len(lines):
                            lines[i + 1] = ""
                fixed_lines.append(line)
            code = '\n'.join(fixed_lines)

        # ШАГ 5: Гарантируем перенос строки между определением узла и следующей стрелкой
        # Исправляем случаи вида `}B --|Нет| ...` или `]B --|> ...` или `)B --> ...`
        if not code.startswith("sequenceDiagram") and not code.startswith("stateDiagram"):
            # Сначала исправляем случаи с --|> и --|
            code = re.sub(
                r'([\}\]\)])\s*([A-Za-z0-9_]+)\s*(--\|>|--\|)',
                r'\1\n\2 \3',
                code,
                flags=re.MULTILINE,
            )

            # Затем исправляем случаи с -- и -->
            code = re.sub(
                r'([\}\]\)])\s*([A-Za-z0-9_]+)\s*(--|-->)',
                r'\1\n\2 \3',
                code,
                flags=re.MULTILINE,
            )

        # ШАГ 4.5: Исправляем неправильно сформированные метки узлов
        # Ищем случаи, когда после закрывающей скобки метки идут символы без пробела
        # Например: Node[маленькие буквы]B --|Менее 12 символов
        # Это означает, что метка не была правильно закрыта или содержит специальные символы
        if not code.startswith("sequenceDiagram") and not code.startswith("stateDiagram"):
            lines = code.split('\n')
            fixed_lines = []
            for line in lines:
                # Ищем паттерн: ID[текст]символы (где после ] идут символы без пробела или стрелки)
                # Это означает, что метка содержит специальные символы или неправильно закрыта
                # Исправляем: оборачиваем весь текст после [ в кавычки до правильного закрытия
                # Ищем паттерны вида: ID[текст]символы (где символы идут сразу после ])
                # Пример: Node[маленькие буквы]B --|Менее 12 символов
                # Это означает, что метка содержит специальные символы или неправильно закрыта
                def fix_broken_label(m):
                    node_id = m.group(1)
                    label_start = m.group(2)  # текст внутри [
                    rest = m.group(3)  # текст после ], который должен быть частью метки
                    # Если rest содержит специальные символы или стрелки, это часть метки
                    # Объединяем все в одну метку в кавычках
                    full_label = f'{label_start}{rest}'
                    # Экранируем кавычки внутри
                    escaped_label = full_label.replace('"', '\\"')
                    return f'{node_id}["{escaped_label}"]'

                # Паттерн: ID[текст]символы (захватываем все до следующего пробела или конца строки)
                # Но также проверяем, есть ли после этого -- или |, что указывает на продолжение метки
                # Сначала обрабатываем случаи с --| или -- после символов
                line = re.sub(
                    r'(\w+)\[([^\]]+)\]([^\s]+?)\s*(--\|?|-->)',
                    lambda m: f'{m.group(1)}["{m.group(2)}{m.group(3)}"] {m.group(4)}',
                    line
                )
                # Затем обрабатываем остальные случаи: ID[текст]символы (без пробела после ])
                line = re.sub(
                    r'(\w+)\[([^\]]+)\]([^\s]+)',
                    fix_broken_label,
                    line
                )
                fixed_lines.append(line)
            code = '\n'.join(fixed_lines)

        # ШАГ 5: Разбиваем на строки и очищаем каждую
        lines = code.split("\n")
        cleaned_lines = []
        for line in lines:
            cleaned_line = line.rstrip()  # Убираем пробелы в конце строки
            # Пропускаем полностью пустые строки в начале
            if not cleaned_line and not cleaned_lines:
                continue
            cleaned_lines.append(cleaned_line)

        # Убираем пустые строки в конце
        while cleaned_lines and not cleaned_lines[-1]:
            cleaned_lines.pop()

        code = "\n".join(cleaned_lines)

        # ШАГ 6: Добавляем светлую продуктовую тему для читаемости при статическом экспорте.
        if "%%{init}" not in code and "%%{" not in code:
            # Определяем тип диаграммы
            first_line = code.strip().split('\n')[0].strip()
            if any(diagram_type in first_line for diagram_type in ['flowchart', 'graph', 'sequenceDiagram', 'stateDiagram']):
                from ..utils.markdown_renderer import _mermaid_theme_json
                theme_json = _mermaid_theme_json()
                init_block = f"%%{{init:{theme_json}}}%%\n"
                code = init_block + code

        # ШАГ 6: Удаляем пустые строки в начале и конце
        code = code.strip()

        if not code:
            return ""

        # ШАГ 7: Исправляем синтаксис направления
        # sequenceDiagram и stateDiagram не требуют направления
        if code.startswith("sequenceDiagram") or code.startswith("stateDiagram"):
            return code

        # Исправляем старый синтаксис graph без направления
        if code.startswith("graph ") and not any(code.startswith(f"graph {d}") for d in ["TD", "LR", "TB", "BT"]):
            code = code.replace("graph ", "flowchart TD ", 1)

        # Проверяем валидность начала
        valid_starts = ["flowchart TD", "flowchart LR", "flowchart TB", "flowchart BT",
                       "graph TD", "graph LR", "graph TB", "graph BT",
                       "sequenceDiagram", "stateDiagram"]

        if not any(code.startswith(vs) for vs in valid_starts):
            # Исправляем flowchart без направления
            if code.startswith("flowchart") and not any(code.startswith(f"flowchart {d}") for d in ["TD", "LR", "TB", "BT"]):
                code = code.replace("flowchart", "flowchart TD", 1)
            # Исправляем graph без направления
            elif code.startswith("graph") and not any(code.startswith(f"graph {d}") for d in ["TD", "LR", "TB", "BT"]):
                code = code.replace("graph", "flowchart TD", 1)

        # ШАГ 8: Исправляем неправильные стрелки для flowchart/graph
        if not code.startswith("sequenceDiagram") and not code.startswith("stateDiagram"):
            lines = code.split("\n")
            fixed_lines = []
            for line in lines:
                # Пропускаем строки с метками узлов (внутри [])
                if '[' in line and ']' in line and '-->' not in line:
                    fixed_lines.append(line)
                # Уже правильные стрелки
                elif '-->' in line or '---' in line or '==>' in line:
                    fixed_lines.append(line)
                else:
                    # Заменяем -> на --> для flowchart
                    fixed_line = re.sub(r'(\w+)\s*->\s*(\w+)', r'\1 --> \2', line)
                    fixed_lines.append(fixed_line)
            code = '\n'.join(fixed_lines)

        # ШАГ 8.5: Нормализуем стрелки в sequenceDiagram (критично для парсинга!)
        if code.startswith("sequenceDiagram"):
            lines = code.split("\n")
            fixed_lines = []
            for line in lines:
                if line.strip():
                    # Нормализуем стрелки: -->>, ->>, -->, -> должны иметь правильный формат
                    # Формат: A -->> B : текст
                    # Убираем лишние пробелы и дефисы внутри стрелок
                    line = re.sub(r'(\w+)\s*-+\s*>\s*>\s*(\w+)', r'\1-->>\2', line)  # -->> без пробелов внутри
                    line = re.sub(r'(\w+)\s*-+\s*>\s*(\w+)', r'\1->>\2', line)  # ->> без пробелов внутри
                    # Нормализуем пробелы вокруг стрелок: A-->>B : текст -> A -->> B : текст
                    line = re.sub(r'(\w+)(-->>)(\w+)', r'\1 \2 \3', line)
                    line = re.sub(r'(\w+)(->>)(\w+)', r'\1 \2 \3', line)
                    line = re.sub(r'(\w+)(-->)(\w+)', r'\1 \2 \3', line)
                    # Убираем лишние дефисы после текста сообщения (могут вызывать ошибки парсинга)
                    line = re.sub(r':\s*([^:\n]+)\s*-+\s*$', r': \1', line)
                    # Убираем лишние пробелы в конце строки
                    line = line.rstrip()
                    fixed_lines.append(line)
                else:
                    fixed_lines.append(line)
            code = "\n".join(fixed_lines)

        # ШАГ 9: Исправляем отсутствие пробелов вокруг стрелок для flowchart/graph
        if not code.startswith("sequenceDiagram") and not code.startswith("stateDiagram"):
            # КРИТИЧНО: Удаляем лишние дефисы после закрывающих скобок меток узлов
            # Проблема: H[Конец] ---------------------1 парсер интерпретирует как синтаксис
            # Решение: удаляем дефисы после ], если они не являются частью стрелки
            lines = code.split("\n")
            fixed_lines = []
            for line in lines:
                # Ищем паттерн: ID[метка] ---------------------символы
                # Это означает, что дефисы после ] не являются частью стрелки
                # Удаляем дефисы между ] и следующим узлом/символом
                line = re.sub(
                    r'(\])\s*-{2,}(?=\s*[A-Za-z0-9_])',  # ] после которого идут 2+ дефиса, за которыми идет ID узла
                    r'\1 ',  # Заменяем на ] и пробел
                    line
                )
                # Также обрабатываем случай, когда дефисы идут до конца строки или до комментария
                line = re.sub(
                    r'(\])\s*-{3,}(?=\s*$|#)',  # ] после которого идут 3+ дефиса до конца строки или комментария
                    r'\1',  # Просто удаляем дефисы
                    line
                )
                fixed_lines.append(line)
            code = '\n'.join(fixed_lines)

            # Теперь нормализуем стрелки
            code = re.sub(r'(\w+)(-->)(\w+)', r'\1 \2 \3', code)
            code = re.sub(r'(\w+)(==>)(\w+)', r'\1 \2 \3', code)

        return code

    def _check_mermaid_syntax(self, mermaid_code: str) -> bool:
        """
        Проверяет, что Mermaid код начинается с валидного синтаксиса.
        
        Поддерживаемые типы диаграмм:
        - flowchart TD, flowchart LR, flowchart TB, flowchart BT
        - graph TD, graph LR, graph TB, graph BT
        - sequenceDiagram
        - stateDiagram
        
        Args:
            mermaid_code: Код Mermaid диаграммы (уже нормализованный)
        
        Returns:
            True если синтаксис валиден, False иначе
        """
        if not mermaid_code or not mermaid_code.strip():
            return False

        code = mermaid_code.strip()

        # Убираем %%{init:...}%% из начала, если есть
        if code.startswith("%%{"):
            # Ищем конец init блока
            end_init = code.find("%%", 2)
            if end_init != -1:
                code = code[end_init + 2:].strip()

        valid_starts = [
            "flowchart TD", "flowchart LR", "flowchart TB", "flowchart BT",
            "graph TD", "graph LR", "graph TB", "graph BT",
            "sequenceDiagram", "stateDiagram"
        ]

        return any(code.startswith(vs) for vs in valid_starts)
