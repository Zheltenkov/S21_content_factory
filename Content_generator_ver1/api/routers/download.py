"""Endpoints для скачивания результатов и шаблонов."""

import io
import zipfile
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from api.db.generation_results_db import (
    get_generation_result,
    get_report_by_request_id,
)
from api.dependencies import get_current_user
from api.services.archive_builder import (
    add_assets_to_zip,
    build_readme_filename,
    merge_assets,
)
from api.utils.logger import get_logger
from api.utils.result_cache import get_result
from content_gen.utils.markdown_display_normalizer import normalize_markdown_display_blocks

router = APIRouter()
logger = get_logger("download")


def _ensure_download_access(cached: dict[str, Any] | None, db_result: Any, user: dict) -> None:
    """Проверяет доступ к результату по владельцу, сохраняя совместимость со старым кэшем без user_id."""
    current_user_id = (user or {}).get("id")
    owner_id = None
    if isinstance(cached, dict):
        owner_id = cached.get("user_id")
    if owner_id is None and db_result is not None:
        owner_id = getattr(db_result, "user_id", None)

    if owner_id and current_user_id and owner_id != current_user_id:
        raise HTTPException(status_code=403, detail="Нет доступа к результату другого пользователя")


@router.get("/download/{request_id}")
async def download_results(
    request_id: str,
    include_regenerated: bool = Query(False, description="Включить перегенерированную версию"),
    user: dict = Depends(get_current_user)
):
    """
    Скачивает ZIP архив с результатами генерации.
    
    Args:
        request_id: ID запроса генерации
        include_regenerated: Включить перегенерированную версию (если есть)
        user: Данные пользователя
        
    Returns:
        ZIP архив с файлами
    """
    cached = get_result(request_id)
    db_result = get_generation_result(request_id)

    if not cached and not db_result:
        logger.warning("⚠️ Результат %s не найден ни в кэше, ни в БД", request_id)
        raise HTTPException(
            status_code=404,
            detail="Результат генерации не найден или истек срок хранения",
        )

    logger.info(
        "📥 Скачивание результата: request_id=%s, include_regenerated=%s",
        request_id,
        include_regenerated,
    )

    _ensure_download_access(cached, db_result, user)

    # Создаем ZIP архив
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        markdown = (cached or {}).get("markdown") or (db_result.markdown if db_result else "")
        markdown = normalize_markdown_display_blocks(markdown)
        if not markdown:
            raise HTTPException(status_code=404, detail="README не найден в результатах")

        report_json = get_report_by_request_id(request_id) or (cached or {}).get("report_json")
        readme_name = build_readme_filename(report_json)
        z.writestr(readme_name, markdown)
        if readme_name != "README.md":
            z.writestr("README.md", markdown)

        assets = merge_assets((report_json or {}).get("assets"), (cached or {}).get("assets"))
        image_count, file_count = add_assets_to_zip(z, assets, logger)
        logger.info("📦 В архив добавлены assets: images=%s, files=%s", image_count, file_count)

        # Обработка перегенерированной версии
        regen_md = None
        if include_regenerated:
            regen_block = (cached or {}).get("regenerated")
            if regen_block and regen_block.get("regenerated_md"):
                regen_md = regen_block["regenerated_md"]
            elif db_result and db_result.regenerated_markdown:
                regen_md = db_result.regenerated_markdown

            if regen_md:
                regen_md = normalize_markdown_display_blocks(regen_md)
                # Формируем имя для перегенерированного README с префиксом regen_
                original_name = build_readme_filename(report_json)
                # Добавляем префикс regen_ в начало имени файла
                regen_name = f"regen_{original_name}"
                z.writestr(regen_name, regen_md)
                logger.info(f"📝 Перегенерированный README сохранен как: {regen_name}")

    zip_size = len(buf.getvalue())
    logger.info("✅ ZIP архив создан: размер=%s байт", zip_size)

    # Название архива как у README (без расширения .md) + .zip
    zip_filename = readme_name.replace('.md', '.zip')

    # Если включена перегенерированная версия, добавляем префикс regen_ к имени архива
    if include_regenerated and regen_md:
        zip_filename = f"regen_{zip_filename}"

    return StreamingResponse(
        io.BytesIO(buf.getvalue()),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={zip_filename}"}
    )


@router.get("/download/translated/{request_id}")
async def download_translated_results(
    request_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Скачивает ZIP архив с переведенным README, переведенными диаграммами и файлами данных.
    
    Args:
        request_id: ID запроса генерации
        user: Данные пользователя
        
    Returns:
        ZIP архив с переведенными файлами
    """
    cached = get_result(request_id)
    db_result = get_generation_result(request_id)

    if not cached and not db_result:
        logger.warning("⚠️ Результат %s не найден ни в кэше, ни в БД", request_id)
        raise HTTPException(
            status_code=404,
            detail="Результат генерации не найден или истек срок хранения",
        )

    logger.info("📥 Скачивание переведенного результата: request_id=%s", request_id)
    _ensure_download_access(cached, db_result, user)

    # Создаем ZIP архив
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # Получаем переведенный markdown
        report_json = get_report_by_request_id(request_id) or (cached or {}).get("report_json")
        translated_markdown = (report_json or {}).get("translated_markdown")
        translated_markdown = normalize_markdown_display_blocks(translated_markdown or "")

        if not translated_markdown:
            raise HTTPException(
                status_code=404,
                detail="Переведенный README не найден в результатах генерации"
            )

        # Формируем имя файла для переведенного README
        readme_name = build_readme_filename(report_json)
        # Добавляем префикс translated_ к имени файла
        translated_readme_name = f"translated_{readme_name}"
        z.writestr(translated_readme_name, translated_markdown)
        logger.info(f"📝 Переведенный README сохранен как: {translated_readme_name}")

        translated_assets = (report_json or {}).get("translated_assets") or {}
        translated_assets = dict(translated_assets) if isinstance(translated_assets, dict) else {}
        if not translated_assets.get("files"):
            fallback_assets = merge_assets((report_json or {}).get("assets"), (cached or {}).get("assets"))
            translated_assets["files"] = fallback_assets.get("files", [])
        image_count, file_count = add_assets_to_zip(z, translated_assets, logger)
        logger.info("📦 В архив перевода добавлены assets: images=%s, files=%s", image_count, file_count)

    zip_size = len(buf.getvalue())
    logger.info("✅ ZIP архив с переводом создан: размер=%s байт", zip_size)

    # Название архива
    zip_filename = translated_readme_name.replace('.md', '.zip')

    return StreamingResponse(
        io.BytesIO(buf.getvalue()),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={zip_filename}"}
    )


@router.get("/template")
async def download_template():
    """
    Скачивает Excel шаблон для спецификации проекта.
    
    Returns:
        Excel файл шаблона
    """
    try:
        from utils.excel_io import excel_template

        template_buffer = excel_template()

        # Получаем байты из буфера
        template_buffer.seek(0)  # Убеждаемся, что указатель в начале
        template_bytes = template_buffer.getvalue()

        if not template_bytes or len(template_bytes) == 0:
            logger.error("❌ Созданный Excel шаблон пуст")
            raise HTTPException(status_code=500, detail="Ошибка: созданный шаблон пуст")

        logger.info(f"📄 Excel шаблон создан: размер={len(template_bytes)} байт")

        # Создаем новый BytesIO для ответа
        response_buffer = io.BytesIO(template_bytes)

        return StreamingResponse(
            response_buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=project_spec_template.xlsx"}
        )
    except Exception as e:
        logger.error(f"❌ Ошибка создания шаблона: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка создания шаблона: {str(e)}")
