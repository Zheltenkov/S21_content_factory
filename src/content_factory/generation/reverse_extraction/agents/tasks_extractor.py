"""
TasksExtractor - детальное извлечение и анализ практических задач из README.

Специализируется на точном подсчете задач и их структурировании.
"""

import logging
import re

from pydantic import BaseModel, Field

from ...config.loader import get_agent_config, prompt_trace_kwargs
from ...agents.base.llm_client import LLMClientProtocol
from ...llm.structured_output import StructuredLLMClient
from ..models import NormalizedReadme

logger = logging.getLogger(__name__)


class TasksExtractionResult(BaseModel):
    """Результат извлечения задач."""
    tasks_count: int | None = Field(
        default=None,
        ge=0,
        description="Точное количество практических задач"
    )
    task_descriptions: list[str] = Field(
        default_factory=list,
        description="Краткие описания каждой задачи (для валидации подсчета)"
    )
    confidence: str = Field(
        default="medium",
        description="Уровень уверенности в подсчете: high, medium, low"
    )


class TasksExtractor:
    """Агент для детального извлечения и подсчета практических задач."""

    CONFIG_NAME = "tasks_extractor"

    def __init__(self, llm: LLMClientProtocol):
        """
        Инициализация экстрактора задач.
        
        Args:
            llm: LLM клиент для извлечения
        """
        self.llm = llm
        self.structured_client = StructuredLLMClient(llm)
        self.config = get_agent_config(self.CONFIG_NAME)
        self.llm_kwargs = self.config.llm.to_kwargs() if self.config.llm else {}

    def extract_tasks(
        self,
        normalized_readme: NormalizedReadme,
        initial_count: int | None = None
    ) -> TasksExtractionResult:
        """
        Детально извлекает и подсчитывает практические задачи.
        
        Args:
            normalized_readme: Нормализованный README
            initial_count: Предварительный подсчет из StructureExtractor (для валидации)
            
        Returns:
            TasksExtractionResult с точным количеством задач
        """
        # Получаем главу 3 (практика) или ищем раздел с практикой
        practice_text = self._get_practice_section(normalized_readme)

        if not practice_text:
            logger.warning("Раздел с практикой не найден")
            return TasksExtractionResult(
                tasks_count=initial_count or 0,
                confidence="low"
            )

        # Формируем промпты
        system_prompt = self.config.get_prompt("system")
        user_prompt = self.config.get_prompt("user_template").format(
            practice_text=practice_text[:15000],  # Ограничиваем размер
            initial_count=initial_count or "не определено"
        )

        # Получаем извлеченные данные через structured output
        llm_kwargs = self.llm_kwargs.copy()
        llm_kwargs.setdefault("temperature", 0.1)
        llm_kwargs.update(
            prompt_trace_kwargs(
                self.config,
                "system",
                "user_template",
                output_schema="TasksExtractionResult",
            )
        )

        try:
            result = self.structured_client.complete_structured(
                output_model=TasksExtractionResult,
                system=system_prompt,
                user=user_prompt,
                **llm_kwargs
            )

            logger.info(
                f"TasksExtractor: найдено {result.tasks_count} задач "
                f"(уверенность: {result.confidence})"
            )

            # Валидация: если LLM вернул невероятное значение, используем fallback
            if result.tasks_count is not None:
                if result.tasks_count > 20:
                    logger.warning(f"Подозрительно большое количество задач: {result.tasks_count}, используем fallback")
                    return self._fallback_count(practice_text, initial_count)
                if result.tasks_count < 0:
                    logger.warning(f"Отрицательное количество задач: {result.tasks_count}, используем fallback")
                    return self._fallback_count(practice_text, initial_count)

            return result

        except Exception as e:
            logger.error(f"Ошибка при извлечении задач: {e}")
            return self._fallback_count(practice_text, initial_count)

    def _get_practice_section(self, normalized_readme: NormalizedReadme) -> str:
        """Извлекает текст раздела с практикой."""
        # Сначала проверяем главу 3
        if 3 in normalized_readme.chapters:
            return normalized_readme.chapters[3]

        # Ищем разделы с практикой по заголовкам
        raw_text = normalized_readme.raw_text
        practice_patterns = [
            r'##\s+(?:Глава\s+3|Практика|Practice|Задачи|Tasks|Задания)[^\n]*\n(.*?)(?=\n##|\Z)',
            r'###\s+(?:Практика|Practice|Задачи|Tasks)[^\n]*\n(.*?)(?=\n###|\n##|\Z)',
        ]

        for pattern in practice_patterns:
            match = re.search(pattern, raw_text, re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip()

        # Если не нашли, возвращаем весь текст после главы 2
        if 2 in normalized_readme.chapters:
            # Ищем позицию после главы 2
            chapter2_end = raw_text.find(normalized_readme.chapters[2]) + len(normalized_readme.chapters[2])
            return raw_text[chapter2_end:].strip()

        return ""

    def _fallback_count(
        self,
        practice_text: str,
        initial_count: int | None = None
    ) -> TasksExtractionResult:
        """
        Fallback метод для подсчета задач без LLM.
        
        Использует регулярные выражения для поиска задач.
        """
        if not practice_text:
            return TasksExtractionResult(
                tasks_count=initial_count or 0,
                confidence="low"
            )

        task_patterns = [
            r'(?:Задача|Task|Задание)\s+(\d+)',
            r'^\s*(\d+)\.\s+(?:Задача|Task|Задание)',
        ]

        found_numbers = set()
        for pattern in task_patterns:
            matches = re.findall(pattern, practice_text, re.IGNORECASE | re.MULTILINE)
            for match in matches:
                try:
                    num = int(match)
                    if 1 <= num <= 20:  # Разумные пределы
                        found_numbers.add(num)
                except ValueError:
                    continue

        # Если нашли номера, берем максимальный
        if found_numbers:
            count = max(found_numbers)
            logger.info(f"Fallback: найдено {count} задач по номерам")
            return TasksExtractionResult(
                tasks_count=count,
                confidence="medium"
            )

        final_count = initial_count or 0
        logger.warning(f"Fallback: не удалось определить количество задач, используем {final_count}")
        return TasksExtractionResult(
            tasks_count=final_count,
            confidence="low"
        )
