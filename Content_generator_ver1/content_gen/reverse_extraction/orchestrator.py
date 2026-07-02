"""
Orchestrator - координация всех агентов обратного извлечения.

Выполняет полный пайплайн: нормализация → извлечение → классификация → маппинг → валидация → Excel.
"""

import io
import logging
from typing import Any

from ..agents.base.llm_client import LLMClientProtocol
from .agents import ClassifierAgent, InputAgent, MapperAgent, StructureExtractor, TasksExtractor, ValidatorAgent
from .excel_writer import ExcelWriterTool
from .models import ClassificationResult, NormalizedReadme, PartialProjectSeed

logger = logging.getLogger(__name__)


class ReverseExtractionOrchestrator:
    """Оркестратор для обратного извлечения данных из README."""

    def __init__(self, llm: LLMClientProtocol):
        """
        Инициализация оркестратора.
        
        Args:
            llm: LLM клиент для агентов
        """
        self.llm = llm

        # Инициализируем агенты
        self.input_agent = InputAgent()
        self.structure_extractor = StructureExtractor(llm)
        self.tasks_extractor = TasksExtractor(llm)
        self.classifier_agent = ClassifierAgent(llm)
        self.mapper_agent = MapperAgent()
        self.validator_agent = ValidatorAgent()
        self.excel_writer = ExcelWriterTool()

    def extract_from_readme(
        self,
        readme_text: str
    ) -> tuple[io.BytesIO, dict[str, Any]]:
        """
        Выполняет полный пайплайн извлечения данных из README.
        
        Args:
            readme_text: Текст README.md
            
        Returns:
            Tuple[Excel файл (BytesIO), метаданные]
            
        Raises:
            Exception: Если произошла критичная ошибка на каком-то этапе
        """
        metadata = {
            "warnings": [],
            "errors": [],
            "extracted_fields": {}
        }

        try:
            # Шаг 1: Нормализация README
            logger.info("Шаг 1: Нормализация README")
            normalized_readme = self.input_agent.normalize(readme_text)
            logger.info(f"Нормализовано: {len(normalized_readme.raw_text)} символов, {len(normalized_readme.chapters)} глав")

            # Шаг 2: Извлечение структуры
            logger.info("Шаг 2: Извлечение структурированных данных")
            partial_seed = self.structure_extractor.extract(normalized_readme)

            # Шаг 2.5: Детальное извлечение задач (уточнение подсчета)
            logger.info("Шаг 2.5: Детальное извлечение задач")
            tasks_result = self.tasks_extractor.extract_tasks(
                normalized_readme,
                initial_count=partial_seed.tasks_count
            )
            # Обновляем tasks_count более точным значением
            if tasks_result.tasks_count is not None and tasks_result.confidence in ["high", "medium"]:
                partial_seed.tasks_count = tasks_result.tasks_count
                logger.info(f"Количество задач уточнено: {tasks_result.tasks_count} (уверенность: {tasks_result.confidence})")

            metadata["extracted_fields"]["partial_seed"] = {
                "title_seed": partial_seed.title_seed,
                "tasks_count": partial_seed.tasks_count,
                "tasks_confidence": tasks_result.confidence,
                "skills_count": len(partial_seed.skills),
                "learning_outcomes_count": len(partial_seed.learning_outcomes),
                "required_tools_count": len(partial_seed.required_tools)
            }

            # Шаг 3: Классификация
            logger.info("Шаг 3: Классификация метаданных")
            classification = self.classifier_agent.classify(partial_seed, normalized_readme)

            # Приоритет: если предложен новый блок, используем его, иначе найденный
            final_thematic_block = (
                classification.thematic_block_suggested or
                classification.thematic_block or
                ""
            )

            metadata["extracted_fields"]["classification"] = {
                "language": classification.language,
                "thematic_block": classification.thematic_block,
                "thematic_block_suggested": classification.thematic_block_suggested,
                "thematic_block_name": classification.thematic_block_name,
                "final_thematic_block": final_thematic_block,  # Финальный блок для отображения
                "audience_level": classification.audience_level,
                "project_type": classification.project_type
            }

            # Если предложен новый блок, добавляем предупреждение
            if classification.thematic_block_suggested:
                metadata["warnings"].append(
                    f"Предложен новый тематический блок: {classification.thematic_block_suggested} "
                    f"({classification.thematic_block_name})"
                )

            # Шаг 4: Маппинг в Excel формат
            logger.info("Шаг 4: Маппинг данных в формат Excel")
            mapping = self.mapper_agent.map_to_excel(partial_seed, classification)

            # Шаг 5: Валидация
            logger.info("Шаг 5: Валидация данных")
            validation_result = self.validator_agent.validate(mapping)

            # Добавляем предупреждения и ошибки из валидации
            metadata["warnings"].extend(validation_result.warnings)
            metadata["errors"].extend(validation_result.errors)

            if not validation_result.is_valid:
                logger.warning(f"Валидация не пройдена: {validation_result.errors}")
                # Продолжаем работу, но с предупреждением

            # Шаг 6: Заполнение Excel
            logger.info("Шаг 6: Заполнение Excel шаблона")
            excel_buffer = self.excel_writer.fill_excel_template(validation_result.mapping)

            metadata["extracted_fields"]["final_mapping"] = validation_result.mapping
            metadata["status"] = "completed"

            logger.info("Обратное извлечение завершено успешно")

            return excel_buffer, metadata

        except Exception as e:
            logger.error(f"Ошибка при обратном извлечении: {e}", exc_info=True)
            metadata["errors"].append(f"Критичная ошибка: {str(e)}")
            metadata["status"] = "error"
            raise

    def extract_data_only(
        self,
        readme_text: str
    ) -> tuple[PartialProjectSeed, ClassificationResult, NormalizedReadme, dict[str, Any]]:
        """
        Извлекает только данные из README без создания Excel файла.
        
        Используется для улучшения README, где нужны только извлеченные данные
        для последующей генерации.
        
        Args:
            readme_text: Текст README.md
            
        Returns:
            Tuple[PartialProjectSeed, ClassificationResult, NormalizedReadme, metadata]
            
        Raises:
            Exception: Если произошла критичная ошибка на каком-то этапе
        """
        metadata = {
            "warnings": [],
            "errors": [],
            "extracted_fields": {}
        }

        try:
            # Шаг 1: Нормализация README
            logger.info("Шаг 1: Нормализация README")
            normalized_readme = self.input_agent.normalize(readme_text)
            logger.info(f"Нормализовано: {len(normalized_readme.raw_text)} символов, {len(normalized_readme.chapters)} глав")

            # Шаг 2: Извлечение структуры
            logger.info("Шаг 2: Извлечение структурированных данных")
            partial_seed = self.structure_extractor.extract(normalized_readme)

            # Шаг 2.5: Детальное извлечение задач (уточнение подсчета)
            logger.info("Шаг 2.5: Детальное извлечение задач")
            tasks_result = self.tasks_extractor.extract_tasks(
                normalized_readme,
                initial_count=partial_seed.tasks_count
            )
            # Обновляем tasks_count более точным значением
            if tasks_result.tasks_count is not None and tasks_result.confidence in ["high", "medium"]:
                partial_seed.tasks_count = tasks_result.tasks_count
                logger.info(f"Количество задач уточнено: {tasks_result.tasks_count} (уверенность: {tasks_result.confidence})")

            metadata["extracted_fields"]["partial_seed"] = {
                "title_seed": partial_seed.title_seed,
                "tasks_count": partial_seed.tasks_count,
                "tasks_confidence": tasks_result.confidence,
                "skills_count": len(partial_seed.skills),
                "learning_outcomes_count": len(partial_seed.learning_outcomes),
                "required_tools_count": len(partial_seed.required_tools)
            }

            # Шаг 3: Классификация
            logger.info("Шаг 3: Классификация метаданных")
            classification = self.classifier_agent.classify(partial_seed, normalized_readme)

            # Приоритет: если предложен новый блок, используем его, иначе найденный
            final_thematic_block = (
                classification.thematic_block_suggested or
                classification.thematic_block or
                ""
            )

            metadata["extracted_fields"]["classification"] = {
                "language": classification.language,
                "thematic_block": classification.thematic_block,
                "thematic_block_suggested": classification.thematic_block_suggested,
                "thematic_block_name": classification.thematic_block_name,
                "final_thematic_block": final_thematic_block,
                "audience_level": classification.audience_level,
                "project_type": classification.project_type
            }

            # Если предложен новый блок, добавляем предупреждение
            if classification.thematic_block_suggested:
                metadata["warnings"].append(
                    f"Предложен новый тематический блок: {classification.thematic_block_suggested} "
                    f"({classification.thematic_block_name})"
                )

            metadata["status"] = "completed"

            logger.info("Извлечение данных завершено успешно (без Excel)")

            return partial_seed, classification, normalized_readme, metadata

        except Exception as e:
            logger.error(f"Ошибка при извлечении данных: {e}", exc_info=True)
            metadata["errors"].append(f"Критичная ошибка: {str(e)}")
            metadata["status"] = "error"
            raise

