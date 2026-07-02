"""
utils/token_counter.py

Утилита для подсчета токенов в тексте.
"""

try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False


def count_tokens(text: str) -> int:
    """
    Подсчитывает количество токенов в тексте.
    
    Args:
        text: Текст для подсчета
        
    Returns:
        Количество токенов
    """
    if not TIKTOKEN_AVAILABLE:
        # Fallback: приблизительный подсчет (1 токен ≈ 4 символа)
        return len(text or "") // 4

    try:
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text or ""))
    except Exception:
        # Fallback при ошибке
        return len(text or "") // 4

