"""Тесты для StructuredLLMClient."""

from unittest.mock import MagicMock, Mock

import pytest
from pydantic import BaseModel, Field

from content_gen.agents.base.llm_client import LLMClientProtocol
from content_gen.llm.structured_output import StructuredLLMClient


class StructuredOutputTestModel(BaseModel):
    """Тестовая Pydantic модель."""
    name: str = Field(description="Имя")
    age: int = Field(description="Возраст", ge=0)
    tags: list[str] = Field(default_factory=list, description="Теги")


class NestedStructuredOutputModel(BaseModel):
    """Тестовая модель с вложенными объектами."""
    class InnerModel(BaseModel):
        value: str

    title: str
    inner: InnerModel


@pytest.fixture
def mock_llm_client():
    """Создает мок LLM клиента."""
    client = Mock(spec=LLMClientProtocol)
    client.model = "gpt-4o-mini"
    return client


@pytest.fixture
def structured_client(mock_llm_client):
    """Создает StructuredLLMClient с моком."""
    return StructuredLLMClient(mock_llm_client)


def test_supports_structured_outputs(structured_client):
    """Тест проверки поддержки structured outputs."""
    # gpt-4o-mini поддерживает
    structured_client.llm.model = "gpt-4o-mini"
    assert structured_client._supports_structured_outputs() is True

    # Polza использует OpenAI-compatible маршрут к модели OpenAI и тоже поддерживает JSON schema.
    structured_client.llm.model = "openai/openai/gpt-5.4-mini"
    assert structured_client._supports_structured_outputs() is True

    # GPT 5.4 mini используется как дефолтная Polza-модель.
    structured_client.llm.model = "gpt-5.4-mini"
    assert structured_client._supports_structured_outputs() is True

    # gpt-4o поддерживает
    structured_client.llm.model = "gpt-4o"
    assert structured_client._supports_structured_outputs() is True

    # o1-mini поддерживает
    structured_client.llm.model = "o1-mini"
    assert structured_client._supports_structured_outputs() is True

    # gpt-3.5 не поддерживает
    structured_client.llm.model = "gpt-3.5-turbo"
    assert structured_client._supports_structured_outputs() is False


def test_prepare_json_schema(structured_client):
    """Тест подготовки JSON Schema из Pydantic модели."""
    schema = structured_client._prepare_json_schema(StructuredOutputTestModel)

    assert schema["type"] == "object"
    assert "properties" in schema
    assert "name" in schema["properties"]
    assert "age" in schema["properties"]
    assert "tags" in schema["properties"]
    assert "required" in schema


def test_complete_structured_with_structured_outputs(structured_client):
    """Тест complete_structured с поддержкой structured outputs."""
    structured_client.llm.model = "gpt-4o-mini"

    # Мокаем ответ от LLM (уже валидный JSON благодаря structured outputs)
    mock_response = '{"name": "Test", "age": 25, "tags": ["tag1", "tag2"]}'
    structured_client.llm.complete = MagicMock(return_value=mock_response)

    result = structured_client.complete_structured(
        output_model=StructuredOutputTestModel,
        system="You are a test assistant",
        user="Generate test data"
    )

    assert isinstance(result, StructuredOutputTestModel)
    assert result.name == "Test"
    assert result.age == 25
    assert result.tags == ["tag1", "tag2"]

    # Проверяем, что был вызван complete с правильным response_format
    call_args = structured_client.llm.complete.call_args
    assert call_args is not None
    response_format = call_args.kwargs.get("response_format")
    assert response_format is not None
    assert response_format["type"] == "json_schema"
    assert "json_schema" in response_format


def test_complete_structured_fallback_to_json_mode(structured_client):
    """Тест fallback на JSON mode для неподдерживаемых моделей."""
    structured_client.llm.model = "gpt-3.5-turbo"

    # Мокаем ответ от LLM (обычный JSON mode)
    mock_response = '{"name": "Test", "age": 25, "tags": ["tag1"]}'
    structured_client.llm.complete = MagicMock(return_value=mock_response)

    result = structured_client.complete_structured(
        output_model=StructuredOutputTestModel,
        system="You are a test assistant",
        user="Generate test data"
    )

    assert isinstance(result, StructuredOutputTestModel)
    assert result.name == "Test"
    assert result.age == 25

    # Проверяем, что был вызван complete с response_format="json_object"
    call_args = structured_client.llm.complete.call_args
    assert call_args is not None
    response_format = call_args.kwargs.get("response_format")
    assert response_format == "json_object"


def test_complete_structured_with_nested_model(structured_client):
    """Тест complete_structured с вложенными моделями."""
    structured_client.llm.model = "gpt-4o-mini"

    mock_response = '{"title": "Test", "inner": {"value": "nested"}}'
    structured_client.llm.complete = MagicMock(return_value=mock_response)

    result = structured_client.complete_structured(
        output_model=NestedStructuredOutputModel,
        system="Test",
        user="Test"
    )

    assert isinstance(result, NestedStructuredOutputModel)
    assert result.title == "Test"
    assert result.inner.value == "nested"


def test_complete_structured_validation_error(structured_client):
    """Тест обработки ошибки валидации Pydantic."""
    structured_client.llm.model = "gpt-4o-mini"

    # Невалидные данные (age отрицательный)
    mock_response = '{"name": "Test", "age": -5, "tags": []}'
    structured_client.llm.complete = MagicMock(return_value=mock_response)

    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        structured_client.complete_structured(
            output_model=StructuredOutputTestModel,
            system="Test",
            user="Test"
        )


def test_complete_structured_json_decode_error(structured_client):
    """Тест обработки ошибки парсинга JSON."""
    structured_client.llm.model = "gpt-4o-mini"

    # Невалидный JSON
    mock_response = '{"name": "Test", "age": 25'  # незакрытая скобка
    structured_client.llm.complete = MagicMock(return_value=mock_response)

    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        structured_client.complete_structured(
            output_model=StructuredOutputTestModel,
            system="Test",
            user="Test"
        )


def test_complete_structured_with_markdown_code_block(structured_client):
    """Тест обработки ответа с markdown code block."""
    structured_client.llm.model = "gpt-4o-mini"

    # Ответ обернут в markdown code block
    mock_response = '```json\n{"name": "Test", "age": 25, "tags": []}\n```'
    structured_client.llm.complete = MagicMock(return_value=mock_response)

    result = structured_client.complete_structured(
        output_model=StructuredOutputTestModel,
        system="Test",
        user="Test"
    )

    assert isinstance(result, StructuredOutputTestModel)
    assert result.name == "Test"
    assert result.age == 25


def test_complete_structured_llm_api_error(structured_client):
    """Тест обработки ошибки LLM API."""
    structured_client.llm.model = "gpt-4o-mini"

    from content_gen.exceptions import LLMAPIError

    structured_client.llm.complete = MagicMock(side_effect=LLMAPIError("API error"))

    with pytest.raises(LLMAPIError):
        structured_client.complete_structured(
            output_model=StructuredOutputTestModel,
            system="Test",
            user="Test"
        )

