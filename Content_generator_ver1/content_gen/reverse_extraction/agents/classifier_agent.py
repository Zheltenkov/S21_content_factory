"""
ClassifierAgent - определение метаданных проекта.

Сначала проверяет существующие thematic_blocks, затем использует LLM если не найдено.
"""

import json
import logging
from pathlib import Path

from ...config.loader import get_agent_config, prompt_trace_kwargs
from ...agents.base.llm_client import LLMClientProtocol
from ...llm.structured_output import StructuredLLMClient
from ..models import ClassificationResult, NormalizedReadme, PartialProjectSeed

logger = logging.getLogger(__name__)


class ClassifierAgent:
    """Агент для классификации метаданных проекта."""

    CONFIG_NAME = "classifier"

    def __init__(self, llm: LLMClientProtocol):
        """
        Инициализация классификатора.
        
        Args:
            llm: LLM клиент для классификации
        """
        self.llm = llm
        self.structured_client = StructuredLLMClient(llm)
        self.config = get_agent_config(self.CONFIG_NAME)
        self.llm_kwargs = self.config.llm.to_kwargs() if self.config.llm else {}
        self._thematic_blocks = self._load_thematic_blocks()

    def _load_thematic_blocks(self) -> dict:
        """Загружает существующие тематические блоки."""
        # Пытаемся загрузить из thematic_blocks.json
        blocks_file = Path("thematic_blocks.json")
        if blocks_file.exists():
            try:
                with open(blocks_file, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Не удалось загрузить thematic_blocks.json: {e}")

        # Возвращаем значения по умолчанию
        return {
            "Бизнес аналитика": "BSA",
            "Кибербезопасность": "Cb",
            "DevOps": "DO",
            "Проектный менеджмент": "PjM",
            "Тестирование и обеспечение качества": "QA",
            "Машинное обучение": "DS"
        }

    def _find_thematic_block_by_keywords(
        self,
        text: str,
        partial_seed: PartialProjectSeed
    ) -> str | None:
        """
        Ищет тематический блок по ключевым словам в тексте.
        
        Args:
            text: Текст README
            partial_seed: Частично заполненный seed
            
        Returns:
            Кодовое обозначение блока или None
        """
        text_lower = text.lower()

        # Ключевые слова для каждого блока
        keywords_map = {
            "BSA": ["бизнес аналитика", "business analysis", "требования", "requirements", "use case", "user story"],
            "Cb": ["кибербезопасность", "cybersecurity", "безопасность", "security", "пароль", "password", "шифрование", "encryption", "сеть", "network"],
            "DO": ["devops", "ci/cd", "docker", "kubernetes", "k8s", "deployment", "развертывание", "мониторинг", "monitoring"],
            "PjM": ["проектный менеджмент", "project management", "управление проектом", "scrum", "agile", "backlog", "sprint"],
            "QA": ["тестирование", "testing", "qa", "quality assurance", "тест", "test", "баг", "bug", "test case"],
            "DS": ["машинное обучение", "machine learning", "ml", "data science", "нейросеть", "neural network", "анализ данных"]
        }

        # Проверяем каждый блок
        for block_code, keywords in keywords_map.items():
            for keyword in keywords:
                if keyword in text_lower:
                    logger.info(f"Найден тематический блок {block_code} по ключевому слову '{keyword}'")
                    return block_code

        # Дополнительная проверка по навыкам и инструментам
        all_text = " ".join([
            text_lower,
            " ".join(partial_seed.skills).lower(),
            " ".join(partial_seed.required_tools).lower()
        ])

        for block_code, keywords in keywords_map.items():
            for keyword in keywords:
                if keyword in all_text:
                    logger.info(f"Найден тематический блок {block_code} по навыкам/инструментам")
                    return block_code

        return None

    def classify(
        self,
        partial_seed: PartialProjectSeed,
        normalized_readme: NormalizedReadme
    ) -> ClassificationResult:
        """
        Классифицирует метаданные проекта.
        
        Args:
            partial_seed: Частично заполненный seed
            normalized_readme: Нормализованный README
            
        Returns:
            ClassificationResult с метаданными
        """
        # Сначала пытаемся найти thematic_block по ключевым словам
        found_block = self._find_thematic_block_by_keywords(
            normalized_readme.raw_text,
            partial_seed
        )

        # Формируем список существующих блоков для промпта
        existing_blocks_list = ", ".join([
            f"{name} ({code})" for name, code in self._thematic_blocks.items()
        ])
        existing_blocks_json = json.dumps(self._thematic_blocks, ensure_ascii=False, indent=2)

        # Формируем промпты
        system_prompt = self.config.get_prompt("system").format(
            existing_blocks=existing_blocks_json
        )
        user_prompt = self.config.get_prompt("user_template").format(
            title_seed=partial_seed.title_seed or "—",
            project_description=partial_seed.project_description or "—",
            skills=", ".join(partial_seed.skills) if partial_seed.skills else "—",
            required_tools=", ".join(partial_seed.required_tools) if partial_seed.required_tools else "—",
            learning_outcomes="\n".join(partial_seed.learning_outcomes) if partial_seed.learning_outcomes else "—",
            readme_text=normalized_readme.raw_text[:5000],  # Ограничиваем размер
            existing_blocks_list=existing_blocks_list
        )

        # Получаем классификацию через structured output
        llm_kwargs = self.llm_kwargs.copy()
        llm_kwargs.setdefault("temperature", 0.1)
        llm_kwargs.update(
            prompt_trace_kwargs(
                self.config,
                "system",
                "user_template",
                output_schema="ClassificationResult",
            )
        )

        try:
            result = self.structured_client.complete_structured(
                output_model=ClassificationResult,
                system=system_prompt,
                user=user_prompt,
                **llm_kwargs
            )

            # Если нашли блок по ключевым словам, используем его
            if found_block:
                result.thematic_block = found_block

            # Создаем обратный словарь: код -> название для быстрого поиска
            code_to_name = {code: name for name, code in self._thematic_blocks.items()}
            name_to_code = self._thematic_blocks  # название -> код

            # Проверяем thematic_block_suggested - если он уже есть в словаре, используем его
            if result.thematic_block_suggested:
                suggested_code = result.thematic_block_suggested.upper()
                # Проверяем по коду
                if suggested_code in code_to_name:
                    # Блок уже существует, используем его вместо предложения нового
                    result.thematic_block = suggested_code
                    result.thematic_block_suggested = None
                    result.thematic_block_name = None
                    logger.info(f"Предложенный блок {suggested_code} уже существует, используем его")
                # Проверяем по названию (если thematic_block_name указан)
                elif result.thematic_block_name and result.thematic_block_name in name_to_code:
                    existing_code = name_to_code[result.thematic_block_name]
                    result.thematic_block = existing_code
                    result.thematic_block_suggested = None
                    result.thematic_block_name = None
                    logger.info(f"Предложенное название '{result.thematic_block_name}' уже существует как {existing_code}, используем его")

            # Проверяем thematic_block - если он есть в словаре, используем его
            if result.thematic_block:
                block_code = result.thematic_block.upper()
                if block_code in code_to_name:
                    # Блок существует, все хорошо
                    logger.info(f"Используем существующий блок {block_code} ({code_to_name[block_code]})")
                else:
                    # Блок не найден, проверяем по названию
                    # Ищем похожее название в словаре
                    for name, code in name_to_code.items():
                        if block_code.lower() in name.lower() or name.lower() in block_code.lower():
                            result.thematic_block = code
                            logger.info(f"Найден похожий блок: {name} ({code}), используем его")
                            break

            logger.info(
                f"ClassifierAgent: language={result.language}, "
                f"thematic_block={result.thematic_block}, "
                f"thematic_block_suggested={result.thematic_block_suggested}, "
                f"audience_level={result.audience_level}"
            )

            return result

        except Exception as e:
            logger.error(f"Ошибка при классификации: {e}")
            # Fallback: возвращаем базовую классификацию
            return self._fallback_classification(found_block, normalized_readme)

    def _fallback_classification(
        self,
        found_block: str | None,
        normalized_readme: NormalizedReadme
    ) -> ClassificationResult:
        """Fallback метод для классификации без LLM."""
        # Определяем язык по тексту
        text_lower = normalized_readme.raw_text.lower()
        language = "ru"  # По умолчанию
        if any(word in text_lower for word in ["the", "is", "are", "and", "or"]):
            # Простая эвристика для английского
            if len([w for w in ["the", "is", "are", "and"] if w in text_lower]) >= 3:
                language = "en"

        # Определяем уровень аудитории по ключевым словам
        audience_level = None
        if any(word in text_lower for word in ["базовый", "начальный", "basic", "beginner", "введение"]):
            audience_level = "Начальный"
        elif any(word in text_lower for word in ["продвинутый", "advanced", "эксперт", "expert"]):
            audience_level = "Продвинутый"

        # Определяем тип проекта
        project_type = None
        if any(word in text_lower for word in ["групповой", "командный", "group", "team"]):
            project_type = "group"
        else:
            project_type = "individual"  # По умолчанию

        logger.warning(
            f"Fallback classification: language={language}, "
            f"thematic_block={found_block}, audience_level={audience_level}"
        )

        return ClassificationResult(
            language=language,
            thematic_block=found_block,
            thematic_block_suggested=None,
            thematic_block_name=None,
            audience_level=audience_level,
            project_type=project_type
        )
