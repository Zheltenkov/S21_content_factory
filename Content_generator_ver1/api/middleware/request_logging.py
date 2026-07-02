"""Middleware для логирования HTTP запросов в базу данных."""

import os
import time
import uuid
from collections.abc import Callable

from fastapi import Request, Response
from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware

from api.db.logging_db import write_request_log_async
from api.utils.data_masking import mask_request_body

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
JWT_ALGORITHM = "HS256"


async def extract_user_id_from_token(request: Request) -> str | None:
    """
    Извлекает user_id из JWT токена в заголовке Authorization.
    
    Args:
        request: FastAPI Request объект
        
    Returns:
        user_id или None, если токен невалиден или отсутствует
    """
    authorization = request.headers.get("authorization")
    if not authorization or not authorization.startswith("Bearer "):
        return None

    token = authorization.split(" ")[1]
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware для логирования всех HTTP запросов в базу данных."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Обрабатывает запрос и логирует его в БД.
        
        Args:
            request: FastAPI Request объект
            call_next: Следующий middleware или endpoint
            
        Returns:
            Response объект
        """
        # Генерируем request_id для этого запроса
        request_id = str(uuid.uuid4())

        # Извлекаем user_id из токена (если есть)
        user_id = await extract_user_id_from_token(request)

        # Получаем информацию о запросе
        method = request.method
        path = request.url.path
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

        # Читаем тело запроса (если есть)
        request_body = None
        body_bytes = None
        if method in ("POST", "PUT", "PATCH"):
            try:
                # Читаем тело запроса
                body_bytes = await request.body()
                if body_bytes:
                    # Маскируем чувствительные данные
                    request_body = mask_request_body(body_bytes)
            except Exception:
                # Если не удалось прочитать тело, игнорируем
                pass

        # Засекаем время начала обработки
        start_time = time.time()

        # Восстанавливаем body для дальнейшей обработки (если было прочитано)
        if body_bytes is not None:
            async def receive():
                return {"type": "http.request", "body": body_bytes}
            request._receive = receive

        # Выполняем запрос
        try:
            response = await call_next(request)

            # Вычисляем время ответа
            response_time_ms = int((time.time() - start_time) * 1000)

            # Получаем статус код
            status_code = response.status_code

            # Логируем запрос асинхронно (не блокирует ответ)
            await write_request_log_async(
                request_id=request_id,
                user_id=user_id,
                method=method,
                path=path,
                status_code=status_code,
                request_body=request_body,
                response_time_ms=response_time_ms,
                ip_address=ip_address,
                user_agent=user_agent
            )

            return response

        except Exception:
            # Если произошла ошибка, логируем её
            response_time_ms = int((time.time() - start_time) * 1000)

            await write_request_log_async(
                request_id=request_id,
                user_id=user_id,
                method=method,
                path=path,
                status_code=500,
                request_body=request_body,
                response_time_ms=response_time_ms,
                ip_address=ip_address,
                user_agent=user_agent
            )

            # Пробрасываем ошибку дальше
            raise
