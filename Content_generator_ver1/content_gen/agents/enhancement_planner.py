"""
content_gen/agents/enhancement_planner.py

Агент для глобального планирования улучшений контента.

Строит EnhancementPlan для всего README до обработки частей:
- Анализирует все части (темы + аннотации)
- Определяет цели улучшений из content_type policy и бюджета
- Распределяет элементы по частям с учетом важности (must/nice_to_have/no)
- Учитывает бюджет элементов
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..config.thresholds import (
    DEFAULT_ENHANCEMENT_BUDGET,
    get_enhancement_config_for_content_type,
)
from .base.llm_client import LLMClientProtocol
from ..llm.structured_output import StructuredLLMClient
from ..models.enhancement_plan import (
    EnhancementBudget,
    EnhancementPlan,
    GlobalEnhancementTargets,
    ImportanceLevel,
    PartEnhancementPlan,
)
from ..models.generation_profile import DEFAULT_PROFILE, GenerationProfile, get_profile_by_name
from ..models.schemas import ProjectSeed, TheoryPart
from ..observability import FallbackTraceEvent
from ..utils.logging import safe_print


class EnhancementPlanLLMResponse(BaseModel):
    """Промежуточная модель для парсинга ответа LLM."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    class AnchorHintsData(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

        formula: str = Field(default="")
        table: str = Field(default="")
        diagram: str = Field(default="")

    class PartPlanData(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

        part_index: int = Field(ge=1, le=12)
        topic: str = Field(min_length=1, max_length=160)
        formulas: Literal["must", "nice_to_have", "no"] = "no"
        tables: Literal["must", "nice_to_have", "no"] = "no"
        diagrams: Literal["must", "nice_to_have", "no"] = "no"
        code_examples: Literal["must", "nice_to_have", "no"] = "no"
        reasoning: str = Field(default="", max_length=800)
        anchor_hints: AnchorHintsData | None = None

    per_part: list[PartPlanData] = Field(
        description="План для каждой части. ОБЯЗАТЕЛЬНОЕ поле, должно содержать минимум столько элементов, сколько указано частей теории."
    )
    reasoning: str = Field(
        default="",
        description="Обоснование глобального плана"
    )


SYSTEM = """Ты — эксперт по планированию образовательного контента.
Твоя задача — построить глобальный план улучшений для всего README проекта.

Ты анализируешь все части теории и определяешь:
1. Цели улучшений из content_type policy и бюджета
2. Распределение элементов по частям с учетом важности (must/nice_to_have/no)
3. Якоря для встраивания элементов в текст

Язык: {language}.
"""

PLANNING_TMPL = """Построй глобальный план улучшений для README проекта.

=== МЕТАДАННЫЕ ПРОЕКТА ===
Тематический блок: {thematic_block}
Описание проекта: {project_description}
Навыки: {skills}
Образовательные результаты: {learning_outcomes}
Является ли программистским: {is_programming}
Тип контента: {content_type}

=== ЧАСТИ ТЕОРИИ ===
{parts_info}

=== ТИП КОНТЕНТА И ОГРАНИЧЕНИЯ (КРИТИЧЕСКИ ВАЖНО!) ===
{content_type_constraints}

=== БЮДЖЕТ ЭЛЕМЕНТОВ ===
{budget_info}

=== ЗАДАЧА ===
Построй план улучшений, который:
1. Удовлетворяет content_type policy и бюджету элементов
2. Распределяет элементы по частям с учетом важности
3. Указывает подсказки для якорей вставки

КРИТИЧЕСКИ ВАЖНО: Ты ОБЯЗАН вернуть поле "per_part" с планом для КАЖДОЙ части теории. Поле "per_part" НЕ может быть пустым или отсутствовать! Это должен быть МАССИВ, где для каждой части есть отдельный объект с полем "part_index".

Верни строго JSON объект:
{{
  "per_part": [
    {{
      "part_index": 1,
      "topic": "название части",
      "formulas": "must" | "nice_to_have" | "no",
      "tables": "must" | "nice_to_have" | "no",
      "diagrams": "must" | "nice_to_have" | "no",
      "code_examples": "must" | "nice_to_have" | "no",
      "reasoning": "обоснование для этой части",
      "anchor_hints": {{
        "formula": "после какого фрагмента вставить формулу (например, 'после определения метрики точности')",
        "diagram": "после какого фрагмента вставить диаграмму (например, 'после описания жизненного цикла проекта')",
        "table": "после какого фрагмента вставить таблицу"
      }}
    }},
    {{
      "part_index": 2,
      "topic": "название второй части",
      "formulas": "no",
      "tables": "must",
      "diagrams": "nice_to_have",
      "code_examples": "no",
      "reasoning": "обоснование для второй части",
      "anchor_hints": {{
        "table": "после сравнения подходов"
      }}
    }}
  ],
  "reasoning": "Обоснование глобального плана"
}}

КРИТЕРИИ:
- **formulas: "must"** - если в части есть математические зависимости, метрики, расчеты
- **formulas: "nice_to_have"** - если формулы могут улучшить понимание, но не критичны
- **diagrams: "must"** - для СЛОЖНЫХ концепций (процессы, алгоритмы, workflow, взаимодействия)
- **diagrams: "nice_to_have"** - для концепций средней сложности
- **tables: "must"** - для СЛОЖНЫХ сравнений, классификаций, структурирования
- **tables: "nice_to_have"** - для простых сравнений
- **code_examples: "must"** - только для программистских тем, где код критичен
- **code_examples: "nice_to_have"** - для программистских тем, где код полезен, но не обязателен

ВАЖНО:
- Не назначай "must" только ради общего правила. "must" допустим только когда элемент действительно нужен теме части и разрешён content_type policy.
- Если элемент полезен, но без него теория останется понятной, ставь "nice_to_have".
- Если content_type policy запрещает элемент, ставь "no" для всех частей.
- Распределяй элементы равномерно, избегай концентрации в одной части
- Якоря должны указывать на логические места в тексте (не просто "в конце")
- ОБЯЗАТЕЛЬНО верни план для ВСЕХ частей теории, указанных в "ЧАСТИ ТЕОРИИ"!
- У каждого элемента массива "per_part" должен быть корректный "part_index" (1-based)
- Если в тексте/аннотации части явно сказано «таблица сравнивает», «сравнительная таблица», «таблица сравнения» — для этой части ставь tables: "must" или "nice_to_have", diagrams: "no". Не предлагай диаграмму вместо таблицы.
"""


class EnhancementPlanner:
    """Агент для глобального планирования улучшений контента."""

    def __init__(self, llm: LLMClientProtocol):
        self.llm = llm
        self.structured_client = StructuredLLMClient(llm)

    def _determine_content_type(self, seed: ProjectSeed) -> str:
        """
        Определяет тип контента на основе направления.
        
        Returns:
            'hard_code' | 'low_code' | 'no_code'
        """
        explicit_type = getattr(seed, "project_content_type", None)
        if explicit_type in {"hard_code", "low_code", "no_code"}:
            return explicit_type

        direction = (getattr(seed, 'direction', '') or seed.thematic_block or "").upper()

        # Hard code: Разработчик ПО, C/C++, Java, Python backend
        hard_code_directions = {
            'C', 'CPP', 'C++', 'JAVA', 'GO', 'RUST', 'BACKEND', 'MOBILE',
            'WEB', 'FRONTEND', 'FULLSTACK', 'DEV', 'SWE'
        }

        # Low code: DS, DevOps, QA, Биоинформатика
        low_code_directions = {
            'DS', 'DO', 'QA', 'BIO', 'BIOINF', 'DEVOPS', 'DATA',
            'ML', 'AI', 'TESTING', 'AUTOMATION'
        }

        # No code: Project Manager, UX, Кибербез, BSA
        no_code_directions = {
            'PJM', 'UX', 'CB', 'KB', 'BSA', 'BA', 'PM', 'CYBER',
            'SECURITY', 'PRODUCT', 'DESIGN', 'MANAGEMENT', 'ANALYST'
        }

        if direction in hard_code_directions:
            return 'hard_code'
        elif direction in low_code_directions:
            return 'low_code'
        elif direction in no_code_directions:
            return 'no_code'
        else:
            # По умолчанию - low_code (средний вариант)
            return 'low_code'

    def _is_programming_project(self, seed: ProjectSeed) -> bool:
        """Определяет, является ли проект программистским."""
        # Сначала проверяем content_type
        content_type = self._determine_content_type(seed)
        if content_type == 'hard_code':
            return True
        if content_type == 'no_code':
            return False

        # Для low_code проверяем явный флаг
        if seed.is_programming_project is not None:
            return seed.is_programming_project

        # Затем проверяем навыки и описание
        programming_keywords = [
            "python", "javascript", "java", "c++", "c#", "go", "rust", "kotlin",
            "программирование", "разработка", "код", "алгоритм", "функция", "класс",
            "api", "sdk", "framework", "library", "библиотека", "модуль"
        ]

        skills_text = " ".join(seed.skills).lower()
        desc_text = seed.project_description.lower()

        return any(kw in skills_text or kw in desc_text for kw in programming_keywords)

    def _format_parts_info(self, parts: list[TheoryPart]) -> str:
        """Форматирует информацию о частях для промпта."""
        lines = []
        for i, part in enumerate(parts, 1):
            # Берем первые 200 символов body как аннотацию
            preview = part.body[:200] + "..." if len(part.body) > 200 else part.body
            lines.append(f"Часть {i}. {part.title}")
            lines.append(f"  Аннотация: {preview}")
        return "\n".join(lines)

    def _get_content_type_constraints(self, content_type: str) -> str:
        """Генерирует ограничения на основе типа контента."""
        if content_type == "no_code":
            return """ТИП: ГУМАНИТАРНЫЙ (no_code) — PjM, BSA, UX, Кибербезопасность

СТРОГО ЗАПРЕЩЕНО:
- Любые формулы (даже простые!) — ставь formulas: "no" для ВСЕХ частей
- Любой код (даже псевдокод) — ставь code_examples: "no" для ВСЕХ частей

РАЗРЕШЕНО:
- Таблицы — ставь tables: "must" или "nice_to_have" для сравнений и классификаций
- Простые диаграммы (mermaid flowchart без кода) — ставь diagrams: "nice_to_have"

ГЛОБАЛЬНЫЕ ЦЕЛИ:
- Формулы: 0 (НЕ нужны)
- Диаграммы: 0-1 (опционально, только простые)
- Таблицы: минимум 1 (для сравнений)
- Код: 0 (НЕ нужен)"""

        elif content_type == "low_code":
            return """ТИП: ТЕХНИЧЕСКИЙ С ОГРАНИЧЕНИЯМИ (low_code) — DS, DevOps, QA

ОГРАНИЧЕНИЯ:
- Формулы: максимум 1-2 ПРОСТЫЕ (ставь formulas: "nice_to_have", НЕ "must")
- Код: максимум 1-2 коротких примера (ставь code_examples: "nice_to_have", НЕ "must")

РАЗРЕШЕНО:
- Таблицы — ставь tables: "must" для сравнений
- Диаграммы — ставь diagrams: "nice_to_have" или "must" только для действительно сложного процесса

ГЛОБАЛЬНЫЕ ЦЕЛИ:
- Формулы: 0 (не обязательны)
- Диаграммы: 0-1 (желательно для сложного процесса, но не обязательно для каждого проекта)
- Таблицы: 1 (обязательно)
- Код: 0 (не обязателен)"""

        else:  # hard_code
            return """ТИП: ТЕХНИЧЕСКИЙ (hard_code) — Разработка ПО

РАЗРЕШЕНО ВСЁ:
- Формулы — ставь formulas: "must" там, где нужны расчёты
- Код — ставь code_examples: "must" для программистских тем
- Таблицы и диаграммы — по необходимости

ГЛОБАЛЬНЫЕ ЦЕЛИ:
- Формулы: минимум 1
- Диаграммы: минимум 1
- Код: минимум 2 примера"""

    def create_plan(
        self,
        parts: list[TheoryPart],
        seed: ProjectSeed,
        budget: EnhancementBudget | None = None,
        profile: GenerationProfile | None = None
    ) -> EnhancementPlan:
        """
        Создает глобальный план улучшений для всего README.
        
        Args:
            parts: Список частей теории
            seed: Входные данные проекта
            budget: Бюджет элементов (если None - используется из профиля или DEFAULT_ENHANCEMENT_BUDGET)
            profile: Профиль генерации (если None - определяется автоматически)
        
        Returns:
            EnhancementPlan с планом для каждой части
        """
        safe_print(f"[PLANNER] Начало планирования для {len(parts)} частей", flush=True)

        # Определяем тип контента (hard_code / low_code / no_code)
        content_type = self._determine_content_type(seed)
        safe_print(f"[PLANNER] Тип контента: {content_type}", flush=True)

        is_programming = self._is_programming_project(seed)
        safe_print(f"[PLANNER] Проект программистский: {is_programming}", flush=True)

        # Получаем бюджет и цели на основе типа контента
        content_budget_dict, content_targets_dict = get_enhancement_config_for_content_type(content_type)

        # Определяем профиль, если не указан
        if profile is None:
            # Автоматическое определение профиля на основе типа проекта и навыков
            if is_programming:
                profile = get_profile_by_name("programming")
            else:
                profile = DEFAULT_PROFILE
            safe_print(f"[PLANNER] Используется профиль: {profile.name}", flush=True)

        # Используем бюджет на основе content_type (приоритетнее профиля)
        if budget is None:
            budget = EnhancementBudget(
                formulas=content_budget_dict["formulas"],
                tables=content_budget_dict["tables"],
                diagrams=content_budget_dict["diagrams"],
                code_examples=content_budget_dict["code_examples"]
            )

        # Формируем глобальные цели на основе content_type
        global_targets = GlobalEnhancementTargets(
            formulas=content_targets_dict.get("formulas", 0),
            diagrams=content_targets_dict.get("diagrams", 0),
            tables=content_targets_dict.get("tables", 0),
            code_examples=content_targets_dict.get("code_examples", 0)
        )

        safe_print(f"[PLANNER] Глобальные цели: формулы={global_targets.formulas}, диаграммы={global_targets.diagrams}, таблицы={global_targets.tables}", flush=True)

        # Форматируем входные данные
        parts_info = self._format_parts_info(parts)
        budget_info = json.dumps({
            "formulas": budget.formulas,
            "tables": budget.tables,
            "diagrams": budget.diagrams,
            "code_examples": budget.code_examples
        }, ensure_ascii=False, indent=2)

        # Генерируем ограничения на основе типа контента
        content_type_constraints = self._get_content_type_constraints(content_type)

        # Генерируем план через LLM
        system_prompt = SYSTEM.format(language=seed.language)
        user_prompt = PLANNING_TMPL.format(
            thematic_block=seed.thematic_block,
            project_description=seed.project_description,
            skills=", ".join(seed.skills),
            learning_outcomes=", ".join(seed.learning_outcomes),
            is_programming=is_programming,
            content_type=content_type,
            parts_info=parts_info,
            budget_info=budget_info,
            content_type_constraints=content_type_constraints
        )

        # Получаем план от LLM с structured output
        try:
            llm_response = self.structured_client.complete_structured(
                output_model=EnhancementPlanLLMResponse,
                system=system_prompt,
                user=user_prompt,
            )

            # КРИТИЧНО: Детальное логирование для диагностики
            safe_print(f"[PLANNER] LLM ответ получен: per_part is not None={llm_response.per_part is not None}, len={len(llm_response.per_part) if llm_response.per_part else 0}, reasoning={bool(llm_response.reasoning)}", flush=True)

            # Дополнительное логирование содержимого per_part
            if llm_response.per_part:
                safe_print(
                    f"[PLANNER] per_part содержит {len(llm_response.per_part)} частей: "
                    f"{[item.part_index for item in llm_response.per_part]}",
                    flush=True
                )
                for item in llm_response.per_part[:3]:
                    safe_print(
                        f"[PLANNER]   Часть {item.part_index}: topic={item.topic[:50] if item.topic else 'None'}...",
                        flush=True
                    )
            else:
                safe_print(f"[PLANNER] ⚠️ per_part пустой или None (тип: {type(llm_response.per_part)})", flush=True)
                # Логируем raw JSON для отладки
                try:
                    raw_json = llm_response.model_dump_json(exclude_none=True)
                    safe_print(f"[PLANNER] Raw JSON ответ: {raw_json[:500]}...", flush=True)
                except Exception as e:
                    safe_print(f"[PLANNER] Ошибка при логировании raw JSON: {e}", flush=True)

            # Преобразуем данные в модель
            per_part_plans = {}
            if llm_response.per_part and len(llm_response.per_part) > 0:
                for part_data in llm_response.per_part:
                    try:
                        part_idx = int(part_data.part_index)
                        per_part_plans[part_idx] = PartEnhancementPlan(
                            part_index=part_idx,
                            topic=part_data.topic or f"Часть {part_idx}",
                            formulas=ImportanceLevel(part_data.formulas),
                            tables=ImportanceLevel(part_data.tables),
                            diagrams=ImportanceLevel(part_data.diagrams),
                            code_examples=ImportanceLevel(part_data.code_examples),
                            reasoning=part_data.reasoning or "",
                            anchor_hints=(
                                {
                                    key: value
                                    for key, value in part_data.anchor_hints.model_dump().items()
                                    if value
                                }
                                if part_data.anchor_hints
                                else None
                            )
                        )
                    except (ValueError, KeyError) as e:
                        safe_print(f"[PLANNER] ОШИБКА обработки части {getattr(part_data, 'part_index', 'unknown')}: {e}", flush=True)
                        continue

            # Если per_part пустой или None, используем fallback план
            if not per_part_plans:
                safe_print(f"[PLANNER] ⚠️ План пустой (0 частей), создаем fallback план для {len(parts)} частей", flush=True)
                safe_print(f"[PLANNER] Причина: per_part={llm_response.per_part}, len={len(llm_response.per_part) if llm_response.per_part else 0}", flush=True)
                return self._create_fallback_plan(
                    parts,
                    global_targets,
                    is_programming,
                    budget,
                    reason="structured response contained no per_part plan",
                    fallback_type="empty_enhancement_plan",
                )

            plan = EnhancementPlan(
                global_targets=global_targets,
                budget=budget,
                per_part=per_part_plans,
                is_programming_project=is_programming,
                reasoning=llm_response.reasoning or ""
            )
        except Exception as e:
            # Recovery path for providers that reject structured output.
            safe_print(f"[PLANNER] ОШИБКА structured output, используем fallback: {e}", flush=True)
            response = self.llm.complete(
                system=system_prompt,
                user=user_prompt,
                response_format="json_object"
            )

            # Parse raw JSON from the recovery response.
            try:
                json_start = response.find("{")
                json_end = response.rfind("}") + 1
                if json_start >= 0 and json_end > json_start:
                    json_str = response[json_start:json_end]
                    plan_data = json.loads(json_str)
                else:
                    raise ValueError("JSON не найден в ответе")
            except (json.JSONDecodeError, ValueError) as parse_error:
                safe_print(f"[PLANNER] ОШИБКА парсинга JSON: {parse_error}", flush=True)
                safe_print(f"[PLANNER] Ответ LLM: {response[:500]}", flush=True)
                # Fallback: создаем минимальный план
                return self._create_fallback_plan(
                    parts,
                    global_targets,
                    is_programming,
                    budget,
                    reason=f"json_parse_error: {parse_error}",
                    fallback_type="enhancement_plan_json_parse_error",
                )

            # Преобразуем данные в модель
            per_part_plans = {}
            raw_per_part = plan_data.get("per_part", {})
            if isinstance(raw_per_part, list):
                iterable = raw_per_part
            elif isinstance(raw_per_part, dict):
                iterable = []
                for part_idx_str, part_data in raw_per_part.items():
                    if isinstance(part_data, dict):
                        part_data = {"part_index": int(part_idx_str), **part_data}
                        iterable.append(part_data)
            else:
                iterable = []

            for part_data in iterable:
                try:
                    part_idx = int(part_data.get("part_index"))
                    anchor_hints = part_data.get("anchor_hints")
                    if not isinstance(anchor_hints, dict):
                        anchor_hints = None

                    per_part_plans[part_idx] = PartEnhancementPlan(
                        part_index=part_idx,
                        topic=part_data.get("topic", f"Часть {part_idx}"),
                        formulas=ImportanceLevel(part_data.get("formulas", "no")),
                        tables=ImportanceLevel(part_data.get("tables", "no")),
                        diagrams=ImportanceLevel(part_data.get("diagrams", "no")),
                        code_examples=ImportanceLevel(part_data.get("code_examples", "no")),
                        reasoning=part_data.get("reasoning", ""),
                        anchor_hints=anchor_hints
                    )
                except (ValueError, KeyError, TypeError) as e:
                    safe_print(f"[PLANNER] ОШИБКА обработки части {part_data}: {e}", flush=True)
                    continue

            plan = EnhancementPlan(
                global_targets=global_targets,
                budget=budget,
                per_part=per_part_plans,
                is_programming_project=is_programming,
                reasoning=plan_data.get("reasoning", "")
            )

        safe_print(f"[PLANNER] План создан: {len(per_part_plans)} частей запланировано", flush=True)
        return plan

    def _create_fallback_plan(
        self,
        parts: list[TheoryPart],
        global_targets: GlobalEnhancementTargets,
        is_programming: bool,
        budget: EnhancementBudget | None = None,
        *,
        reason: str = "fallback plan requested",
        fallback_type: str = "enhancement_plan_fallback",
    ) -> EnhancementPlan:
        """Создает минимальный план при ошибке парсинга."""
        safe_print(f"[PLANNER] Создание fallback плана для {len(parts)} частей", flush=True)

        per_part_plans = {}
        num_parts = len(parts)
        target_diagram_indexes = set()
        if global_targets.diagrams > 0 and num_parts:
            target_diagram_indexes.add(max(1, min(num_parts, num_parts // 2 or 1)))

        target_formula_indexes = set()
        if global_targets.formulas > 0 and num_parts:
            target_formula_indexes.add(max(1, min(num_parts, 2)))

        target_table_indexes = set()
        if global_targets.tables > 0 and num_parts:
            target_table_indexes.add(1)

        # Распределяем только те элементы, которые реально требуются policy targets.
        for i, part in enumerate(parts, 1):
            has_diagram = i in target_diagram_indexes
            has_formula = i in target_formula_indexes
            has_table = i in target_table_indexes
            has_code = is_programming and global_targets.code_examples > 0 and (i == 1 or i == 2)

            per_part_plans[i] = PartEnhancementPlan(
                part_index=i,
                topic=part.title,
                formulas=ImportanceLevel.MUST if has_formula else ImportanceLevel.NO,
                tables=ImportanceLevel.MUST if has_table else ImportanceLevel.NO,
                diagrams=ImportanceLevel.MUST if has_diagram else ImportanceLevel.NO,
                code_examples=ImportanceLevel.MUST if has_code else ImportanceLevel.NO,
                reasoning="Fallback план по content_type policy при ошибке парсинга или пустом ответе LLM"
            )

        return EnhancementPlan(
            global_targets=global_targets,
            budget=budget or EnhancementBudget(**DEFAULT_ENHANCEMENT_BUDGET),
            per_part=per_part_plans,
            is_programming_project=is_programming,
            reasoning="Fallback план",
            fallback_traces=[
                FallbackTraceEvent.from_fallback(
                    node="theory_enhancement",
                    fallback_type=fallback_type,
                    reason=reason,
                    quality_risk="medium",
                    inputs={
                        "parts_count": len(parts),
                        "global_targets": global_targets.model_dump(mode="json"),
                        "is_programming": is_programming,
                    },
                    trace={"planned_parts": sorted(per_part_plans)},
                ).model_dump(mode="json")
            ],
        )
