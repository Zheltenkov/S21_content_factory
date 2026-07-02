"""
content_gen/agents/enhancement_manager.py

Менеджер для координации агентов улучшения контента теории.

Координирует применение улучшений к частям теории:
- CodeExampleAgent: примеры кода
- FormulaTableAgent: формулы, таблицы, Mermaid диаграммы

Принимает решения на основе анализа темы и навыков.
"""

from .base.llm_client import LLMClientProtocol
from ..models.enhancement_models import FormulaItem, TableItem, VisualItem
from ..models.enhancement_plan import EnhancementExecutionLog, EnhancementPlan, ImportanceLevel
from ..models.schemas import ProjectSeed, TheoryPart

from ..utils.logging import safe_print
from .code_example import CodeExampleAgent
from .formula_table import FormulaTableAgent


class TheoryEnhancementManager:
    """Менеджер для координации агентов улучшения контента теории."""

    def __init__(self, llm: LLMClientProtocol):
        self.llm = llm
        self.code_agent = CodeExampleAgent(llm)
        self.formula_agent = FormulaTableAgent(llm)

    def enhance_parts_with_plan(
        self,
        parts: list[TheoryPart],
        seed: ProjectSeed,
        plan: EnhancementPlan
    ) -> tuple[list[TheoryPart], list[EnhancementExecutionLog]]:
        """
        Применяет улучшения к частям теории на основе глобального плана.
        
        Args:
            parts: Список частей теории
            seed: Входные данные проекта
            plan: Глобальный план улучшений
        
        Returns:
            Кортеж: (улучшенные части, логи выполнения)
        """
        safe_print(f"[ENHANCEMENT] Применение плана к {len(parts)} частям", flush=True)

        enhanced_parts = []
        execution_logs = []
        all_formulas = []
        all_tables = []
        all_visuals = []
        all_code_examples = []

        for i, part in enumerate(parts, 1):
            part_plan = plan.per_part.get(i)
            if not part_plan:
                safe_print(f"  ⚠️ Нет плана для части {i}, пропускаем", flush=True)
                enhanced_parts.append(part)
                continue

            safe_print(f"  🔧 Улучшение части {i}/{len(parts)}: {part.title[:50]}...", flush=True)
            safe_print(f"     План: formulas={part_plan.formulas.value}, tables={part_plan.tables.value}, diagrams={part_plan.diagrams.value}, code={part_plan.code_examples.value}", flush=True)

            log = EnhancementExecutionLog(
                part_index=i,
                topic=part.title,
                plan=part_plan,
                generated={},
                embedded_positions={},
                errors=[]
            )

            enhanced_body = part.body
            new_formulas = []
            new_tables = []
            new_visuals = []
            new_code_examples = []

            try:
                # 1. Формулы (если must или nice_to_have)
                if seed.include_formulas and part_plan.formulas in [ImportanceLevel.MUST, ImportanceLevel.NICE_TO_HAVE]:
                    try:
                        formula_result = self.formula_agent.analyze(
                            topic=part.title,
                            theory_text=part.body,
                            seed=seed,
                            existing_formulas=all_formulas,
                            existing_tables=all_tables,
                            existing_enhancements={}
                        )

                        if formula_result.needs_formulas:
                            generation_result = self.formula_agent._generate(
                                topic=part.title,
                                theory_text=part.body,
                                skills=seed.skills or [],
                                seed=seed,
                                needs_formulas=True,
                                needs_tables=False,
                                needs_visuals=False,
                                existing_formulas=all_formulas,
                                existing_tables=all_tables
                            )

                            if generation_result.get("formulas"):
                                new_formulas = [FormulaItem(**f) for f in generation_result.get("formulas", [])]
                                # Встраивание по якорям или общим правилам
                                enhanced_body = self._embed_formulas(enhanced_body, new_formulas, part_plan.anchor_hints)
                                log.embedded_positions["formulas"] = [f"встроено {len(new_formulas)} формул"]
                    except Exception as e:
                        error_msg = f"Ошибка генерации формул: {str(e)}"
                        log.errors.append(error_msg)
                        safe_print(f"     ⚠️ {error_msg}", flush=True)

                # 2. Таблицы (если must или nice_to_have; для MUST генерируем даже при seed.include_tables=False — план приоритетнее)
                if (seed.include_tables or part_plan.tables == ImportanceLevel.MUST) and part_plan.tables in [ImportanceLevel.MUST, ImportanceLevel.NICE_TO_HAVE]:
                    try:
                        # Для MUST таблицы генерируем принудительно, для NICE_TO_HAVE проверяем через analyze
                        is_must = part_plan.tables == ImportanceLevel.MUST

                        if is_must:
                            # Для MUST генерируем принудительно
                            needs_tables = True
                        else:
                            # Для NICE_TO_HAVE проверяем через analyze
                            formula_result = self.formula_agent.analyze(
                                topic=part.title,
                                theory_text=enhanced_body,
                                seed=seed,
                                existing_formulas=all_formulas + new_formulas,
                                existing_tables=all_tables,
                                existing_enhancements={}
                            )
                            needs_tables = formula_result.needs_tables

                        if needs_tables:
                            generation_result = self.formula_agent._generate(
                                topic=part.title,
                                theory_text=enhanced_body,
                                skills=seed.skills or [],
                                seed=seed,
                                needs_formulas=False,
                                needs_tables=True,
                                needs_visuals=False,
                                existing_formulas=all_formulas + new_formulas,
                                existing_tables=all_tables
                            )

                            if generation_result.get("tables"):
                                new_tables = [TableItem(**t) for t in generation_result.get("tables", [])]
                                enhanced_body = self._embed_tables(enhanced_body, new_tables, part_plan.anchor_hints)
                                log.embedded_positions["tables"] = [f"встроено {len(new_tables)} таблиц"]
                            elif is_must:
                                # Если таблицы MUST, но не сгенерированы - это ошибка
                                error_msg = f"Таблицы обязательны (MUST) для части '{part.title}', но не удалось сгенерировать"
                                log.errors.append(error_msg)
                                safe_print(f"     ⚠️ {error_msg}", flush=True)
                    except Exception as e:
                        error_msg = f"Ошибка генерации таблиц: {str(e)}"
                        log.errors.append(error_msg)
                        safe_print(f"     ⚠️ {error_msg}", flush=True)

                # 3. Диаграммы (план приоритетнее seed: при MUST или NICE_TO_HAVE пробуем генерировать даже если seed.include_diagrams=False)
                if (seed.include_diagrams or part_plan.diagrams in [ImportanceLevel.MUST, ImportanceLevel.NICE_TO_HAVE]) and part_plan.diagrams in [ImportanceLevel.MUST, ImportanceLevel.NICE_TO_HAVE]:
                    try:
                        is_must_diagrams = part_plan.diagrams == ImportanceLevel.MUST
                        # Раз план сказал MUST или NICE_TO_HAVE — пробуем сгенерировать (не зависим от analyze/seed)
                        needs_visuals = True
                        if needs_visuals:
                            generation_result = self.formula_agent._generate(
                                topic=part.title,
                                theory_text=enhanced_body,
                                skills=seed.skills or [],
                                seed=seed,
                                needs_formulas=False,
                                needs_tables=False,
                                needs_visuals=True,
                                existing_formulas=all_formulas + new_formulas,
                                existing_tables=all_tables + new_tables
                            )

                            if generation_result.get("visuals"):
                                new_visuals = [VisualItem(**v) for v in generation_result.get("visuals", [])]
                                enhanced_body = self.formula_agent.embed_in_text(
                                    enhanced_body,
                                    [],
                                    [],
                                    new_visuals[:1]  # Максимум 1 диаграмма на часть
                                )
                                log.embedded_positions["diagrams"] = [f"встроена {len(new_visuals)} диаграмма"]
                            elif is_must_diagrams:
                                error_msg = f"Диаграммы обязательны (MUST) для части '{part.title}', но не удалось сгенерировать"
                                log.errors.append(error_msg)
                                safe_print(f"     ⚠️ {error_msg}", flush=True)
                    except Exception as e:
                        error_msg = f"Ошибка генерации диаграмм: {str(e)}"
                        log.errors.append(error_msg)
                        safe_print(f"     ⚠️ {error_msg}", flush=True)

                # 4. Примеры кода (если must или nice_to_have и проект программистский)
                if plan.is_programming_project and part_plan.code_examples in [ImportanceLevel.MUST, ImportanceLevel.NICE_TO_HAVE]:
                    try:
                        code_result = self.code_agent.generate(
                            topic=part.title,
                            skills=seed.skills or [],
                            seed=seed,
                            context=enhanced_body[:500]  # Используем context вместо context_preview
                        )

                        if code_result.examples:
                            new_code_examples = code_result.examples
                            enhanced_body = self.code_agent.embed_example_in_text(
                                enhanced_body,
                                new_code_examples[0]  # Первый пример
                            )
                            log.embedded_positions["code_examples"] = [f"встроено {len(new_code_examples)} примеров"]
                    except Exception as e:
                        error_msg = f"Ошибка генерации примеров кода: {str(e)}"
                        log.errors.append(error_msg)
                        safe_print(f"     ⚠️ {error_msg}", flush=True)

            except Exception as e:
                error_msg = f"Критическая ошибка при улучшении части: {str(e)}"
                log.errors.append(error_msg)
                safe_print(f"     ❌ {error_msg}", flush=True)

            # Обновляем лог
            log.generated = {
                "formulas": len(new_formulas),
                "tables": len(new_tables),
                "diagrams": len(new_visuals),
                "code_examples": len(new_code_examples)
            }

            # Создаем улучшенную часть
            enhanced_part = TheoryPart(
                title=part.title,
                body=enhanced_body,
                example=part.example,
                bridge_questions=part.bridge_questions,
                covers_outcomes=part.covers_outcomes,
                references=part.references.copy() if part.references else [],
                text_markdown=part.body  # Сохраняем исходный текст
            )

            enhanced_parts.append(enhanced_part)
            execution_logs.append(log)

            # Обновляем общие списки
            all_formulas.extend(new_formulas)
            all_tables.extend(new_tables)
            all_visuals.extend(new_visuals)
            all_code_examples.extend(new_code_examples)

            safe_print(f"     ✅ Сгенерировано: {len(new_formulas)} формул, {len(new_tables)} таблиц, {len(new_visuals)} диаграмм, {len(new_code_examples)} примеров кода", flush=True)

        safe_print(f"  ✅ Всего сгенерировано: {len(all_formulas)} формул, {len(all_tables)} таблиц, {len(all_visuals)} диаграмм, {len(all_code_examples)} примеров кода", flush=True)

        return enhanced_parts, execution_logs

    def _embed_formulas(self, text: str, formulas: list[FormulaItem], anchor_hints: dict[str, str] | None = None) -> str:
        """Встраивает формулы в текст по якорям или общим правилам."""
        if not formulas:
            return text

        import re

        from ..utils.markdown_renderer import render_formula

        # Ищем якоря в тексте ({{INSERT_FORMULA:...}})
        anchor_pattern = r'\{\{INSERT_FORMULA:([^}]+)\}\}'

        for formula in formulas:
            formula_md = render_formula(formula.label, formula.latex, formula.parameters)

            # Пытаемся найти якорь для этой формулы
            matches = list(re.finditer(anchor_pattern, text))
            if matches:
                # Используем первый найденный якорь
                match = matches[0]
                anchor_name = match.group(1)
                # Заменяем якорь на формулу
                text = text[:match.start()] + formula_md + text[match.end():]
                safe_print(f"     ✅ Формула встроена по якорю: {anchor_name}", flush=True)
            else:
                # Если есть подсказка, пытаемся найти место по ключевым словам
                if anchor_hints and "formula" in anchor_hints:
                    hint = anchor_hints["formula"].lower()
                    # Ищем ключевые слова из подсказки в тексте
                    hint_words = hint.split()
                    # Ищем предложения, содержащие ключевые слова
                    sentences = re.split(r'([.!?]\s+)', text)
                    insert_pos = None
                    for i, sentence in enumerate(sentences):
                        if any(word in sentence.lower() for word in hint_words if len(word) > 3):
                            # Вставляем после этого предложения
                            insert_pos = sum(len(s) for s in sentences[:i+1])
                            break

                    if insert_pos is not None:
                        text = text[:insert_pos] + f"\n\n{formula_md}\n\n" + text[insert_pos:]
                        safe_print(f"     ✅ Формула встроена по подсказке: {hint[:50]}...", flush=True)
                        continue

                # Общие правила: вставляем перед **Пример:** или в конец
                if "**Пример:**" in text:
                    text = text.replace("**Пример:**", f"{formula_md}\n\n**Пример:**", 1)
                    safe_print("     ✅ Формула встроена перед **Пример:**", flush=True)
                else:
                    # Вставляем в конец перед **Вопросы к практике:**
                    if "**Вопросы к практике:**" in text:
                        text = text.replace("**Вопросы к практике:**", f"{formula_md}\n\n**Вопросы к практике:**", 1)
                        safe_print("     ✅ Формула встроена перед **Вопросы к практике:**", flush=True)
                    else:
                        # Вставляем в самый конец
                        text = f"{text}\n\n{formula_md}"
                        safe_print("     ✅ Формула встроена в конец текста", flush=True)

        return text
    def _embed_tables(self, text: str, tables: list[TableItem], anchor_hints: dict[str, str] | None = None) -> str:
        """Встраивает таблицы в текст по якорям или общим правилам."""
        if not tables:
            return text

        import re

        from ..utils.markdown_renderer import render_table

        # Ищем якоря в тексте ({{INSERT_TABLE:...}})
        anchor_pattern = r'\{\{INSERT_TABLE:([^}]+)\}\}'

        for table in tables:
            table_md = render_table(table.label, table.md_table, table.description)

            # Пытаемся найти якорь для этой таблицы
            matches = list(re.finditer(anchor_pattern, text))
            if matches:
                # Используем первый найденный якорь
                match = matches[0]
                anchor_name = match.group(1)
                # Заменяем якорь на таблицу
                text = text[:match.start()] + table_md + text[match.end():]
                safe_print(f"     ✅ Таблица встроена по якорю: {anchor_name}", flush=True)
            else:
                # Если есть подсказка, пытаемся найти место по ключевым словам
                if anchor_hints and "table" in anchor_hints:
                    hint = anchor_hints["table"].lower()
                    hint_words = hint.split()
                    sentences = re.split(r'([.!?]\s+)', text)
                    insert_pos = None
                    for i, sentence in enumerate(sentences):
                        if any(word in sentence.lower() for word in hint_words if len(word) > 3):
                            insert_pos = sum(len(s) for s in sentences[:i+1])
                            break

                    if insert_pos is not None:
                        text = text[:insert_pos] + f"\n\n{table_md}\n\n" + text[insert_pos:]
                        safe_print(f"     ✅ Таблица встроена по подсказке: {hint[:50]}...", flush=True)
                        continue

                # Общие правила: вставляем перед **Пример:**
                if "**Пример:**" in text:
                    text = text.replace("**Пример:**", f"{table_md}\n\n**Пример:**", 1)
                    safe_print("     ✅ Таблица встроена перед **Пример:**", flush=True)
                else:
                    if "**Вопросы к практике:**" in text:
                        text = text.replace("**Вопросы к практике:**", f"{table_md}\n\n**Вопросы к практике:**", 1)
                        safe_print("     ✅ Таблица встроена перед **Вопросы к практике:**", flush=True)
                    else:
                        text = f"{text}\n\n{table_md}"
                        safe_print("     ✅ Таблица встроена в конец текста", flush=True)

        return text
    def _embed_diagrams(self, text: str, diagrams: list[VisualItem], anchor_hints: dict[str, str] | None = None) -> str:
        """Встраивает диаграммы в текст по якорям или общим правилам."""
        if not diagrams:
            return text

        import re

        # Ищем якоря в тексте ({{INSERT_DIAGRAM:...}})
        anchor_pattern = r'\{\{INSERT_DIAGRAM:([^}]+)\}\}'

        for diagram in diagrams:
            # Используем embed_in_text из formula_agent для правильного форматирования
            # Но сначала проверяем якоря
            matches = list(re.finditer(anchor_pattern, text))
            if matches:
                # Используем первый найденный якорь
                match = matches[0]
                anchor_name = match.group(1)
                # Генерируем Markdown для диаграммы
                from ..utils.markdown_renderer import render_mermaid
                diagram_md = render_mermaid(diagram.label, diagram.mermaid, diagram.description)
                # Заменяем якорь на диаграмму
                text = text[:match.start()] + diagram_md + text[match.end():]
                safe_print(f"     ✅ Диаграмма встроена по якорю: {anchor_name}", flush=True)
            else:
                # Если есть подсказка, пытаемся найти место по ключевым словам
                if anchor_hints and "diagram" in anchor_hints:
                    hint = anchor_hints["diagram"].lower()
                    hint_words = hint.split()
                    sentences = re.split(r'([.!?]\s+)', text)
                    insert_pos = None
                    for i, sentence in enumerate(sentences):
                        if any(word in sentence.lower() for word in hint_words if len(word) > 3):
                            insert_pos = sum(len(s) for s in sentences[:i+1])
                            break

                    if insert_pos is not None:
                        from ..utils.markdown_renderer import render_mermaid
                        diagram_md = render_mermaid(diagram.label, diagram.mermaid, diagram.description)
                        text = text[:insert_pos] + f"\n\n{diagram_md}\n\n" + text[insert_pos:]
                        safe_print(f"     ✅ Диаграмма встроена по подсказке: {hint[:50]}...", flush=True)
                        continue

                # Используем стандартный метод встраивания
                enhanced_body = self.formula_agent.embed_in_text(
                    text,
                    [],
                    [],
                    [diagram]
                )
                text = enhanced_body
                safe_print("     ✅ Диаграмма встроена по общим правилам", flush=True)

        return text
