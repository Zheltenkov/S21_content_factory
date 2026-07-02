"""Тесты для утилит валидации рубрики."""

import pytest

from content_gen.validators.rubric.utils import (
    bag,
    cosine,
    readability_index,
    semantic_similarity,
    tokens,
)


class TestRubricUtils:
    """Тесты для утилит валидации."""

    def test_cosine_similarity_identical(self):
        """Тест cosine similarity с идентичными мешками слов."""
        a = {"word1": 2, "word2": 1}
        b = {"word1": 2, "word2": 1}

        result = cosine(a, b)
        assert result == pytest.approx(1.0, abs=1e-6)

    def test_cosine_similarity_different(self):
        """Тест cosine similarity с разными мешками слов."""
        a = {"word1": 1, "word2": 1}
        b = {"word3": 1, "word4": 1}

        result = cosine(a, b)
        assert result == pytest.approx(0.0, abs=1e-6)

    def test_cosine_empty(self):
        """Тест cosine similarity с пустыми мешками."""
        result = cosine({}, {})
        assert result == 0.0

    def test_bag_of_words(self):
        """Тест создания мешка слов."""
        tokens_list = ["word1", "word2", "word1", "word3"]
        result = bag(tokens_list)

        assert result["word1"] == 2
        assert result["word2"] == 1
        assert result["word3"] == 1

    def test_tokens_extraction(self):
        """Тест извлечения токенов."""
        text = "Это тестовый текст для проверки."
        result = tokens(text, "ru")

        assert len(result) > 0
        assert all(isinstance(t, str) for t in result)

    def test_semantic_similarity_fallback(self):
        """Тест semantic similarity с fallback на bag-of-words."""
        text1 = "Это тестовый текст для проверки"
        text2 = "Это тестовый текст для проверки"

        result = semantic_similarity(text1, text2, lang="ru", embedding_function=None)

        assert 0.0 <= result <= 1.0 + 1e-6  # Допускаем небольшую погрешность
        assert result > 0.5  # Должна быть высокая схожесть

    def test_readability_index(self):
        """Тест индекса читаемости."""
        text = "Это простой текст. Он содержит несколько предложений. Каждое предложение короткое."
        result = readability_index(text, lang="ru")

        assert 0.0 <= result <= 100.0

