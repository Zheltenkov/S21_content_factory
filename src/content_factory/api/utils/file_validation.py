"""Валидация загружаемых файлов."""

import os
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

# Максимальные размеры
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE_BYTES", 10 * 1024 * 1024))  # 10MB
MAX_TOTAL_SIZE = int(os.getenv("MAX_TOTAL_FILES_SIZE_BYTES", 50 * 1024 * 1024))  # 50MB
MAX_FILES_COUNT = int(os.getenv("MAX_FILES_COUNT", "20"))

# Разрешенные расширения
ALLOWED_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml",
    ".xml", ".csv", ".xlsx", ".xls", ".pdf", ".zip", ".tar", ".gz"
}

# Видео для пайплайна субтитров
MAX_VIDEO_SIZE = int(os.getenv("MAX_VIDEO_SIZE_BYTES", 500 * 1024 * 1024))  # 500MB
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".avi", ".mkv", ".m4v"}

# Запрещенные имена файлов (защита от path traversal)
FORBIDDEN_FILENAMES = {
    "..", ".", "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
}


async def read_upload_limited(file: UploadFile, *, max_size: int, chunk_size: int = 1024 * 1024) -> bytes:
    """Читает UploadFile потоково и останавливается сразу после превышения лимита."""
    content = bytearray()
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > max_size:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Файл слишком большой. Максимальный размер: {max_size / 1024 / 1024:.0f}MB",
            )
    return bytes(content)


def validate_file(file: UploadFile) -> None:
    """
    Валидирует один файл.
    
    Args:
        file: Файл для валидации
        
    Raises:
        HTTPException: Если файл не проходит валидацию
    """
    # Проверка размера
    if hasattr(file, 'size') and file.size and file.size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Файл {file.filename} слишком большой. Максимальный размер: {MAX_FILE_SIZE / 1024 / 1024}MB"
        )

    # Проверка расширения
    if file.filename:
        raw_filename = file.filename
        filename = Path(raw_filename).name
        if (
            filename in FORBIDDEN_FILENAMES
            or ".." in raw_filename
            or "/" in raw_filename
            or "\\" in raw_filename
            or filename != raw_filename
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Недопустимое имя файла"
            )

        file_ext = Path(filename).suffix.lower()
        if file_ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Расширение {file_ext} не разрешено. Разрешенные: {', '.join(ALLOWED_EXTENSIONS)}"
            )


def validate_video_file(file: UploadFile) -> None:
    """
    Валидирует видеофайл для пайплайна субтитров (размер и расширение).

    Raises:
        HTTPException: Если файл не проходит валидацию
    """
    if file.filename:
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in ALLOWED_VIDEO_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Формат видео не поддерживается. Разрешены: {', '.join(sorted(ALLOWED_VIDEO_EXTENSIONS))}"
            )
        filename = Path(file.filename).name
        if filename in FORBIDDEN_FILENAMES or ".." in filename or "/" in filename or "\\" in filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Недопустимое имя файла"
            )
    # Размер проверяется при чтении в эндпоинте (после await file.read())


async def validate_files(files: list[UploadFile]) -> None:
    """
    Валидирует список файлов.
    
    Args:
        files: Список файлов для валидации
        
    Raises:
        HTTPException: Если файлы не проходят валидацию
    """
    if len(files) > MAX_FILES_COUNT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Слишком много файлов. Максимум: {MAX_FILES_COUNT}"
        )

    total_size = 0
    for file in files:
        validate_file(file)

        # Читаем размер файла
        if hasattr(file, 'size') and file.size:
            total_size += file.size
        else:
            # Если размер неизвестен, читаем файл для проверки
            content = await file.read()
            file_size = len(content)
            total_size += file_size

            # Возвращаем позицию в начало
            await file.seek(0)

            if file_size > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Файл {file.filename} слишком большой"
                )

    if total_size > MAX_TOTAL_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Общий размер файлов слишком большой. Максимум: {MAX_TOTAL_SIZE / 1024 / 1024}MB"
        )
