"""Endpoints для управления тематическими блоками."""

import json
from pathlib import Path

from fastapi import APIRouter, Depends

from api.routers.admin import is_admin

router = APIRouter()

THEMATIC_BLOCKS_FILE = Path("thematic_blocks.json")


def load_thematic_blocks() -> dict[str, str]:
    """Загружает тематические блоки из файла."""
    if THEMATIC_BLOCKS_FILE.exists():
        try:
            with open(THEMATIC_BLOCKS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    # Возвращаем значения по умолчанию
    return {
        "Бизнес аналитика": "BSA",
        "Кибербезопасность": "Cb",
        "DevOps": "DO",
        "Проектный менеджмент": "PjM",
        "Тестирование и обеспечение качества": "QA",
        "Машинное обучение": "DS"
    }


def save_thematic_blocks(blocks: dict[str, str]) -> None:
    """Сохраняет тематические блоки в файл."""
    with open(THEMATIC_BLOCKS_FILE, "w", encoding="utf-8") as f:
        json.dump(blocks, f, ensure_ascii=False, indent=2)


@router.get("/thematic-blocks")
async def get_thematic_blocks() -> dict[str, str]:
    """Получает список тематических блоков."""
    return load_thematic_blocks()


@router.post("/thematic-blocks")
async def save_thematic_blocks_endpoint(
    blocks: dict[str, str],
    admin: dict = Depends(is_admin),
) -> dict[str, str]:
    """Сохраняет тематические блоки."""
    save_thematic_blocks(blocks)
    return blocks

