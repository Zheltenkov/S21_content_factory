"""Базовые классы и интерфейсы для агентов."""

from .agent import BaseAgent
from .llm_client import LLMClientProtocol

__all__ = ["BaseAgent", "LLMClientProtocol"]
