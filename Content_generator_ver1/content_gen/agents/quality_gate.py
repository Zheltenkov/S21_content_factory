"""
content_gen/agents/quality_gate.py

Quality Gate для проверки соответствия плана улучшений и фактически сгенерированных элементов.

Разделяет:
- Hard guarantees (must) - обязательные элементы
- Targets (nice_to_have) - желательные элементы
- Budget limits - ограничения по бюджету
"""


from ..models.enhancement_plan import EnhancementExecutionLog, EnhancementPlan, ImportanceLevel, QualityGateResult
from ..utils.logging import safe_print


class QualityGate:
    """Проверка качества выполнения плана улучшений."""

    def check(
        self,
        plan: EnhancementPlan,
        execution_logs: list[EnhancementExecutionLog]
    ) -> QualityGateResult:
        """
        Проверяет соответствие плана и фактически сгенерированных элементов.
        
        Args:
            plan: Глобальный план улучшений
            execution_logs: Логи выполнения для каждой части
        
        Returns:
            QualityGateResult с нарушениями и предупреждениями
        """
        safe_print("[QUALITY_GATE] Начало проверки качества", flush=True)

        violations = []
        warnings = []

        # Подсчитываем фактические значения
        total_formulas = sum(log.generated.get("formulas", 0) for log in execution_logs)
        total_tables = sum(log.generated.get("tables", 0) for log in execution_logs)
        total_diagrams = sum(log.generated.get("diagrams", 0) for log in execution_logs)
        total_code_examples = sum(log.generated.get("code_examples", 0) for log in execution_logs)

        safe_print("  📊 Фактические значения:", flush=True)
        safe_print(f"     - Формулы: {total_formulas}", flush=True)
        safe_print(f"     - Таблицы: {total_tables}", flush=True)
        safe_print(f"     - Диаграммы: {total_diagrams}", flush=True)
        safe_print(f"     - Примеры кода: {total_code_examples}", flush=True)

        # Проверка hard guarantees (must)
        safe_print("  🔍 Проверка hard guarantees (must)...", flush=True)

        # 1. Проверка глобальных целей
        if plan.global_targets.diagrams > 0 and total_diagrams < plan.global_targets.diagrams:
            violations.append({
                "type": "hard_guarantee",
                "element": "diagrams",
                "expected": plan.global_targets.diagrams,
                "actual": total_diagrams,
                "reason": f"Требуется минимум {plan.global_targets.diagrams} диаграмма(ы), сгенерировано {total_diagrams}"
            })
            safe_print(f"     ❌ Нарушение: диаграммы ({total_diagrams} < {plan.global_targets.diagrams})", flush=True)

        if plan.global_targets.formulas > 0 and total_formulas < plan.global_targets.formulas:
            violations.append({
                "type": "hard_guarantee",
                "element": "formulas",
                "expected": plan.global_targets.formulas,
                "actual": total_formulas,
                "reason": f"Требуется минимум {plan.global_targets.formulas} формул(ы), сгенерировано {total_formulas}"
            })
            safe_print(f"     ❌ Нарушение: формулы ({total_formulas} < {plan.global_targets.formulas})", flush=True)

        if plan.is_programming_project and plan.global_targets.code_examples > 0:
            if total_code_examples < plan.global_targets.code_examples:
                violations.append({
                    "type": "hard_guarantee",
                    "element": "code_examples",
                    "expected": plan.global_targets.code_examples,
                    "actual": total_code_examples,
                    "reason": f"Требуется минимум {plan.global_targets.code_examples} пример(ов) кода, сгенерировано {total_code_examples}"
                })
                safe_print(f"     ❌ Нарушение: примеры кода ({total_code_examples} < {plan.global_targets.code_examples})", flush=True)

        # 2. Проверка must в планах частей
        for part_idx, part_plan in plan.per_part.items():
            log = next((l for l in execution_logs if l.part_index == part_idx), None)
            if not log:
                continue

            # Проверяем must для формул
            if part_plan.formulas == ImportanceLevel.MUST:
                if log.generated.get("formulas", 0) == 0:
                    violations.append({
                        "type": "hard_guarantee",
                        "element": "formulas",
                        "part": part_idx,
                        "expected": ">= 1",
                        "actual": 0,
                        "reason": f"В части {part_idx} ('{part_plan.topic}') формулы обязательны (must), но не сгенерированы"
                    })
                    safe_print(f"     ❌ Нарушение: часть {part_idx} - формулы must, но не сгенерированы", flush=True)

            # Проверяем must для таблиц
            if part_plan.tables == ImportanceLevel.MUST:
                if log.generated.get("tables", 0) == 0:
                    violations.append({
                        "type": "hard_guarantee",
                        "element": "tables",
                        "part": part_idx,
                        "expected": ">= 1",
                        "actual": 0,
                        "reason": f"В части {part_idx} ('{part_plan.topic}') таблицы обязательны (must), но не сгенерированы"
                    })
                    safe_print(f"     ❌ Нарушение: часть {part_idx} - таблицы must, но не сгенерированы", flush=True)

            # Проверяем must для диаграмм
            if part_plan.diagrams == ImportanceLevel.MUST:
                if log.generated.get("diagrams", 0) == 0:
                    violations.append({
                        "type": "hard_guarantee",
                        "element": "diagrams",
                        "part": part_idx,
                        "expected": ">= 1",
                        "actual": 0,
                        "reason": f"В части {part_idx} ('{part_plan.topic}') диаграммы обязательны (must), но не сгенерированы"
                    })
                    safe_print(f"     ❌ Нарушение: часть {part_idx} - диаграммы must, но не сгенерированы", flush=True)

            # Проверяем must для примеров кода
            if part_plan.code_examples == ImportanceLevel.MUST:
                if log.generated.get("code_examples", 0) == 0:
                    violations.append({
                        "type": "hard_guarantee",
                        "element": "code_examples",
                        "part": part_idx,
                        "expected": ">= 1",
                        "actual": 0,
                        "reason": f"В части {part_idx} ('{part_plan.topic}') примеры кода обязательны (must), но не сгенерированы"
                    })
                    safe_print(f"     ❌ Нарушение: часть {part_idx} - примеры кода must, но не сгенерированы", flush=True)

        # Проверка targets (nice_to_have)
        safe_print("  🔍 Проверка targets (nice_to_have)...", flush=True)

        for part_idx, part_plan in plan.per_part.items():
            log = next((l for l in execution_logs if l.part_index == part_idx), None)
            if not log:
                continue

            if part_plan.formulas == ImportanceLevel.NICE_TO_HAVE:
                if log.generated.get("formulas", 0) == 0:
                    warnings.append({
                        "type": "target_missed",
                        "element": "formulas",
                        "part": part_idx,
                        "expected": ">= 1",
                        "actual": 0,
                        "reason": f"В части {part_idx} ('{part_plan.topic}') формулы желательны (nice_to_have), но не сгенерированы"
                    })

            if part_plan.tables == ImportanceLevel.NICE_TO_HAVE:
                if log.generated.get("tables", 0) == 0:
                    warnings.append({
                        "type": "target_missed",
                        "element": "tables",
                        "part": part_idx,
                        "expected": ">= 1",
                        "actual": 0,
                        "reason": f"В части {part_idx} ('{part_plan.topic}') таблицы желательны (nice_to_have), но не сгенерированы"
                    })

            if part_plan.diagrams == ImportanceLevel.NICE_TO_HAVE:
                if log.generated.get("diagrams", 0) == 0:
                    warnings.append({
                        "type": "target_missed",
                        "element": "diagrams",
                        "part": part_idx,
                        "expected": ">= 1",
                        "actual": 0,
                        "reason": f"В части {part_idx} ('{part_plan.topic}') диаграммы желательны (nice_to_have), но не сгенерированы"
                    })

            if part_plan.code_examples == ImportanceLevel.NICE_TO_HAVE:
                if log.generated.get("code_examples", 0) == 0:
                    warnings.append({
                        "type": "target_missed",
                        "element": "code_examples",
                        "part": part_idx,
                        "expected": ">= 1",
                        "actual": 0,
                        "reason": f"В части {part_idx} ('{part_plan.topic}') примеры кода желательны (nice_to_have), но не сгенерированы"
                    })

        # Проверка бюджета
        safe_print("  🔍 Проверка бюджета...", flush=True)

        if total_formulas > plan.budget.formulas["max"]:
            warnings.append({
                "type": "budget_exceeded",
                "element": "formulas",
                "expected": f"<= {plan.budget.formulas['max']}",
                "actual": total_formulas,
                "reason": f"Превышен бюджет формул: {total_formulas} > {plan.budget.formulas['max']}"
            })
            safe_print(f"     ⚠️ Предупреждение: превышен бюджет формул ({total_formulas} > {plan.budget.formulas['max']})", flush=True)

        if total_tables > plan.budget.tables["max"]:
            warnings.append({
                "type": "budget_exceeded",
                "element": "tables",
                "expected": f"<= {plan.budget.tables['max']}",
                "actual": total_tables,
                "reason": f"Превышен бюджет таблиц: {total_tables} > {plan.budget.tables['max']}"
            })
            safe_print(f"     ⚠️ Предупреждение: превышен бюджет таблиц ({total_tables} > {plan.budget.tables['max']})", flush=True)

        if total_diagrams > plan.budget.diagrams["max"]:
            warnings.append({
                "type": "budget_exceeded",
                "element": "diagrams",
                "expected": f"<= {plan.budget.diagrams['max']}",
                "actual": total_diagrams,
                "reason": f"Превышен бюджет диаграмм: {total_diagrams} > {plan.budget.diagrams['max']}"
            })
            safe_print(f"     ⚠️ Предупреждение: превышен бюджет диаграмм ({total_diagrams} > {plan.budget.diagrams['max']})", flush=True)

        if total_code_examples > plan.budget.code_examples["max"]:
            warnings.append({
                "type": "budget_exceeded",
                "element": "code_examples",
                "expected": f"<= {plan.budget.code_examples['max']}",
                "actual": total_code_examples,
                "reason": f"Превышен бюджет примеров кода: {total_code_examples} > {plan.budget.code_examples['max']}"
            })
            safe_print(f"     ⚠️ Предупреждение: превышен бюджет примеров кода ({total_code_examples} > {plan.budget.code_examples['max']})", flush=True)

        # Проверка ошибок в логах
        for log in execution_logs:
            if log.errors:
                for error in log.errors:
                    violations.append({
                        "type": "generation_error",
                        "part": log.part_index,
                        "element": "unknown",
                        "expected": "success",
                        "actual": "error",
                        "reason": f"Ошибка в части {log.part_index}: {error}"
                    })
                    safe_print(f"     ❌ Ошибка генерации в части {log.part_index}: {error}", flush=True)

        # Вычисляем оценку качества
        passed = len(violations) == 0
        grade = self._calculate_grade(violations, warnings, plan)

        safe_print("  ✅ Проверка завершена:", flush=True)
        safe_print(f"     - Нарушений: {len(violations)}", flush=True)
        safe_print(f"     - Предупреждений: {len(warnings)}", flush=True)
        safe_print(f"     - Оценка: {grade:.2f}", flush=True)
        safe_print(f"     - Статус: {'✅ ПРОШЕЛ' if passed else '❌ НЕ ПРОШЕЛ'}", flush=True)

        return QualityGateResult(
            passed=passed,
            violations=violations,
            warnings=warnings,
            grade=grade
        )

    def _calculate_grade(
        self,
        violations: list[dict],
        warnings: list[dict],
        plan: EnhancementPlan
    ) -> float:
        """
        Вычисляет оценку качества (0.0-1.0).
        
        Args:
            violations: Список нарушений
            warnings: Список предупреждений
            plan: План улучшений
        
        Returns:
            Оценка качества от 0.0 до 1.0
        """
        # Базовая оценка
        grade = 1.0

        # Штрафы за нарушения (hard guarantees)
        # Каждое нарушение снижает оценку на 0.2
        grade -= len(violations) * 0.2

        # Штрафы за предупреждения (targets)
        # Каждое предупреждение снижает оценку на 0.05
        grade -= len(warnings) * 0.05

        # Ограничиваем диапазон
        grade = max(0.0, min(1.0, grade))

        return grade

