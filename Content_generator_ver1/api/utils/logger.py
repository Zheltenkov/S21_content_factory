"""Централизованное логирование для API."""

import logging
import sys
from datetime import datetime

from api.utils.logging_context import get_request_id, get_user_id


# Настройка форматтера с цветами для терминала
class ColoredFormatter(logging.Formatter):
    """Форматтер с цветами для терминала."""

    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',  # Magenta
        'RESET': '\033[0m'       # Reset
    }

    def format(self, record):
        # Добавляем контекст из contextvars
        request_id = get_request_id()
        user_id = get_user_id()

        if request_id:
            record.request_id = request_id[:8]  # Короткий ID
        else:
            record.request_id = "N/A"

        if user_id:
            record.user_id = user_id[:12]  # Короткий ID
        else:
            record.user_id = "N/A"

        # Форматируем сообщение
        log_color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        reset = self.COLORS['RESET']

        # Формат: [HH:MM:SS] LEVEL [req_id] [user_id] message
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_msg = (
            f"{log_color}[{timestamp}] {record.levelname:8s}{reset} "
            f"[req:{record.request_id}] [user:{record.user_id}] "
            f"{record.getMessage()}"
        )

        # Добавляем информацию об исключении, если есть
        if record.exc_info:
            log_msg += f"\n{self.formatException(record.exc_info)}"

        return log_msg


def setup_logging(level: str = "INFO") -> logging.Logger:
    """
    Настраивает централизованное логирование для API.
    
    Args:
        level: Уровень логирования (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        
    Returns:
        Настроенный logger
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Настраиваем root logger для вывода в терминал
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Удаляем существующие handlers root logger, чтобы не дублировать
    root_logger.handlers.clear()

    # Создаем handler для stderr с цветным форматтером для root logger
    root_handler = logging.StreamHandler(sys.stderr)
    root_handler.setLevel(logging.DEBUG)
    root_formatter = ColoredFormatter()
    root_handler.setFormatter(root_formatter)
    root_logger.addHandler(root_handler)

    # Все логгеры приложения должны писать через root handler.
    # Это убирает дубли, когда модульный logger и root логируют одно и то же сообщение.
    logger = logging.getLogger("api")
    logger.setLevel(log_level)
    logger.handlers.clear()
    logger.propagate = True

    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """
    Получает logger для модуля.
    
    Args:
        name: Имя модуля (опционально)
        
    Returns:
        Logger для модуля
    """
    if name:
        return logging.getLogger(f"api.{name}")
    return logging.getLogger("api")
