"""
Иерархия кастомных исключений для централизованной обработки ошибок.
"""


class ContentGenerationError(Exception):
    """Базовое исключение для всех ошибок генерации контента."""

    def __init__(self, message: str, context: dict = None):
        """
        Args:
            message: Сообщение об ошибке
            context: Дополнительный контекст (request_id, agent_name, phase и т.д.)
        """
        # Явно вызываем Exception.__init__ с message
        Exception.__init__(self, message)
        self.message = message
        self.context = context or {}

    def __str__(self) -> str:
        context_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
        if context_str:
            return f"{self.message} [{context_str}]"
        return self.message


class LLMError(ContentGenerationError):
    """Ошибка при работе с LLM API."""
    pass


class LLMAPIError(LLMError):
    """Ошибка API LLM (network, timeout, rate limit)."""
    pass


class LLMTimeoutError(LLMAPIError):
    """Таймаут при вызове LLM."""
    pass


class LLMRateLimitError(LLMAPIError):
    """Превышен rate limit LLM."""
    pass


class LLMInvalidResponseError(LLMError):
    """Невалидный ответ от LLM (не JSON, пустой ответ и т.д.)."""
    pass


class ValidationError(ContentGenerationError):
    """Ошибка валидации входных данных."""
    pass


class AgentError(ContentGenerationError):
    """Ошибка в работе агента."""
    pass


class AgentGenerationError(AgentError):
    """Ошибка генерации контента агентом."""
    pass


class AgentValidationError(AgentError):
    """Ошибка валидации результата агента."""
    pass


class ConfigurationError(ContentGenerationError):
    """Ошибка конфигурации."""
    pass
