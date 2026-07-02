"""Тесты для Chapter1Checker."""

import re

import pytest

from content_gen.models.criteria_models import CheckMethod
from content_gen.validators.rubric.chapter1_checker import Chapter1Checker


class TestChapter1Checker:
    """Тесты для проверки Главы 1."""

    @pytest.fixture
    def regex_patterns(self):
        """Фикстура с регулярными выражениями."""
        return {
            "rx_h3": re.compile(r"^###\s+(.+)$", re.M),
            "rx_directives": [
                re.compile(r"нажми|кликни|перейди|введи|скачай|открой|выбери|запусти", re.I)
            ]
        }

    def test_init(self, mock_llm_client, regex_patterns):
        """Тест инициализации checker'а."""
        checker = Chapter1Checker(
            llm_client=mock_llm_client,
            language="ru",
            regex_patterns=regex_patterns
        )
        assert checker.llm == mock_llm_client
        assert checker.lang == "ru"
        assert checker.rx_h3 is not None

    def test_check_empty_chapter1(self, mock_llm_client, regex_patterns):
        """Тест проверки пустой Главы 1."""
        checker = Chapter1Checker(
            llm_client=mock_llm_client,
            regex_patterns=regex_patterns
        )
        results = checker.check("")

        assert len(results) > 0
        # Все критерии должны провалиться
        assert all(item.score == 0 for item in results)
        # Должны быть все подкритерии 2.3.1-2.3.7
        sub_ids = [item.id for item in results]
        assert "2.3.1" in sub_ids or any("2.3" in item.id for item in results)

    def test_check_valid_chapter1(self, mock_llm_client, regex_patterns, sample_markdown):
        """Тест проверки валидной Главы 1."""
        checker = Chapter1Checker(
            llm_client=mock_llm_client,
            regex_patterns=regex_patterns
        )

        # Извлекаем Главу 1 из sample_markdown
        ch1_match = re.search(r"## Глава 1\s+(.*?)(?=## Глава 2|$)", sample_markdown, re.DOTALL)
        ch1_content = ch1_match.group(1) if ch1_match else ""

        results = checker.check(ch1_content)

        assert len(results) > 0
        # Проверяем структуру результатов
        for item in results:
            assert hasattr(item, 'id')
            assert hasattr(item, 'title')
            assert hasattr(item, 'score')
            assert hasattr(item, 'check_method')

    def test_check_chapter1_structure(self, mock_llm_client, regex_patterns):
        """Тест проверки структуры Главы 1."""
        checker = Chapter1Checker(
            llm_client=mock_llm_client,
            regex_patterns=regex_patterns
        )

        # Глава 1 с правильной структурой
        valid_ch1 = """### Введение

Введение к проекту.

### Инструкция

Инструкция по выполнению проекта."""

        results = checker.check(valid_ch1)

        # Должен быть критерий 2.3.1 (структура)
        structure_item = next((item for item in results if "2.3.1" in item.id), None)
        if structure_item:
            assert structure_item.check_method == CheckMethod.AI_AGENT

    def test_check_chapter1_directives(self, mock_llm_client, regex_patterns):
        """Тест проверки директив в Главе 1."""
        checker = Chapter1Checker(
            llm_client=mock_llm_client,
            regex_patterns=regex_patterns
        )

        # Глава 1 с директивами (запрещено)
        bad_ch1 = """### Инструкция

Нажми кнопку и запусти программу."""

        results = checker.check(bad_ch1)

        # Должен быть критерий 2.3.6 (автономность)
        autonomy_item = next((item for item in results if "2.3.6" in item.id), None)
        if autonomy_item:
            # Если найдены директивы, score должен быть 0
            assert autonomy_item.score == 0

    def test_contextual_constraints_script_detects_static_instruction(self, regex_patterns):
        checker = Chapter1Checker(
            llm_client=None,
            regex_patterns=regex_patterns
        )

        instruction = """
        Эта инструкция задаёт общие правила работы с проектом и не описывает конкретные шаги.

        Контекст и ограничения проекта
        Требования к окружению: проект выполняется в среде обучения, допускается черновая работа локально.
        Исходные данные: на старте есть доступ к репозиторию с материалами.
        Структура артефактов: результаты обязательно размещаются в структуре проекта.
        Правила сдачи и проверки: проверка выполняется через P2P по чек-листу.
        """

        assert checker._has_contextual_constraints_script(instruction) is True
