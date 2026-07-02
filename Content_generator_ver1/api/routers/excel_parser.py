"""Endpoint для парсинга Excel файлов."""

from typing import Any
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from api.dependencies import get_current_user
from api.utils.file_validation import MAX_FILE_SIZE, read_upload_limited, validate_file

router = APIRouter()


@router.post("/parse-excel")
async def parse_excel(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Парсит Excel файл и возвращает данные в формате JSON.
    
    Args:
        file: Загруженный Excel файл
        
    Returns:
        Словарь с данными проекта
    """
    try:
        from utils.excel_io import excel_to_json

        validate_file(file)
        extension = Path(file.filename or "").suffix.lower()
        if extension not in {".xlsx", ".xls"}:
            raise HTTPException(status_code=400, detail="Поддерживаются только Excel файлы .xlsx или .xls")

        file_bytes = await read_upload_limited(file, max_size=MAX_FILE_SIZE)

        # Парсим Excel
        data_list = excel_to_json(file_bytes)

        if not data_list:
            raise HTTPException(status_code=400, detail="Excel файл пуст или не содержит данных")

        # Возвращаем первый элемент (если несколько проектов в файле)
        result = data_list[0]

        # Маппинг для обратной совместимости
        if "project_title" in result and "title_seed" not in result:
            result["title_seed"] = result["project_title"]

        return result

    except ImportError:
        raise HTTPException(status_code=500, detail="Для работы с Excel файлами требуется pandas. Установите: pip install pandas openpyxl")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail="Ошибка при чтении Excel файла")

