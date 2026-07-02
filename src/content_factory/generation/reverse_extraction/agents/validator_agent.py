"""
ValidatorAgent - валидация данных перед записью в Excel.

Проверяет критичные поля, длину, формат и выполняет автоматические правки.
"""

import re
from typing import Any

from ..models import ValidationResult


class ValidatorAgent:
    """Агент для валидации данных перед записью в Excel."""

    MAX_TITLE_LENGTH = 120
    MIN_LEARNING_OUTCOMES = 2
    MAX_DESCRIPTION_SENTENCES = 5

    def validate(self, mapping: dict[str, Any]) -> ValidationResult:
        """
        Валидирует mapping и выполняет автоматические правки.
        
        Args:
            mapping: Словарь с данными для Excel
            
        Returns:
            ValidationResult с результатами валидации и исправленным mapping
        """
        warnings = []
        errors = []
        corrected_mapping = mapping.copy()

        # Проверка критичных полей
        if not corrected_mapping.get("title_seed"):
            errors.append("title_seed не может быть пустым")
        elif len(corrected_mapping["title_seed"]) > self.MAX_TITLE_LENGTH:
            # Автоматическое укорочение
            corrected_mapping["title_seed"] = corrected_mapping["title_seed"][:self.MAX_TITLE_LENGTH].rstrip()
            warnings.append(f"title_seed был обрезан до {self.MAX_TITLE_LENGTH} символов")

        if not corrected_mapping.get("project_description"):
            errors.append("project_description не может быть пустым")
        else:
            # Проверка длины описания (количество предложений)
            sentences = re.split(r'[.!?]+', corrected_mapping["project_description"])
            sentences = [s.strip() for s in sentences if s.strip()]
            if len(sentences) > self.MAX_DESCRIPTION_SENTENCES:
                # Берем первые N предложений
                corrected_mapping["project_description"] = ". ".join(
                    sentences[:self.MAX_DESCRIPTION_SENTENCES]
                ) + "."
                warnings.append(
                    f"project_description был сокращен до {self.MAX_DESCRIPTION_SENTENCES} предложений"
                )

        # Проверка learning_outcomes
        lo_text = corrected_mapping.get("learning_outcomes", "")
        if lo_text:
            lo_list = [lo.strip() for lo in lo_text.split("\n") if lo.strip()]
            if len(lo_list) < self.MIN_LEARNING_OUTCOMES:
                warnings.append(
                    f"learning_outcomes содержит менее {self.MIN_LEARNING_OUTCOMES} пунктов"
                )
            corrected_mapping["learning_outcomes"] = "\n".join(lo_list)
        else:
            errors.append("learning_outcomes не может быть пустым")

        # Проверка skills
        skills_text = corrected_mapping.get("skills", "")
        if skills_text:
            skills_list = [s.strip() for s in skills_text.split("\n") if s.strip()]
            if not skills_list:
                errors.append("skills не может быть пустым")
            corrected_mapping["skills"] = "\n".join(skills_list)
        else:
            errors.append("skills не может быть пустым")

        # Проверка required_tools (если в тексте явно есть инструменты, но поле пустое)
        if not corrected_mapping.get("required_tools"):
            warnings.append("required_tools пустое (возможно, инструменты не были извлечены)")

        # Проверка thematic_block
        if not corrected_mapping.get("thematic_block"):
            warnings.append("thematic_block не определен (может быть предложен новый блок)")

        # Проверка на HTML/markdown мусор
        html_pattern = re.compile(r'<[^>]+>')
        for key, value in corrected_mapping.items():
            if isinstance(value, str) and html_pattern.search(value):
                # Удаляем HTML теги
                corrected_mapping[key] = html_pattern.sub('', value)
                warnings.append(f"Удалены HTML теги из поля {key}")

        # Проверка формата списков (должны быть через \n, а не через запятую)
        for list_field in ["learning_outcomes", "skills"]:
            value = corrected_mapping.get(list_field, "")
            if value and "," in value and "\n" not in value:
                # Преобразуем запятые в переносы строк
                corrected_mapping[list_field] = "\n".join([
                    item.strip() for item in value.split(",") if item.strip()
                ])
                warnings.append(f"{list_field} преобразован из запятых в переносы строк")

        is_valid = len(errors) == 0

        return ValidationResult(
            is_valid=is_valid,
            mapping=corrected_mapping,
            warnings=warnings,
            errors=errors
        )

