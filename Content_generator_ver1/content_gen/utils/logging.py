"""Утилиты для безопасного логирования в многопоточном окружении."""

import sys
from typing import Optional

# Сохраняем оригинальный stderr для использования в потоках
_ORIGINAL_STDERR = sys.__stderr__


def safe_print(*args, sep: str = " ", end: str = "\n", flush: bool = False, file: Optional = None):
    """
    Безопасный print для использования в потоках.
    Использует оригинальный stderr, если текущий stderr недоступен (например, нет Streamlit контекста).
    
    Args:
        *args: Аргументы для вывода
        sep: Разделитель между аргументами
        end: Символ в конце строки
        flush: Флаг принудительной очистки буфера
        file: Файл для вывода (по умолчанию sys.stderr)
    """
    if file is None:
        file = sys.stderr

    try:
        # Пытаемся использовать указанный файл
        print(*args, sep=sep, end=end, flush=flush, file=file)
    except (RuntimeError, AttributeError, Exception):
        # Если ошибка (например, нет Streamlit контекста в потоке), используем оригинальный stderr
        try:
            print(*args, sep=sep, end=end, flush=flush, file=_ORIGINAL_STDERR)
        except Exception:
            # В крайнем случае просто игнорируем (не критично для логирования)
            pass

