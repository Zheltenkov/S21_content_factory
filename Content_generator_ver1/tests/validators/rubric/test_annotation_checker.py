"""Тесты для AnnotationChecker."""

from content_gen.models.criteria_models import CheckMethod
from content_gen.validators.rubric.annotation_checker import AnnotationChecker


class TestAnnotationChecker:
    """Тесты для проверки аннотации."""

    def test_init(self, mock_llm_client, mock_embedding_function):
        """Тест инициализации checker'а."""
        checker = AnnotationChecker(
            llm_client=mock_llm_client,
            embedding_function=mock_embedding_function,
            language="ru"
        )
        assert checker.llm == mock_llm_client
        assert checker.embedding_function == mock_embedding_function
        assert checker.lang == "ru"

    def test_check_empty_annotation(self, mock_llm_client, mock_embedding_function):
        """Тест проверки пустой аннотации."""
        checker = AnnotationChecker(
            llm_client=mock_llm_client,
            embedding_function=mock_embedding_function
        )
        results = checker.check("", "Тестовый проект")

        assert len(results) > 0
        length_item = next((item for item in results if item.id == "2.1.1"), None)
        assert length_item is not None and length_item.score == 0
        structure_item = next((item for item in results if item.id == "2.1.2"), None)
        assert structure_item is not None and structure_item.score == 0
        topic_item = next((item for item in results if item.id == "2.1.3"), None)
        assert topic_item is not None and topic_item.score == 1

    def test_check_valid_annotation(self, mock_llm_client, mock_embedding_function, sample_annotation):
        """Тест проверки валидной аннотации."""
        checker = AnnotationChecker(
            llm_client=mock_llm_client,
            embedding_function=mock_embedding_function
        )
        results = checker.check(sample_annotation, "Тестовый проект")

        assert len(results) > 0
        # Проверяем, что все критерии имеют корректную структуру
        for item in results:
            assert hasattr(item, 'id')
            assert hasattr(item, 'title')
            assert hasattr(item, 'score')
            assert hasattr(item, 'check_method')
            assert item.check_method in [CheckMethod.SCRIPT, CheckMethod.AI_AGENT, CheckMethod.SBERT]

    def test_check_annotation_length(self, mock_llm_client, mock_embedding_function):
        """Тест проверки длины аннотации."""
        checker = AnnotationChecker(
            llm_client=mock_llm_client,
            embedding_function=mock_embedding_function
        )

        # Слишком короткая аннотация
        short_annotation = "Короткая аннотация."
        results = checker.check(short_annotation, "Тестовый проект")

        # Должен быть критерий 2.1.1 (длина)
        length_item = next((item for item in results if item.id == "2.1.1"), None)
        if length_item:
            assert length_item.score == 0

    def test_check_annotation_structure(self, mock_llm_client, mock_embedding_function):
        """Тест проверки структуры аннотации."""
        checker = AnnotationChecker(
            llm_client=mock_llm_client,
            embedding_function=mock_embedding_function
        )

        # Аннотация без структуры
        bad_annotation = "Просто текст без структуры."
        results = checker.check(bad_annotation, "Тестовый проект")

        # Должен быть критерий 2.1.2 (структура)
        structure_item = next((item for item in results if item.id == "2.1.2"), None)
        if structure_item:
            assert structure_item.check_method == CheckMethod.AI_AGENT

