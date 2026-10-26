"""Перевод архива проекта: опись до оплаты, затем прогон по отмеченным файлам.

Поток двухшаговый нарочно. Архив курса — это сотни файлов, и перевести их все вслепую значит
заплатить за английские примеры и за служебные файлы. Поэтому сначала ``inspect`` считает
работу вхолостую и возвращает опись со сметой, а методист снимает лишнее. Модель на этом шаге
не зовётся ни разу — это проверяется тестом, а не обещанием.

План живёт в памяти процесса. Перезапуск ``cf-app`` его теряет, и архив придётся загрузить
заново: это принято сознательно, потому что хранить чужой архив на диске дольше одного прогона
незачем. Пределы — по числу планов и по времени, как у загрузок РПД, иначе память растёт от
каждой описи, за которой не последовал прогон.

План привязан к пользователю, и чужой ``plan_id`` не открывается: без этой проверки
угаданный идентификатор отдавал бы опись чужого архива вместе с именами всех файлов в нём.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any, cast

from fastapi import HTTPException, UploadFile

from content_factory.api.routers.document_translation import (
    SOURCE_LANGUAGE,
    _write_translation_artifact,
    create_llm_client,
    logger,
)
from content_factory.api.utils.file_validation import FORBIDDEN_FILENAMES, read_upload_limited
from content_factory.api.utils.result_cache import set_translation_job, set_translation_phase
from content_factory.generation.agents.base.llm_client import LLMClientProtocol
from content_factory.generation.models.schemas import ProjectSeed
from content_factory.translation.archive import (
    ArchiveMember,
    Estimate,
    InventoryEntry,
    estimate_for,
    read_members,
    repack,
    take_inventory,
)
from content_factory.translation.batch import translate_files
from content_factory.translation.errors import DocumentFormatError
from content_factory.translation.report import build_report

ARCHIVE_EXTENSIONS = {".zip"}
MAX_ARCHIVE_SIZE = int(os.getenv("MAX_TRANSLATION_ARCHIVE_SIZE_BYTES", 25 * 1024 * 1024))

# Пределы на хранилище планов: план держит распакованный архив в памяти.
MAX_PLANS = 10
PLAN_TTL_SECONDS = 60 * 60

_PLANS: dict[str, dict[str, Any]] = {}


def safe_archive_filename(file: UploadFile) -> tuple[str, str]:
    """Проверяет имя загруженного архива — ровно как у одиночного документа."""
    raw = file.filename or ""
    name = Path(raw).name
    if (
        not name
        or name in FORBIDDEN_FILENAMES
        or ".." in raw
        or "/" in raw
        or "\\" in raw
        or name != raw
    ):
        raise HTTPException(status_code=400, detail="Недопустимое имя файла")
    extension = Path(name).suffix.lower()
    if extension not in ARCHIVE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Нужен архив .zip с файлами проекта")
    return name, extension


async def read_uploaded_archive(file: UploadFile) -> tuple[str, bytes]:
    """Читает загруженный архив с ограничением по размеру."""
    name, _extension = safe_archive_filename(file)
    content = await read_upload_limited(file, max_size=MAX_ARCHIVE_SIZE)
    if not content:
        raise HTTPException(status_code=400, detail="Архив пустой")
    return name, content


def inspect_archive(content: bytes) -> tuple[list[ArchiveMember], list[InventoryEntry], Estimate]:
    """Распаковывает архив и считает опись со сметой, не обращаясь к модели."""
    try:
        members = read_members(content)
    except DocumentFormatError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not members:
        raise HTTPException(status_code=400, detail="В архиве нет файлов")
    entries = take_inventory(members)
    return members, entries, estimate_for(entries)


def remember_plan(
    *,
    user_id: str,
    filename: str,
    members: list[ArchiveMember],
    entries: list[InventoryEntry],
) -> str:
    """Запоминает разобранный архив и возвращает идентификатор плана."""
    _forget_stale()
    plan_id = str(uuid.uuid4())
    _PLANS[plan_id] = {
        "user_id": user_id,
        "filename": filename,
        "members": members,
        "entries": entries,
        "created_at": time.monotonic(),
    }
    while len(_PLANS) > MAX_PLANS:
        _PLANS.pop(next(iter(_PLANS)), None)
    return plan_id


def plan_for_user(plan_id: str, user_id: str) -> dict[str, Any]:
    """Отдаёт план владельцу. Чужой или просроченный — 404, без подсказок о том, какой именно."""
    _forget_stale()
    plan = _PLANS.get(plan_id)
    if plan is None or plan["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="План перевода не найден или устарел")
    return plan


def forget_plan(plan_id: str) -> None:
    _PLANS.pop(plan_id, None)


def _forget_stale() -> None:
    deadline = time.monotonic() - PLAN_TTL_SECONDS
    for plan_id in [key for key, plan in _PLANS.items() if plan["created_at"] < deadline]:
        _PLANS.pop(plan_id, None)


def run_archive_translation(
    *,
    request_id: str,
    user_id: str,
    plan: dict[str, Any],
    target_language: str,
    paths: list[str],
    seed: ProjectSeed,
) -> None:
    """Переводит отмеченные файлы архива и складывает результат одним ``.zip``."""
    members: list[ArchiveMember] = plan["members"]
    entries: list[InventoryEntry] = plan["entries"]
    total = len(paths) or 1
    done = 0

    def progress_callback(phase: str) -> None:
        nonlocal done
        done += 1
        set_translation_phase(request_id, phase, progress=min(done / total, 1.0))

    try:
        # Фабрика объявлена возвращающей ``object``; приведение здесь такое же, как в
        # переводе одиночного документа.
        llm_client = cast(
            LLMClientProtocol,
            create_llm_client(
                provider=seed.llm_provider,
                default_role="translator",
                enable_cache=True,
                enable_batching=True,
                user_id=user_id,
                run_id=request_id,
            ),
        )
        translations, outcomes = translate_files(
            llm_client,
            members,
            entries,
            target_language=target_language,
            only=paths,
            progress_callback=progress_callback,
        )
        report = build_report(outcomes, target_language=target_language)
        archive_bytes = repack(members, translations, report=report)

        stem = Path(plan["filename"]).stem
        stored = _write_translation_artifact(
            request_id, f"{stem}_{target_language}.zip", archive_bytes
        )
        set_translation_job(
            request_id=request_id,
            status="completed",
            user_id=user_id,
            phase="completed",
            progress=1.0,
            target_language=target_language,
            job_type="archive",
            source_filename=plan["filename"],
            source_format="zip",
            translated_markdown=report,
            result_links={"zip": stored},
        )
        logger.info(
            "Archive translation finished (request_id=%s, files=%s, translated=%s)",
            request_id,
            len(entries),
            len(translations),
        )
    except Exception as exc:  # noqa: BLE001 — задача не должна падать молча
        logger.error("Archive translation failed (request_id=%s): %s", request_id, exc, exc_info=True)
        set_translation_job(
            request_id=request_id,
            status="failed",
            user_id=user_id,
            phase="failed",
            target_language=target_language,
            job_type="archive",
            source_filename=plan.get("filename", ""),
            source_format="zip",
            error=str(exc) or exc.__class__.__name__,
        )
    finally:
        # План больше не нужен: архив лежит в памяти, а результат уже на диске.
        forget_plan_by_payload(plan)


def forget_plan_by_payload(plan: dict[str, Any]) -> None:
    for plan_id, stored in list(_PLANS.items()):
        if stored is plan:
            _PLANS.pop(plan_id, None)


def selected_paths(entries: list[InventoryEntry], requested: list[str] | None) -> list[str]:
    """Сверяет запрошенные пути с описью.

    Путь, которого в описи нет, — это ошибка вызова, а не просьба перевести что-то ещё:
    молча его проглотив, мы вернули бы архив без ожидаемого файла и без объяснения.
    """
    known = {entry.path for entry in entries if entry.selected}
    if requested is None:
        return sorted(known)
    unknown = sorted(set(requested) - {entry.path for entry in entries})
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"В архиве нет файлов: {', '.join(unknown)}",
        )
    return sorted(set(requested))


def archive_source_language() -> str:
    """Язык, с которого всегда идёт перевод. Назван, чтобы страница не угадывала."""
    return SOURCE_LANGUAGE
