"""Утилиты для маскирования чувствительных данных в логах."""

import json
from typing import Any

# Поля, которые нужно маскировать
SENSITIVE_FIELDS = {
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "api-key",
    "access_token",
    "refresh_token",
    "authorization",
    "auth",
    "credential",
    "credentials",
    "private_key",
    "private-key",
    "secret_key",
    "secret-key",
    "jwt_secret",
    "jwt-secret",
    "openai_api_key",
    "openai-api-key",
    "openai_key",
    "openai-key",
}


def mask_value(value: Any, mask_char: str = "*", visible_chars: int = 4) -> str:
    """
    Маскирует значение, оставляя видимыми только последние символы.
    
    Args:
        value: Значение для маскирования
        mask_char: Символ для маскирования
        visible_chars: Количество видимых символов в конце
        
    Returns:
        Замаскированное значение
    """
    if not value:
        return str(value)

    value_str = str(value)
    if len(value_str) <= visible_chars:
        return mask_char * len(value_str)

    return mask_char * (len(value_str) - visible_chars) + value_str[-visible_chars:]


def mask_dict(data: dict[str, Any], mask_char: str = "*", visible_chars: int = 4) -> dict[str, Any]:
    """
    Рекурсивно маскирует чувствительные поля в словаре.
    
    Args:
        data: Словарь для обработки
        mask_char: Символ для маскирования
        visible_chars: Количество видимых символов в конце
        
    Returns:
        Словарь с замаскированными значениями
    """
    if not isinstance(data, dict):
        return data

    masked = {}
    for key, value in data.items():
        key_lower = key.lower()

        # Проверяем, является ли поле чувствительным
        is_sensitive = any(
            sensitive_field in key_lower
            for sensitive_field in SENSITIVE_FIELDS
        )

        if is_sensitive:
            # Маскируем значение
            if isinstance(value, (str, int, float)):
                masked[key] = mask_value(value, mask_char, visible_chars)
            elif isinstance(value, dict):
                masked[key] = mask_dict(value, mask_char, visible_chars)
            elif isinstance(value, list):
                masked[key] = [
                    mask_dict(item, mask_char, visible_chars) if isinstance(item, dict)
                    else mask_value(item, mask_char, visible_chars) if isinstance(item, (str, int, float))
                    else item
                    for item in value
                ]
            else:
                masked[key] = "[MASKED]"
        elif isinstance(value, dict):
            # Рекурсивно обрабатываем вложенные словари
            masked[key] = mask_dict(value, mask_char, visible_chars)
        elif isinstance(value, list):
            # Обрабатываем списки
            masked[key] = [
                mask_dict(item, mask_char, visible_chars) if isinstance(item, dict)
                else item
                for item in value
            ]
        else:
            masked[key] = value

    return masked


def mask_json_string(json_str: str, mask_char: str = "*", visible_chars: int = 4) -> str:
    """
    Маскирует чувствительные данные в JSON строке.
    
    Args:
        json_str: JSON строка для обработки
        mask_char: Символ для маскирования
        visible_chars: Количество видимых символов в конце
        
    Returns:
        JSON строка с замаскированными значениями
    """
    if not json_str:
        return json_str

    try:
        data = json.loads(json_str)
        if isinstance(data, dict):
            masked_data = mask_dict(data, mask_char, visible_chars)
        elif isinstance(data, list):
            masked_data = [
                mask_dict(item, mask_char, visible_chars) if isinstance(item, dict)
                else item
                for item in data
            ]
        else:
            return json_str

        return json.dumps(masked_data, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        # Если не удалось распарсить, возвращаем исходную строку
        return json_str


def mask_request_body(body: str | bytes | dict[str, Any] | None) -> dict[str, Any] | None:
    """
    Маскирует чувствительные данные в теле запроса.
    
    Args:
        body: Тело запроса (может быть строкой, bytes или словарем)
        
    Returns:
        Словарь с замаскированными данными или None
    """
    if body is None:
        return None

    # Если это bytes, декодируем в строку
    if isinstance(body, bytes):
        try:
            body = body.decode('utf-8')
        except UnicodeDecodeError:
            return {"error": "Unable to decode request body"}

    # Если это строка, пытаемся распарсить как JSON
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            # Если не JSON, возвращаем как есть (может быть form-data)
            return {"raw": "[NON-JSON DATA]"}

    # Если это словарь, маскируем его
    if isinstance(body, dict):
        return mask_dict(body)

    # Для других типов возвращаем как есть
    return body if isinstance(body, (dict, list)) else {"value": str(body)}
