"""
StructureExtractor - извлечение структурированных данных из README.

Использует StructuredLLMClient для гарантированного парсинга.
"""

import logging

from ...config.loader import get_agent_config, prompt_trace_kwargs
from ...agents.base.llm_client import LLMClientProtocol
from ...llm.structured_output import StructuredLLMClient
from ..models import NormalizedReadme, PartialProjectSeed

logger = logging.getLogger(__name__)


class StructureExtractor:
    """Агент для извлечения структурированных данных из README."""

    CONFIG_NAME = "structure_extractor"

    def __init__(self, llm: LLMClientProtocol):
        """
        Инициализация экстрактора.
        
        Args:
            llm: LLM клиент для извлечения
        """
        self.llm = llm
        self.structured_client = StructuredLLMClient(llm)
        self.config = get_agent_config(self.CONFIG_NAME)
        self.llm_kwargs = self.config.llm.to_kwargs() if self.config.llm else {}

    def extract(self, normalized_readme: NormalizedReadme) -> PartialProjectSeed:
        """
        Извлекает структурированные данные из нормализованного README.
        
        Args:
            normalized_readme: Нормализованный README
            
        Returns:
            PartialProjectSeed с извлеченными данными
        """
        # Формируем промпты
        system_prompt = self.config.get_prompt("system")
        # Увеличиваем лимит до 20000 символов для лучшего извлечения информации
        # Берем весь текст, но если он слишком длинный, берем начало и конец
        readme_text = normalized_readme.raw_text
        if len(readme_text) > 20000:
            # Берем первые 15000 и последние 5000 символов
            readme_text = readme_text[:15000] + "\n\n[... текст пропущен ...]\n\n" + readme_text[-5000:]
            logger.info(f"README слишком длинный ({len(normalized_readme.raw_text)} символов), используем сокращенную версию")
        user_prompt = self.config.get_prompt("user_template").format(
            readme_text=readme_text
        )

        # Получаем извлеченные данные через structured output
        llm_kwargs = self.llm_kwargs.copy()
        llm_kwargs.setdefault("temperature", 0.1)
        llm_kwargs.update(
            prompt_trace_kwargs(
                self.config,
                "system",
                "user_template",
                output_schema="PartialProjectSeed",
            )
        )

        try:
            result = self.structured_client.complete_structured(
                output_model=PartialProjectSeed,
                system=system_prompt,
                user=user_prompt,
                **llm_kwargs
            )

            logger.info(
                f"StructureExtractor: извлечено title='{result.title_seed}', "
                f"tasks_count={result.tasks_count}, skills={len(result.skills)}"
            )

            return result

        except Exception as e:
            logger.error(f"Ошибка при извлечении структуры: {e}")
            # Возвращаем частично заполненный результат с базовыми данными
            return self._fallback_extraction(normalized_readme)

    def _fallback_extraction(self, normalized_readme: NormalizedReadme) -> PartialProjectSeed:
        """
        Fallback метод для извлечения базовых данных без LLM.
        
        Пытается извлечь хотя бы минимальную информацию из структуры README.
        """
        import re

        # Извлекаем title из первого заголовка
        title = None
        if normalized_readme.structure.get("headings"):
            first_heading = normalized_readme.structure["headings"][0]
            if first_heading["level"] <= 2:
                title = first_heading["title"]

        # Извлекаем описание из первой главы или введения
        description = None
        if 1 in normalized_readme.chapters:
            chapter1 = normalized_readme.chapters[1]
            # Берем первые 2-3 предложения
            sentences = re.split(r'[.!?]+', chapter1)
            sentences = [s.strip() for s in sentences if s.strip()][:3]
            if sentences:
                description = ". ".join(sentences) + "."

        # Извлекаем learning_outcomes из главы 1 или 2
        learning_outcomes = []
        for chapter_num in [1, 2]:
            if chapter_num in normalized_readme.chapters:
                chapter_text = normalized_readme.chapters[chapter_num]
                # Ищем паттерны типа "После изучения...", "В результате...", "Студент научится..."
                lo_patterns = [
                    r'(?:После изучения|В результате|Студент научится|Студент сможет|Цель|Цели)[:.]?\s*([^.!?]+[.!?])',
                    r'[-•]\s*([А-ЯЁ][^.!?]+[.!?])',  # Маркированные списки с заглавной буквы
                ]
                for pattern in lo_patterns:
                    matches = re.findall(pattern, chapter_text, re.IGNORECASE | re.MULTILINE)
                    learning_outcomes.extend([m.strip() for m in matches if len(m.strip()) > 10])
                    if len(learning_outcomes) >= 3:
                        break
                if learning_outcomes:
                    break

        # Извлекаем инструменты из текста (поиск упоминаний технологий)
        required_tools = []
        tools_keywords = [
            'python', 'java', 'javascript', 'typescript', 'go', 'rust', 'c++', 'c#',
            'docker', 'kubernetes', 'git', 'postgresql', 'mysql', 'mongodb', 'redis',
            'react', 'vue', 'angular', 'django', 'flask', 'fastapi', 'spring',
            'pandas', 'numpy', 'tensorflow', 'pytorch', 'scikit-learn'
        ]
        text_lower = normalized_readme.raw_text.lower()
        for tool in tools_keywords:
            if tool in text_lower:
                required_tools.append(tool.capitalize())

        # Извлекаем навыки (ищем упоминания навыков)
        skills = []
        skills_keywords = [
            'программирование', 'разработка', 'тестирование', 'деплой', 'развертывание',
            'база данных', 'api', 'frontend', 'backend', 'алгоритмы', 'структуры данных',
            'git', 'ci/cd', 'контейнеризация', 'мониторинг', 'логирование'
        ]
        for skill in skills_keywords:
            if skill in text_lower:
                skills.append(skill.capitalize())

        # Подсчитываем задачи из главы 3
        tasks_count = None
        if 3 in normalized_readme.chapters:
            chapter3 = normalized_readme.chapters[3]
            # Простой подсчет по паттерну "Задача", "Task", "Задание"
            task_pattern = re.compile(r'(?:Задача|Task|Задание)\s*\d+', re.IGNORECASE)
            tasks_count = len(task_pattern.findall(chapter3))

        logger.warning(
            f"Fallback extraction: title={title}, description={bool(description)}, "
            f"LO={len(learning_outcomes)}, tools={len(required_tools)}, skills={len(skills)}"
        )

        return PartialProjectSeed(
            title_seed=title,
            project_description=description,
            learning_outcomes=learning_outcomes[:5],  # Ограничиваем до 5
            required_tools=required_tools[:10],  # Ограничиваем до 10
            skills=skills[:10],  # Ограничиваем до 10
            tasks_count=tasks_count,
            theory_parts=[]
        )
