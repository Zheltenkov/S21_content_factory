"""
Объединенный агент улучшения теории (EnhancementPlanner + TheoryEnhancementManager).

Фасадный агент, который объединяет планирование и применение улучшений
в одну точку входа для упрощения использования в Orchestrator.
"""


from .base.llm_client import LLMClientProtocol
from ..models.enhancement_plan import EnhancementExecutionLog, EnhancementPlan
from ..models.schemas import ProjectSeed, TheoryPart
from ..utils.logging import safe_print
from .enhancement_manager import TheoryEnhancementManager
from .enhancement_planner import EnhancementPlanner
from .quality_gate import QualityGate


class TheoryEnhancementAgent:
    """
    Объединенный агент улучшения теории.
    
    Объединяет функциональность:
    - EnhancementPlanner: глобальное планирование улучшений
    - TheoryEnhancementManager: применение улучшений к частям
    - QualityGate: проверка качества выполнения плана
    """

    def __init__(self, llm_client: LLMClientProtocol):
        """
        Инициализация агента улучшения теории.
        
        Args:
            llm_client: LLM клиент для генерации
        """
        self.planner = EnhancementPlanner(llm_client)
        self.manager = TheoryEnhancementManager(llm_client)
        self.quality_gate = QualityGate()

    def enhance(
        self,
        parts: list[TheoryPart],
        seed: ProjectSeed,
        profile: str | None = None,
    ) -> tuple[list[TheoryPart], EnhancementPlan, list[EnhancementExecutionLog]]:
        """
        Выполняет полный цикл улучшения теории: планирование + применение.
        
        Args:
            parts: Список частей теории
            seed: Входные данные проекта
            profile: Профиль генерации (опционально)
            
        Returns:
            Кортеж (enhanced_parts, enhancement_plan, execution_logs)
        """
        # Фаза 1: Планирование улучшений
        safe_print(f"  📋 Планирование улучшений для {len(parts)} частей...", flush=True)
        enhancement_plan = self.planner.create_plan(parts, seed, profile=profile)

        # Фаза 2: Применение улучшений
        safe_print("  🔧 Применение улучшений...", flush=True)
        enhanced_parts, execution_logs = self.manager.enhance_parts_with_plan(
            parts, seed, enhancement_plan
        )

        # Фаза 3: Проверка качества (опционально)
        if execution_logs:
            safe_print("  ✅ Проверка качества улучшений...", flush=True)
            quality_result = self.quality_gate.check(enhancement_plan, execution_logs)
            if not quality_result.passed:
                safe_print(f"  ⚠️ Quality Gate: {len(quality_result.violations)} нарушений, {len(quality_result.warnings)} предупреждений", flush=True)
            else:
                safe_print("  ✅ Quality Gate пройден", flush=True)

        return enhanced_parts, enhancement_plan, execution_logs

