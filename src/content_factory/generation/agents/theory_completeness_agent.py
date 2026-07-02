"""
TheoryCompletenessAgent - агент для проверки полноты теории по исходному проектному контексту.

Проверяет, что ключевые темы и инструменты из входного проектного материала отражены
в сгенерированной теории без переноса практических инструкций и правил сдачи.
"""

import logging
import re
from typing import Any

from ..config.loader import get_agent_config, prompt_trace_kwargs
from ..models.schemas import ProjectSeed
from ..recovery import ModelOutputNormalizer
from .base.agent import BaseAgent
from .base.llm_client import LLMClientProtocol

logger = logging.getLogger(__name__)


class TheoryCompletenessAgent(BaseAgent):
    """
    Агент для проверки полноты теории по проектному контексту.
    
    Работает в контуре улучшения/сопоставления проекта:
    1. Сравнивает темы и инструменты из исходного материала с теорией
    2. Определяет, что отсутствует
    3. Дополняет теорию только предметными темами без правил сдачи
    """

    CONFIG_NAME = "theory_completeness"

    def __init__(self, llm: LLMClientProtocol):
        super().__init__(llm)
        self.config = get_agent_config(self.CONFIG_NAME)
        self.llm_kwargs = self.config.llm.to_kwargs() if self.config.llm else {}
        self.output_normalizer = ModelOutputNormalizer()
        self.rx_part = re.compile(r"^###\s+2\.(\d+)\.\s*(.+?)\s*$", re.M)

    def check_and_enhance(
        self,
        theory_markdown: str,
        original_readme: str,
        extracted_topics: list[str],
        extracted_tools: list[str],
        seed: ProjectSeed,
        context_meta: Any | None = None
    ) -> tuple[str, list[str], list[str]]:
        """
        Проверяет полноту теории и дополняет её при необходимости.
        
        Args:
            theory_markdown: Сгенерированная теория в формате Markdown
            original_readme: Исходный проектный материал для сравнения
            extracted_topics: Список тем, извлеченных из исходного материала
            extracted_tools: Список инструментов, извлеченных из исходного материала
            seed: Входные данные проекта
            context_meta: Метаданные curriculum context (опционально)
            
        Returns:
            Tuple[enhanced_theory_markdown, warnings, issues]
        """
        logger.info("🔍 Проверка полноты теории по исходному проектному контексту...")

        # Извлекаем части теории
        parts = self._extract_theory_parts(theory_markdown)

        if not parts:
            logger.warning("⚠️ Не удалось извлечь части теории")
            return theory_markdown, ["Не удалось извлечь части теории"], []

        # Анализируем полноту
        missing_topics, missing_tools = self._analyze_completeness(
            theory_markdown,
            extracted_topics,
            extracted_tools
        )

        if not missing_topics and not missing_tools:
            logger.info("✅ Все темы и инструменты отражены в теории")
            return theory_markdown, [], []

        logger.info(
            f"⚠️ Обнаружены пропуски: {len(missing_topics)} тем, {len(missing_tools)} инструментов"
        )

        # Дополняем теорию
        enhanced_markdown, warnings, issues = self._enhance_theory(
            theory_markdown,
            parts,
            missing_topics,
            missing_tools,
            original_readme,
            seed,
            context_meta
        )

        return enhanced_markdown, warnings, issues

    def _extract_theory_parts(self, markdown: str) -> list[dict[str, Any]]:
        """Извлекает части теории из Markdown."""
        markdown = self.output_normalizer.normalize_theory_markdown(markdown).markdown
        parts = []
        matches = self.rx_part.finditer(markdown)

        for match in matches:
            part_num = int(match.group(1))
            part_title = match.group(2).strip()

            # Находим конец части (следующая часть или конец главы)
            start_pos = match.end()
            next_match = self.rx_part.search(markdown, start_pos)
            if next_match:
                end_pos = next_match.start()
            else:
                # Ищем конец главы (следующая глава или конец документа)
                chapter_end = re.search(r"^##\s+Глава\s+3", markdown[start_pos:], re.M)
                if chapter_end:
                    end_pos = start_pos + chapter_end.start()
                else:
                    end_pos = len(markdown)

            part_body = markdown[start_pos:end_pos].strip()

            parts.append({
                "number": part_num,
                "title": part_title,
                "body": part_body,
                "start_pos": match.start(),
                "end_pos": end_pos
            })

        return parts

    def _analyze_completeness(
        self,
        theory_markdown: str,
        extracted_topics: list[str],
        extracted_tools: list[str]
    ) -> tuple[list[str], list[str]]:
        """
        Анализирует полноту теории.
        
        Returns:
            Tuple[missing_topics, missing_tools]
        """
        theory_lower = theory_markdown.lower()

        # Проверяем темы
        missing_topics = []
        for topic in extracted_topics:
            if not topic:
                continue
            topic_lower = topic.lower()
            # Проверяем, упоминается ли тема в теории
            if topic_lower not in theory_lower:
                # Также проверяем частичное совпадение (ключевые слова)
                topic_words = topic_lower.split()
                if len(topic_words) > 1:
                    # Если тема состоит из нескольких слов, проверяем хотя бы часть
                    found = any(word in theory_lower for word in topic_words if len(word) > 3)
                    if not found:
                        missing_topics.append(topic)
                else:
                    missing_topics.append(topic)

        # Проверяем инструменты
        missing_tools = []
        for tool in extracted_tools:
            if not tool:
                continue
            tool_lower = tool.lower()
            if tool_lower not in theory_lower:
                # Для инструментов проверяем также варианты написания
                tool_variants = [
                    tool_lower,
                    tool_lower.replace(" ", ""),
                    tool_lower.replace("-", ""),
                    tool_lower.replace("_", "")
                ]
                found = any(variant in theory_lower for variant in tool_variants)
                if not found:
                    missing_tools.append(tool)

        return missing_topics, missing_tools

    def _enhance_theory(
        self,
        theory_markdown: str,
        parts: list[dict[str, Any]],
        missing_topics: list[str],
        missing_tools: list[str],
        original_readme: str,
        seed: ProjectSeed,
        context_meta: Any | None
    ) -> tuple[str, list[str], list[str]]:
        """
        Дополняет теорию недостающими темами и инструментами.
        
        Returns:
            Tuple[enhanced_markdown, warnings, issues]
        """
        warnings = []
        issues = []

        if not missing_topics and not missing_tools:
            return theory_markdown, warnings, issues

        # Формируем промпт для дополнения
        system_prompt = self.config.get_prompt("system").format(language=seed.language)

        missing_info = []
        if missing_topics:
            missing_info.append(f"Темы: {', '.join(missing_topics)}")
        if missing_tools:
            missing_info.append(f"Инструменты: {', '.join(missing_tools)}")

        user_prompt = self.config.get_prompt("user_template").format(
            theory_markdown=theory_markdown[:10000],  # Ограничиваем размер
            original_readme=original_readme[:5000],  # Контекст из исходного README
            missing_topics=", ".join(missing_topics) if missing_topics else "нет",
            missing_tools=", ".join(missing_tools) if missing_tools else "нет",
            missing_info="; ".join(missing_info),
            project_description=seed.project_description or "",
            skills=", ".join(seed.skills) if seed.skills else "не указаны",
            learning_outcomes=", ".join(seed.learning_outcomes) if seed.learning_outcomes else "не указаны"
        )

        try:
            # Получаем дополненную теорию от LLM
            llm_kwargs = self.llm_kwargs.copy()
            llm_kwargs.setdefault("temperature", 0.3)
            llm_kwargs.update(
                prompt_trace_kwargs(
                    self.config,
                    "system",
                    "user_template",
                    output_schema="enhanced_theory_markdown",
                )
            )

            response = self.llm.complete(
                system=system_prompt,
                user=user_prompt,
                **llm_kwargs
            )

            enhanced_markdown = response.strip()

            # Проверяем, что дополнение прошло успешно
            if len(enhanced_markdown) < len(theory_markdown):
                warnings.append("⚠️ Дополненная теория короче исходной, возможно, произошла ошибка")
                return theory_markdown, warnings, issues

            logger.info(f"✅ Теория дополнена: {len(enhanced_markdown)} символов (было {len(theory_markdown)})")

            return enhanced_markdown, warnings, issues

        except Exception as e:
            logger.error(f"❌ Ошибка при дополнении теории: {e}", exc_info=True)
            issues.append(f"Ошибка при дополнении теории: {str(e)}")
            return theory_markdown, warnings, issues
