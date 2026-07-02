"""Протокол для LLM клиента."""

from typing import Protocol


class LLMClientProtocol(Protocol):
    """Протокол для LLM клиента."""

    def complete(
        self,
        system: str,
        user: str,
        response_format: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs
    ) -> str:
        """
        Выполняет запрос к LLM.
        
        Args:
            system: Системный промпт
            user: Пользовательский промпт
            response_format: Формат ответа (например, "json_object")
            temperature: Температура генерации
            max_tokens: Максимальное количество токенов
            **kwargs: Дополнительные параметры
            
        Returns:
            Текст ответа от LLM
        """
        ...

