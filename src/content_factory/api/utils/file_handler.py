"""Обработка загруженных файлов с изоляцией по user_id/request_id."""

import asyncio
import os
import shutil
import tempfile
from pathlib import Path

from fastapi import UploadFile


async def save_uploaded_files(
    files: list[UploadFile],
    user_id: str,
    request_id: str
) -> tuple[list[str], str | None]:
    """
    Сохраняет загруженные файлы во временную директорию с изоляцией.
    
    Args:
        files: Список загруженных файлов
        user_id: ID пользователя для изоляции
        request_id: ID запроса для изоляции
        
    Returns:
        Кортеж (список путей к файлам, путь к временной директории)
    """
    if not files:
        return [], None

    # Создаем изолированную директорию: /tmp/user_{user_id}/request_{request_id}/
    base_temp_dir = Path(tempfile.gettempdir())
    user_dir = base_temp_dir / f"user_{user_id}"
    request_dir = user_dir / f"request_{request_id}"
    request_dir.mkdir(parents=True, exist_ok=True)

    paths = []
    for file in files:
        file_path = request_dir / file.filename
        # Асинхронно читаем и сохраняем файл
        content = await file.read()
        file_path.write_bytes(content)
        paths.append(str(file_path))

    return paths, str(request_dir)


async def cleanup_temp_files(temp_dir: str) -> None:
    """
    Удаляет временную директорию с файлами.
    
    Args:
        temp_dir: Путь к временной директории
    """
    if not temp_dir or not os.path.exists(temp_dir):
        return

    try:
        # Удаляем в отдельном потоке, чтобы не блокировать
        await asyncio.to_thread(shutil.rmtree, temp_dir)
    except Exception as e:
        # Логируем ошибку, но не падаем
        logger.error(f"❌ Ошибка удаления временной директории {temp_dir}: {e}", exc_info=True)

