"""
MapperAgent - преобразование PartialProjectSeed + ClassificationResult в формат Excel.

Маппит данные согласно схеме Excel из utils/excel_io.py.
"""

from typing import Any

from ..models import ClassificationResult, PartialProjectSeed


class MapperAgent:
    """Агент для маппинга данных в формат Excel."""

    def map_to_excel(
        self,
        partial_seed: PartialProjectSeed,
        classification: ClassificationResult
    ) -> dict[str, Any]:
        """
        Преобразует данные в словарь для заполнения Excel.
        
        Args:
            partial_seed: Частично заполненный seed
            classification: Результат классификации
            
        Returns:
            Словарь с данными для Excel (ключи соответствуют колонке "Параметр")
        """
        mapping = {}

        # Базовые поля
        mapping["language"] = classification.language
        mapping["project_type"] = classification.project_type or "individual"

        # Thematic block - приоритет у предложенного нового блока
        mapping["thematic_block"] = (
            classification.thematic_block_suggested or
            classification.thematic_block or
            ""
        )

        # Audience level
        mapping["audience_level"] = classification.audience_level or "base"

        # Required tools - через запятую
        if partial_seed.required_tools:
            mapping["required_tools"] = ", ".join(partial_seed.required_tools)
        else:
            mapping["required_tools"] = ""

        # Title и описание
        mapping["title_seed"] = partial_seed.title_seed or ""
        mapping["project_description"] = partial_seed.project_description or ""

        # Learning outcomes - каждый с новой строки
        if partial_seed.learning_outcomes:
            mapping["learning_outcomes"] = "\n".join(partial_seed.learning_outcomes)
        else:
            mapping["learning_outcomes"] = ""

        # Skills - каждый с новой строки
        if partial_seed.skills:
            mapping["skills"] = "\n".join(partial_seed.skills)
        else:
            mapping["skills"] = ""

        # Опциональные поля (оставляем пустыми, так как их нет в извлеченных данных)
        mapping["group_size"] = ""  # Определяется только для group проектов
        mapping["bonus_wish"] = ""
        mapping["repo_base_url"] = ""
        mapping["repo_path_template"] = ""

        return mapping
