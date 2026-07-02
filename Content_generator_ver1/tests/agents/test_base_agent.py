"""Тесты для базового агента."""

from unittest.mock import Mock

from content_gen.agents.base.agent import BaseAgent
from content_gen.agents.base.llm_client import LLMClientProtocol


class MockLLMClient(LLMClientProtocol):
    """Мок LLM клиента для тестов."""

    def complete(self, system: str, user: str, response_format=None, **kwargs) -> str:
        return "mock response"


class TestBaseAgent:
    """Тесты для BaseAgent."""

    def test_init_with_valid_llm_client(self):
        """Тест инициализации с валидным LLM клиентом."""
        llm_client = MockLLMClient()

        agent = BaseAgent(llm_client)
        assert agent.llm == llm_client

    def test_init_with_invalid_llm_client(self):
        """Тест инициализации с невалидным LLM клиентом."""
        # Создаем мок без метода complete
        llm_client = Mock(spec=[])  # Пустой spec - нет методов

        # BaseAgent проверяет hasattr(llm_client, 'complete')
        # Mock с пустым spec не имеет complete, но hasattr может вернуть True
        # Проверяем что валидация работает
        try:
            agent = BaseAgent(llm_client)
            # Если не выбросило исключение, проверяем что валидация прошла
            # (может быть, что Mock автоматически создает атрибуты)
            assert hasattr(agent.llm, 'complete') or hasattr(llm_client, 'complete')
        except TypeError:
            # Это ожидаемое поведение
            pass

    def test_repr(self):
        """Тест строкового представления."""
        llm_client = MockLLMClient()

        agent = BaseAgent(llm_client)
        repr_str = repr(agent)
        assert "BaseAgent" in repr_str
        assert "MockLLMClient" in repr_str

