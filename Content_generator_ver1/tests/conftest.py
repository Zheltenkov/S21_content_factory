"""Конфигурация pytest и общие фикстуры."""

import asyncio
import inspect
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from content_gen.agents.base.llm_client import LLMClientProtocol


# На некоторых Windows-контурах системный TEMP недоступен для pytest tmp_path.
# Перенаправляем временные каталоги в рабочую директорию репозитория.
_DEFAULT_TEMP_ROOT = Path(__file__).resolve().parents[1] / ".tmp" / "pytest-runtime-local"
_PYTEST_TEMP_ROOT = Path(os.environ.get("PYTEST_TEMP_ROOT", str(_DEFAULT_TEMP_ROOT)))
_PYTEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["TMP"] = str(_PYTEST_TEMP_ROOT)
os.environ["TEMP"] = str(_PYTEST_TEMP_ROOT)
os.environ["TMPDIR"] = str(_PYTEST_TEMP_ROOT)
tempfile.tempdir = str(_PYTEST_TEMP_ROOT)


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    """Локально выполняет async-тесты без отдельного pytest-плагина."""
    test_func = pyfuncitem.obj
    if not inspect.iscoroutinefunction(test_func):
        return None
    funcargs = {
        name: pyfuncitem.funcargs[name]
        for name in pyfuncitem._fixtureinfo.argnames
    }
    asyncio.run(test_func(**funcargs))
    return True


@pytest.fixture
def mock_llm_client():
    """Создает мок LLM клиента."""
    client = Mock(spec=LLMClientProtocol)
    client.complete = MagicMock(return_value='{"result": "test"}')
    return client


@pytest.fixture
def mock_embedding_function():
    """Создает мок функции эмбеддингов."""
    def mock_embed(texts):
        # Возвращаем фиктивные эмбеддинги
        return [[0.1] * 768 for _ in texts]
    return mock_embed


@pytest.fixture
def sample_markdown():
    """Пример markdown для тестирования."""
    return """# Проект: Тестовый проект

## Содержание

1. [Глава 1](#глава-1)
2. [Глава 2](#глава-2)
3. [Глава 3](#глава-3)

## Глава 1

### Введение

Это введение к проекту.

### Инструкция

**Контекст и ограничения проекта**

Проект выполняется в кампусной среде «Школы 21».

## Глава 2

### 2.1. Теория

Теоретический материал.

## Глава 3

### Задание 1. Практика

Практическое задание.
"""


@pytest.fixture
def sample_annotation():
    """Пример аннотации для тестирования."""
    return """Проект предназначен для изучения основ программирования. 
В проекте рассматриваются базовые концепции Python. 
В результате выполнения проекта участник получит навыки работы с Python."""
