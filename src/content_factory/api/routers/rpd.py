"""РПД из программы — временный самодостаточный режим.

Методист загружает утверждённую ДПО-программу (DOCX) → инструмент переносит содержание в РПД по
каркасу-шаблону и отдаёт готовый DOCX; отсутствующие разделы помечены пробелом-вопросом. Модуль
изолирован (свой роутер, своё хранилище под ``.tmp/rpd``), чтобы чисто ретайрить при переходе на
генерацию УП. Бизнес-логика — в пакете :mod:`content_factory.rpd`; здесь только web-обвязка.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import quote

from docx.image.exceptions import UnrecognizedImageError
from docx.image.image import Image as DocxImage
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from content_factory.api.integrations.auth_cookie import validate_request_user
from content_factory.api.integrations.project_paths import GENERATOR_ROOT
from content_factory.catalog.viewer.curriculum_ops import get_curriculum_plan, list_curriculum_plans
from content_factory.catalog.web.deps import get_conn
from content_factory.rpd import RpdBuild, UpPlan, build_rpd_sections, from_registry_plan, parse_up_csv, render_build
from content_factory.rpd.assessment import refresh_final_attestation_form
from content_factory.rpd.build import RpdDocumentFields
from content_factory.rpd.evaluation import evaluate_rpd_build
from content_factory.rpd.llm_runtime import create_rpd_llm_router
from content_factory.rpd.module_evidence import (
    EvidenceKind,
    PlatformEvidenceRequirement,
    synchronize_module_evidence,
    visible_evidence_text,
)
from content_factory.rpd.passport import document_field_updates, merge_document_fields
from content_factory.rpd.quality import evaluate_build_quality
from content_factory.rpd.schedule import refresh_schedule_projection
from content_factory.rpd.translate import russify_up

page_router = APIRouter(prefix="/app/rpd", include_in_schema=False, tags=["rpd-ui"])

RPD_ROOT = (GENERATOR_ROOT / ".tmp" / "rpd").resolve()
MAX_DOCX_BYTES = int(os.getenv("RPD_MAX_DOCX_BYTES", "50000000"))
MAX_EVIDENCE_IMAGE_BYTES = int(os.getenv("RPD_MAX_EVIDENCE_IMAGE_BYTES", "10000000"))
OUTPUT_FILENAME = "РПД.docx"
BUILD_FILE = "build.json"
EVALUATION_FILE = "evaluation.json"
_META_FILE = "rpd_meta.json"
_MAX_RUNS = 12
_MAX_UPLOADS = 10
_MAX_JOBS = 40

_LOCK = Lock()
# Загруженные, но ещё не собранные программы: upload_id -> {work_dir, path, name}.
_UPLOADS: dict[str, dict[str, Any]] = {}
# Готовые прогоны на пользователя: state_key -> [meta, ...] (новые первыми).
_RUNS: dict[str, list[dict[str, Any]]] = {}
# Идущие и только что закончившиеся сборки: run_id -> {state, state_key, name, started_at, error}.
_JOBS: dict[str, dict[str, Any]] = {}
# Ссылки на фоновые задачи: без них сборщик мусора вправе забрать задачу на полпути.
_TASKS: dict[str, asyncio.Task[None]] = {}
_LOADED = False


# --------------------------------------------------------------------------- helpers


def _user_key(user: dict[str, Any]) -> str:
    raw = str(user.get("id") or user.get("email") or user.get("username") or "anonymous")
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in raw)
    return safe[:80] or "anonymous"


def _run_dir(state_key: str, run_id: str) -> Path:
    return (RPD_ROOT / state_key / "runs" / run_id).resolve()


def _safe_name(name: str) -> str:
    base = Path(name).name or "program.docx"
    return "".join(ch for ch in base if ch.isalnum() or ch in {" ", ".", "_", "-", "(", ")"}).strip() or "program.docx"


async def _store_upload(upload: UploadFile, target: Path) -> None:
    total = 0
    with target.open("wb") as handle:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_DOCX_BYTES:
                raise HTTPException(status_code=413, detail="Файл слишком большой")
            handle.write(chunk)


def _evict_uploads() -> None:
    while len(_UPLOADS) > _MAX_UPLOADS:
        oldest = next(iter(_UPLOADS))
        pending = _UPLOADS.pop(oldest, None)
        if pending is not None:
            shutil.rmtree(pending["work_dir"], ignore_errors=True)


def _evict_runs(state_key: str) -> None:
    runs = _RUNS.get(state_key, [])
    while len(runs) > _MAX_RUNS:
        old = runs.pop()
        shutil.rmtree(Path(str(old.get("run_dir", ""))), ignore_errors=True)


def _up_meta(up: UpPlan, prose_generated: bool) -> dict[str, Any]:
    """Лёгкий индекс прогона для панели результата и списка недавних."""
    return {
        "up_title": up.title,
        "modules": len(up.modules),
        "projects": sum(len(m.rows) for m in up.modules),
        "total_hours": up.total_hours,
        "competencies": len(up.competencies()),
        "prose": prose_generated,
    }


def _persist_run(state_key: str, meta: dict[str, Any], run_dir: Path) -> None:
    try:
        (run_dir / _META_FILE).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    with _LOCK:
        runs = _RUNS.setdefault(state_key, [])
        runs[:] = [r for r in runs if r.get("run_id") != meta["run_id"]]
        runs.insert(0, meta)
        _evict_runs(state_key)


def _load_persisted() -> None:
    if not RPD_ROOT.exists():
        return
    by_key: dict[str, list[dict[str, Any]]] = {}
    for meta_path in RPD_ROOT.rglob(_META_FILE):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            state_key = str(meta["state_key"])
        except (OSError, ValueError, KeyError):
            continue
        meta["run_dir"] = str(meta_path.parent)
        by_key.setdefault(state_key, []).append(meta)
    with _LOCK:
        for state_key, metas in by_key.items():
            if state_key in _RUNS:
                continue
            metas.sort(key=lambda m: str(m.get("created_at", "")), reverse=True)
            _RUNS[state_key] = metas[:_MAX_RUNS]


def _ensure_loaded() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    _load_persisted()


def _find_run(state_key: str, run_id: str) -> dict[str, Any] | None:
    with _LOCK:
        return next((r for r in _RUNS.get(state_key, []) if r.get("run_id") == run_id), None)


def _job(run_id: str) -> dict[str, Any] | None:
    with _LOCK:
        job = _JOBS.get(run_id)
        return dict(job) if job is not None else None


def _set_job(run_id: str, **fields: Any) -> None:
    with _LOCK:
        job = _JOBS.get(run_id)
        if job is not None:
            job.update(fields)


def _evict_jobs() -> None:
    while len(_JOBS) > _MAX_JOBS:
        done = next((rid for rid, job in _JOBS.items() if job.get("state") != "running"), None)
        if done is None:  # все идут — ничего не выбрасываем, иначе потеряем ход живой сборки
            return
        _JOBS.pop(done, None)
        _TASKS.pop(done, None)


def _start_build(
    state_key: str,
    run_id: str,
    build: Callable[[], dict[str, Any]],
    name: str,
    cleanup: Callable[[], None] | None = None,
) -> None:
    """Считает сборку в фоне: запрос отвечает сразу, страница следит за ходом по ``/status``.

    Сборка — это два десятка вызовов модели подряд, минуты две. Держать на них HTTP-запрос значит
    показывать методисту пустой экран: он успевает решить, что модуль упал, и перезагрузить страницу,
    а расчёт при этом доходит до конца и пропадает впустую.
    """
    with _LOCK:
        _JOBS[run_id] = {"state": "running", "state_key": state_key, "name": name,
                         "started_at": time.monotonic(), "error": ""}
        _evict_jobs()

    async def runner() -> None:
        try:
            meta = await run_in_threadpool(build)
        except Exception as exc:  # noqa: BLE001 - любой отказ сборки должен дойти до методиста словами
            _set_job(run_id, state="error", error=str(exc) or exc.__class__.__name__)
            return
        finally:
            if cleanup is not None:
                cleanup()
        run_dir = _run_dir(state_key, run_id)
        meta.update({"run_id": run_id, "state_key": state_key, "run_dir": str(run_dir),
                     "created_at": datetime.now(UTC).isoformat(), "name": name})
        _persist_run(state_key, meta, run_dir)
        _set_job(run_id, state="done")

    _TASKS[run_id] = asyncio.create_task(runner())


# --------------------------------------------------------------------------- routes


@page_router.get("")
@page_router.get("/")
async def rpd_home(request: Request) -> HTMLResponse:
    user = await validate_request_user(request)
    _ensure_loaded()
    state_key = _user_key(user)
    return HTMLResponse(_render_screen(state_key, active=None), headers={"Cache-Control": "no-store"})


@page_router.post("/upload")
async def rpd_upload(request: Request, program: UploadFile | None = File(None)) -> Response:
    user = await validate_request_user(request)
    if program is None or not program.filename:
        raise HTTPException(status_code=400, detail="Не выбран файл УП")
    if not program.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Нужен файл УП в формате .csv")
    state_key = _user_key(user)
    work_dir = (RPD_ROOT / state_key / "uploads" / uuid.uuid4().hex).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    name = Path(program.filename).name
    path = work_dir / _safe_name(name)
    await _store_upload(program, path)
    upload_id = uuid.uuid4().hex
    with _LOCK:
        _UPLOADS[upload_id] = {"work_dir": work_dir, "path": path, "name": name}
        _evict_uploads()
    return JSONResponse({"upload_id": upload_id, "name": name})


@page_router.post("/generate")
async def rpd_generate(request: Request, upload_id: str = Form(""), title: str = Form("")) -> Response:
    user = await validate_request_user(request)
    _ensure_loaded()
    state_key = _user_key(user)
    with _LOCK:
        pending = _UPLOADS.pop(upload_id, None)
    if pending is None:
        raise HTTPException(status_code=400, detail="УП не найден — загрузите файл заново")

    run_id = uuid.uuid4().hex
    run_dir = _run_dir(state_key, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    program_name = str(pending["name"])
    program_title = title.strip() or Path(program_name).stem
    source = pending["path"]
    work_dir = pending["work_dir"]
    _start_build(
        state_key,
        run_id,
        lambda: _build_run(source, run_dir, program_title),
        program_title,
        cleanup=lambda: shutil.rmtree(work_dir, ignore_errors=True),
    )
    return JSONResponse({"run_id": run_id, "status_url": f"/app/rpd/{run_id}/status"}, status_code=202)


def _build_run(csv_path: Path, run_dir: Path, program_title: str) -> dict[str, Any]:
    """CSV-источник: разбор УП-CSV → общая сборка."""
    return _build_from_up(parse_up_csv(csv_path, title=program_title), run_dir)


def _build_from_up(up: UpPlan, run_dir: Path) -> dict[str, Any]:
    """Тяжёлая часть (вне event loop): русификация + генерация прозы (LLM) → черновик + рендер на диск."""
    client = _llm_client()
    up = russify_up(up, client)  # англоязычные названия тем/модулей → русский (термины оставляем)
    build = build_rpd_sections(up, None, client)
    rendered = render_build(build, evidence_root=run_dir)
    build.quality = evaluate_build_quality(build, rendered)
    _save_run_artifacts(run_dir, build)
    _atomic_write_bytes(run_dir / OUTPUT_FILENAME, rendered)
    prose_generated = any(
        section.category == "generate" and "[Будет сгенерировано]" not in section.paragraphs
        for section in build.sections
    )
    return _up_meta(up, prose_generated=prose_generated)


def _llm_client() -> Any:
    """Role-aware RPD runtime; None keeps the deterministic draft fallback."""
    return create_rpd_llm_router()


def _load_build(meta: dict[str, Any]) -> RpdBuild | None:
    path = Path(str(meta.get("run_dir", ""))) / BUILD_FILE
    if not path.exists():
        return None
    try:
        return RpdBuild.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save_build(meta: dict[str, Any], build: RpdBuild) -> None:
    _save_run_artifacts(Path(str(meta["run_dir"])), build)


def _save_run_artifacts(run_dir: Path, build: RpdBuild) -> None:
    """Persist the source draft and its derivable pilot metrics as atomic files."""
    _atomic_write_text(run_dir / BUILD_FILE, build.model_dump_json())
    _atomic_write_text(run_dir / EVALUATION_FILE, evaluate_rpd_build(build).model_dump_json(indent=2))


def _atomic_write_text(path: Path, content: str) -> None:
    """Persist JSON atomically so a concurrent reader never observes a partial build."""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    """Persist generated DOCX atomically for the same reason as build.json."""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


async def _read_evidence_image(upload: UploadFile) -> tuple[bytes, str, str]:
    """Read and validate one screenshot using the same parser that will embed it into DOCX."""
    payload = bytearray()
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        payload.extend(chunk)
        if len(payload) > MAX_EVIDENCE_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="Скриншот превышает допустимый размер")
    if not payload:
        raise HTTPException(status_code=400, detail="Файл скриншота пуст")
    try:
        image = DocxImage.from_blob(bytes(payload))
    except (UnrecognizedImageError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Нужен корректный PNG или JPEG") from exc
    if image.content_type not in {"image/png", "image/jpeg"}:
        raise HTTPException(status_code=400, detail="Поддерживаются только PNG и JPEG")
    extension = "png" if image.content_type == "image/png" else "jpg"
    return bytes(payload), extension, image.content_type


def _resolved_run_file(run_dir: Path, relative_path: str) -> Path | None:
    """Resolve persisted evidence paths without allowing a build JSON path traversal."""
    if not relative_path:
        return None
    root = run_dir.resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


@page_router.get("/registry")
async def rpd_registry_list(request: Request, conn: Any = Depends(get_conn)) -> Response:
    """Список УП из Реестра для выбора источника (id + название + часы)."""
    await validate_request_user(request)
    try:
        plans = list_curriculum_plans(conn, limit=50)
    except Exception:  # noqa: BLE001 - Реестр недоступен → пустой список, останется загрузка CSV
        plans = []
    items = [
        {
            "id": plan.get("plan_id") or plan.get("id"),
            "title": _registry_plan_title(plan),
            "hours": plan.get("total_hours") or (plan.get("meta") or {}).get("total_hours"),
        }
        for plan in plans
        if plan.get("plan_id") or plan.get("id")
    ]
    return JSONResponse({"plans": items})


@page_router.post("/registry/{plan_id}")
async def rpd_from_registry(request: Request, plan_id: int, conn: Any = Depends(get_conn)) -> Response:
    """Собрать РПД из выбранного в Реестре УП (без загрузки CSV)."""
    user = await validate_request_user(request)
    _ensure_loaded()
    state_key = _user_key(user)
    plan = get_curriculum_plan(conn, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="УП не найден в Реестре")
    title = _registry_plan_title(plan)
    # Служебное имя старого черновика не должно попадать на обложку новой РПД.
    up = from_registry_plan({**plan, "title": title})

    run_id = uuid.uuid4().hex
    run_dir = _run_dir(state_key, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    _start_build(state_key, run_id, lambda: _build_from_up(up, run_dir), title)
    return JSONResponse({"run_id": run_id, "status_url": f"/app/rpd/{run_id}/status"}, status_code=202)


def _registry_plan_title(plan: dict[str, Any]) -> str:
    """Возвращает содержательное имя УП, включая старые записи с шаблонным title."""
    plan_id = plan.get("plan_id") or plan.get("id")
    stored_title = str(plan.get("title") or "").strip()
    if stored_title and stored_title.casefold() != "черновик учебного плана":
        return stored_title

    # Старые планы сохранялись с одним служебным названием; роль и домен делают их различимыми.
    role = str(plan.get("brief_role") or "").strip()
    domain = str(plan.get("brief_domain") or "").strip()
    meaningful_parts = list(dict.fromkeys(part for part in (role, domain) if part))
    if meaningful_parts:
        return " · ".join(meaningful_parts)
    return f"Учебный план #{plan_id}" if plan_id else "Учебный план без названия"


@page_router.get("/{run_id}/status")
async def rpd_build_status(request: Request, run_id: str) -> JSONResponse:
    """Ход фоновой сборки — по нему страница понимает, ждать дальше или открывать результат."""
    user = await validate_request_user(request)
    _ensure_loaded()
    state_key = _user_key(user)
    job = _job(run_id)
    if job is None or job.get("state_key") != state_key:
        # Сборка давно закончилась и вытеснена из памяти, но результат на месте.
        if _find_run(state_key, run_id) is not None:
            return JSONResponse({"state": "done", "elapsed": 0, "redirect": f"/app/rpd/{run_id}"},
                                headers={"Cache-Control": "no-store"})
        raise HTTPException(status_code=404, detail="Прогон РПД не найден")
    payload: dict[str, Any] = {"state": job["state"], "elapsed": int(time.monotonic() - job["started_at"])}
    if job["state"] == "done":
        payload["redirect"] = f"/app/rpd/{run_id}"
    if job["state"] == "error":
        payload["error"] = job["error"]
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@page_router.get("/{run_id}")
async def rpd_view(request: Request, run_id: str, s: int = 0, check: bool = False) -> HTMLResponse:
    user = await validate_request_user(request)
    _ensure_loaded()
    state_key = _user_key(user)
    meta = _find_run(state_key, run_id)
    if meta is None:
        job = _job(run_id)
        if job is not None and job.get("state_key") == state_key and job["state"] != "done":
            return HTMLResponse(_render_waiting(run_id, job), headers={"Cache-Control": "no-store"})
        raise HTTPException(status_code=404, detail="Прогон РПД не найден")
    build = _load_build(meta)
    if build is None or not build.sections:  # старый прогон без черновика — итог + скачать
        return HTMLResponse(_render_screen(state_key, active=meta), headers={"Cache-Control": "no-store"})
    if check:  # валидация — по явной кнопке, а не на каждом сохранении
        build.quality = evaluate_build_quality(build)
        _save_build(meta, build)
    return HTMLResponse(_render_editor(run_id, build, meta, s, checked=check), headers={"Cache-Control": "no-store"})


@page_router.post("/{run_id}/section/{index}")
async def rpd_save_section(request: Request, run_id: str, index: int) -> Response:
    user = await validate_request_user(request)
    _ensure_loaded()
    state_key = _user_key(user)
    meta = _find_run(state_key, run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Прогон РПД не найден")
    build = _load_build(meta)
    if build is not None and 0 <= index < len(build.sections):
        form = await request.form()
        try:
            _require_current_revision(form.get("revision"), build)
        except HTTPException as exc:
            if exc.status_code == 409:
                return _render_revision_conflict(run_id, build)
            raise
        section = build.sections[index]
        if section.editable:
            if section.kind == "table":
                rows = _table_from_json(str(form.get("table_json", "")))
                if rows:  # пустая форма (не та секция) не должна стирать таблицу
                    section.table_rows = rows
                    if section.category == "passport":
                        # The passport table is the visible editor; mirror its semantic values to
                        # document metadata so cover bindings and repeated summary tables stay aligned.
                        updates = document_field_updates(rows, build.template_profile_id)
                        build.document_fields = build.document_fields.model_copy(update=updates)
                        _refresh_schedule_from_metadata(build)
            elif section.kind == "module_blocks":
                module_updates = _module_blocks_from_json(str(form.get("module_blocks_json", "")))
                for block in section.module_blocks:
                    block_updates = module_updates.get(block.module_index, {})
                    passport_rows = block_updates.get("passport")
                    topics_rows = block_updates.get("topics")
                    if passport_rows:
                        block.passport_rows = passport_rows
                    if topics_rows:
                        block.topics_rows = topics_rows
            else:
                text = str(form.get("text", ""))
                section.paragraphs = [line.strip() for line in text.splitlines() if line.strip()] or [""]
        section.done = bool(form.get("done"))
        if section.editable:
            section.flag = ""  # явная правка методиста закрывает прежнее замечание LLM-критика
        build.revision += 1
        build.quality = evaluate_build_quality(build)
        _save_build(meta, build)
    return RedirectResponse(f"/app/rpd/{run_id}?s={index}", status_code=303)


@page_router.post("/{run_id}/metadata")
async def rpd_save_metadata(request: Request, run_id: str) -> Response:
    """Save cover/passport bindings that cannot be sourced from the curriculum plan."""
    user = await validate_request_user(request)
    _ensure_loaded()
    state_key = _user_key(user)
    meta = _find_run(state_key, run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Прогон РПД не найден")
    build = _load_build(meta)
    if build is None:
        raise HTTPException(status_code=404, detail="Черновик РПД не найден")

    form = await request.form()
    try:
        _require_current_revision(form.get("revision"), build)
    except HTTPException as exc:
        if exc.status_code == 409:
            return _render_revision_conflict(run_id, build)
        raise
    allowed = {name for name, _label in RpdDocumentFields.required_fields()}
    updates = {name: str(form.get(name, "")).strip() for name in allowed}
    build.document_fields = build.document_fields.model_copy(update=updates)
    passport = next((section for section in build.sections if section.category == "passport"), None)
    if passport is not None:
        # Metadata edits use the same contract in the opposite direction; no second source of truth.
        passport.table_rows = merge_document_fields(
            passport.table_rows,
            build.document_fields,
            build.template_profile_id,
        )
    _refresh_schedule_from_metadata(build)
    build.revision += 1
    build.quality = evaluate_build_quality(build)
    _save_build(meta, build)
    return RedirectResponse(f"/app/rpd/{run_id}", status_code=303)


def _refresh_schedule_from_metadata(build: RpdBuild) -> None:
    """Keep untouched 3.2 and 4.2 projections aligned with canonical passport metadata."""
    _refresh_final_attestation_from_metadata(build)
    trace = build.schedule_trace
    schedule = next((section for section in build.sections if section.category == "up_grafik"), None)
    if trace is None or schedule is None or schedule.kind != "table":
        return
    if schedule.initial_content_hash and schedule.content_hash() != schedule.initial_content_hash:
        return  # a methodologist-owned edit must never be overwritten by metadata synchronization
    before = schedule.content_hash()
    refreshed = refresh_schedule_projection(
        schedule.table_rows,
        duration=build.document_fields.duration,
        final_attestation=build.document_fields.final_attestation,
        trace=trace,
    )
    schedule.table_header = list(refreshed.header)
    schedule.table_rows = [list(row) for row in refreshed.rows]
    build.schedule_trace = refreshed.trace
    after = schedule.content_hash()
    if after != before:
        schedule.done = False
        schedule.flag = ""
    schedule.initial_content_hash = after


def _refresh_final_attestation_from_metadata(build: RpdBuild) -> None:
    """Update only the canonical 4.2 form while preserving methodologist-owned table edits."""
    section = next(
        (item for item in build.sections if item.category == "final_attestation"),
        None,
    )
    if section is None or section.kind != "table":
        return
    if section.initial_content_hash and section.content_hash() != section.initial_content_hash:
        return
    before = section.content_hash()
    section.table_rows = refresh_final_attestation_form(
        section.table_rows,
        build.document_fields.final_attestation,
    )
    if build.assessment_trace is not None:
        form = next(
            (
                row[1]
                for row in section.table_rows
                if len(row) >= 2 and row[0].strip().casefold() == "форма"
            ),
            "",
        )
        build.assessment_trace.final_control_form = form
    after = section.content_hash()
    if after != before:
        section.done = False
        section.flag = ""
    section.initial_content_hash = after


def _require_current_revision(raw_revision: Any, build: RpdBuild) -> None:
    """Reject stale editor submissions instead of overwriting a newer revision."""
    try:
        submitted = int(str(raw_revision))
    except (TypeError, ValueError):
        submitted = -1
    if submitted != build.revision:
        raise HTTPException(
            status_code=409,
            detail="Черновик уже изменён в другой вкладке. Обновите страницу и повторите правку.",
        )


def _render_revision_conflict(run_id: str, build: RpdBuild) -> HTMLResponse:
    """Render an actionable conflict instead of exposing FastAPI's JSON error page."""
    safe_run_id = escape(run_id)
    html = f"""<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Черновик изменён</title>
<style>body{{font-family:system-ui,sans-serif;background:#f4f7f5;color:#15221d;padding:40px}}
.card{{max-width:650px;margin:auto;background:#fff;border:1px solid #dde5e1;border-radius:14px;padding:28px}}
.actions{{display:flex;gap:12px;margin-top:22px;flex-wrap:wrap}}a{{padding:10px 16px;border-radius:9px;
text-decoration:none;background:#0e8f6f;color:#fff}}a.secondary{{background:#fff;color:#0e8f6f;border:1px solid #0e8f6f}}
</style></head><body><main class="card"><h1>Черновик уже изменён</h1>
<p>Другая вкладка или пользователь сохранили более новую редакцию № {build.revision}.</p>
<p>Обновите редактор, проверьте актуальные данные и повторите правку. Ваше устаревшее сохранение не применено.</p>
<div class="actions"><a href="/app/rpd/{safe_run_id}">Открыть актуальную редакцию</a>
<a class="secondary" href="javascript:history.back()">Вернуться назад</a></div></main></body></html>"""
    return HTMLResponse(html, status_code=409, headers={"Cache-Control": "no-store"})


def _table_from_json(raw: str) -> list[list[str]]:
    """Разбирает сериализованную редактором таблицу (JSON-массив строк-массивов) в строки."""
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    rows: list[list[str]] = []
    for row in data:
        if isinstance(row, list):
            rows.append([str(cell) for cell in row])
    return rows


def _module_blocks_from_json(raw: str) -> dict[int, dict[str, list[list[str]]]]:
    """Validate the browser payload for editable passport/topics tables in section 3.3.1."""
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    result: dict[int, dict[str, list[list[str]]]] = {}
    for raw_index, raw_tables in data.items():
        try:
            module_index = int(raw_index)
        except (TypeError, ValueError):
            continue
        if module_index < 1 or not isinstance(raw_tables, dict):
            continue
        tables: dict[str, list[list[str]]] = {}
        for role in ("passport", "topics"):
            raw_rows = raw_tables.get(role)
            if not isinstance(raw_rows, list):
                continue
            rows = [
                [str(cell) for cell in row]
                for row in raw_rows
                if isinstance(row, list)
            ]
            if rows:
                tables[role] = rows
        if tables:
            result[module_index] = tables
    return result


@page_router.post("/{run_id}/evidence/{evidence_id}")
async def rpd_upload_evidence(
    request: Request,
    run_id: str,
    evidence_id: str,
    screenshot: UploadFile | None = File(None),
    revision: str = Form(""),
    return_section: int = Form(-1),
) -> Response:
    """Attach one assignment/control screenshot to its immutable evidence requirement."""
    user = await validate_request_user(request)
    _ensure_loaded()
    state_key = _user_key(user)
    meta = _find_run(state_key, run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Прогон РПД не найден")
    build = _load_build(meta)
    if build is None:
        raise HTTPException(status_code=404, detail="Черновик РПД не найден")
    try:
        _require_current_revision(revision, build)
    except HTTPException as exc:
        if exc.status_code == 409:
            return _render_revision_conflict(run_id, build)
        raise
    requirement = next(
        (item for item in build.module_evidence if item.evidence_id == evidence_id),
        None,
    )
    if requirement is None:
        raise HTTPException(status_code=404, detail="Требование к скриншоту не найдено")
    if screenshot is None or not screenshot.filename:
        raise HTTPException(status_code=400, detail="Не выбран файл скриншота")

    payload, extension, content_type = await _read_evidence_image(screenshot)
    run_dir = Path(str(meta["run_dir"])).resolve()
    relative_path = f"evidence/{requirement.evidence_id}.{extension}"
    target = _resolved_run_file(run_dir, relative_path)
    if target is None:  # defensive guard; the generated relative path is expected to be safe
        raise HTTPException(status_code=400, detail="Некорректный путь скриншота")
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(target, payload)

    # Persist only relative, content-addressed metadata; the binary remains in the scoped run dir.
    requirement.status = "uploaded"
    requirement.relative_path = relative_path
    requirement.original_filename = _safe_name(screenshot.filename)
    requirement.content_type = content_type
    requirement.sha256 = hashlib.sha256(payload).hexdigest()
    module_index = next(
        (index for index, section in enumerate(build.sections) if section.category == "up_modules"),
        0,
    )
    module_section = build.sections[module_index] if build.sections else None
    if module_section is not None and module_section.category == "up_modules":
        synchronize_module_evidence(module_section.module_blocks, requirement)
        module_section.done = False
        module_section.flag = ""
    assessment_index = next(
        (
            index
            for index, section in enumerate(build.sections)
            if section.category == "assessment_evidence"
        ),
        -1,
    )
    if requirement.kind == "control" and assessment_index >= 0:
        build.sections[assessment_index].done = False
    build.revision += 1
    build.quality = evaluate_build_quality(build)
    _save_build(meta, build)
    redirect_index = (
        return_section
        if 0 <= return_section < len(build.sections)
        and build.sections[return_section].category in {"up_modules", "assessment_evidence"}
        else module_index
    )
    return RedirectResponse(f"/app/rpd/{run_id}?s={redirect_index}", status_code=303)


@page_router.get("/{run_id}/evidence/{evidence_id}")
async def rpd_view_evidence(request: Request, run_id: str, evidence_id: str) -> Response:
    """Serve an authenticated screenshot preview from the current user's run directory."""
    user = await validate_request_user(request)
    _ensure_loaded()
    meta = _find_run(_user_key(user), run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Прогон РПД не найден")
    build = _load_build(meta)
    requirement = next(
        (item for item in build.module_evidence if item.evidence_id == evidence_id),
        None,
    ) if build is not None else None
    if requirement is None or requirement.status != "uploaded":
        raise HTTPException(status_code=404, detail="Скриншот не найден")
    path = _resolved_run_file(Path(str(meta["run_dir"])), requirement.relative_path)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Файл скриншота не найден")
    return Response(
        content=path.read_bytes(),
        media_type=requirement.content_type,
        headers={"Cache-Control": "private, max-age=300"},
    )


@page_router.get("/{run_id}/download")
async def rpd_download(request: Request, run_id: str, draft: bool = False) -> Response:
    user = await validate_request_user(request)
    _ensure_loaded()
    state_key = _user_key(user)
    meta = _find_run(state_key, run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Прогон РПД не найден")
    build = _load_build(meta)
    if build is not None:  # рендерим из (отредактированного) черновика — свежий DOCX с правками
        run_dir = Path(str(meta["run_dir"])).resolve()
        data = await run_in_threadpool(render_build, build, None, run_dir)
        build.quality = evaluate_build_quality(build, data)
        _save_build(meta, build)
        if not draft and not build.quality.ready:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "РПД не прошла quality gate; скачайте черновик или устраните замечания.",
                    "blocking_count": build.quality.blocking_count,
                    "issues": [issue.model_dump() for issue in build.quality.issues if issue.blocking],
                    "draft_url": f"/app/rpd/{run_id}/download?draft=true",
                },
            )
    else:
        path = (Path(str(meta.get("run_dir", ""))) / OUTPUT_FILENAME).resolve()
        if not path.exists():
            raise HTTPException(status_code=404, detail="Файл РПД не найден")
        data = path.read_bytes()
    suffix = "_РПД_черновик.docx" if draft else "_РПД.docx"
    download = f"{Path(str(meta.get('name', 'РПД'))).stem}{suffix}"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(download)}"},
    )


@page_router.get("/{run_id}/evaluation")
async def rpd_evaluation(request: Request, run_id: str) -> JSONResponse:
    """Return the latest reproducible metrics artifact for pilot aggregation."""
    user = await validate_request_user(request)
    _ensure_loaded()
    meta = _find_run(_user_key(user), run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Прогон РПД не найден")
    build = _load_build(meta)
    if build is None:
        raise HTTPException(status_code=404, detail="Черновик РПД не найден")
    artifact = evaluate_rpd_build(build)
    _atomic_write_text(Path(str(meta["run_dir"])) / EVALUATION_FILE, artifact.model_dump_json(indent=2))
    return JSONResponse(artifact.model_dump(mode="json"))


@page_router.post("/{run_id}/delete")
async def rpd_delete(request: Request, run_id: str) -> RedirectResponse:
    user = await validate_request_user(request)
    _ensure_loaded()
    state_key = _user_key(user)
    with _LOCK:
        runs = _RUNS.get(state_key, [])
        meta = next((r for r in runs if r.get("run_id") == run_id), None)
        if meta is not None:
            runs.remove(meta)
            shutil.rmtree(Path(str(meta.get("run_dir", ""))), ignore_errors=True)
    return RedirectResponse("/app/rpd", status_code=303)


# --------------------------------------------------------------------------- rendering


_LABEL_CLASS = {
    "из УП": "up",
    "УП + агенты": "up",
    "сгенерировано": "gen",
    "с платформы": "gap",
    "методист": "gap",
}


def _label_chip(label: str) -> str:
    if not label:
        return ""
    return f'<span class="cs {_LABEL_CLASS.get(label, "form")}">{escape(label)}</span>'


def _render_table_editor(section: Any) -> str:
    table = _render_editable_grid(
        section.table_header,
        section.table_rows,
        table_id="edit-table",
        table_kind="single",
    )
    return table + '<input type="hidden" name="table_json" id="table-json">'


def _render_editable_grid(
    header: list[str],
    rows: list[list[str]],
    *,
    table_id: str,
    table_kind: str,
    module_index: int = 0,
    role: str = "",
) -> str:
    """Render one editable table surface with explicit serialization metadata."""
    row_widths = [len(row) for row in rows]
    ncols = max([len(header), *row_widths], default=1)
    ths = "".join(f"<th>{escape(str(h))}</th>" for h in header) + "<th></th>"
    body = []
    for row in rows:
        padded = [str(cell) for cell in row] + [""] * (ncols - len(row))
        cells = "".join(f'<td><textarea rows="1">{escape(cell)}</textarea></td>' for cell in padded)
        body.append(f'<tr>{cells}<td class="delc"><button type="button" class="delrow" title="Удалить строку">×</button></td></tr>')
    return (
        f'<div class="tblwrap"><table class="edit" id="{table_id}" data-rpd-table="{table_kind}" '
        f'data-ncols="{ncols}" data-module-index="{module_index}" data-role="{escape(role)}">'
        f'<thead><tr>{ths}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div>'
        f'<button type="button" class="addrow" data-table-target="{table_id}">+ строка</button>'
    )


def _render_module_blocks_editor(section: Any) -> str:
    """Mirror the internal DOCX: two editable tables and one screenshot area per module."""
    modules: list[str] = []
    for block_index, block in enumerate(section.module_blocks):
        passport_id = f"module-{block.module_index}-passport"
        topics_id = f"module-{block.module_index}-topics"
        passport = _render_editable_grid(
            block.passport_header,
            block.passport_rows,
            table_id=passport_id,
            table_kind="module",
            module_index=block.module_index,
            role="passport",
        )
        topics = _render_editable_grid(
            block.topics_header,
            block.topics_rows,
            table_id=topics_id,
            table_kind="module",
            module_index=block.module_index,
            role="topics",
        )
        evidence_items = "".join(
            f'<div class="module-evidence-item">{escape(visible_evidence_text(row[0]))}</div>'
            for row in block.evidence_rows
            if row
        )
        if not evidence_items:
            evidence_items = '<div class="module-evidence-item empty">Скриншоты для этого модуля не требуются.</div>'
        modules.append(
            f'<details class="module-program"{" open" if block_index == 0 else ""}>'
            f'<summary>Модуль {block.module_index}: {escape(block.module_title)}</summary>'
            '<h3>Паспорт учебного модуля</h3>'
            f'{passport}<h3>Темы и виды работ</h3>{topics}'
            '<section class="module-evidence-area">'
            f'<div class="module-evidence-title">{escape(block.evidence_header[0])}</div>'
            f'{evidence_items}</section></details>'
        )
    return (
        '<div class="module-programs">' + "".join(modules) + "</div>"
        '<input type="hidden" name="module_blocks_json" id="module-blocks-json">'
    )


def _render_readonly_section(section: Any) -> str:
    """Render profile-owned template content without writable form controls."""
    if section.kind == "table":
        header = "".join(
            f"<th>{escape(visible_evidence_text(str(cell)))}</th>" for cell in section.table_header
        )
        rows = "".join(
            "<tr>"
            + "".join(
                f"<td>{escape(visible_evidence_text(str(cell)))}</td>" for cell in row
            )
            + "</tr>"
            for row in section.table_rows
        )
        return (
            '<div class="readonly-section" data-readonly="template">'
            f'<div class="tblwrap"><table class="readonly-table"><thead><tr>{header}</tr></thead>'
            f"<tbody>{rows}</tbody></table></div></div>"
        )
    items = "".join(f"<li>{escape(str(paragraph))}</li>" for paragraph in section.paragraphs)
    return f'<div class="readonly-section" data-readonly="template"><ul>{items}</ul></div>'


def _render_competency_trace(build: RpdBuild) -> str:
    """Expose agent recommendations and deterministic coverage next to the editable table."""
    trace = build.competency_trace
    if trace is None:
        return ""
    coverage = (
        f"{trace.source_coverage_rate:.0%}"
        if trace.source_coverage_rate is not None
        else "нет исходных навыков"
    )
    status_labels = {
        "validated": "проверено",
        "repaired": "исправлено и проверено",
        "fallback": "безопасный fallback",
        "not_run": "не запускалось",
    }
    matches = "".join(
        "<li>"
        f"<b>{escape(match.standard_code)}</b> — {escape(match.reason)} "
        f"(уверенность {match.confidence:.0%})"
        + (
            "; функции: " + escape(", ".join(match.relevant_function_codes))
            if match.relevant_function_codes
            else ""
        )
        + "</li>"
        for match in trace.matches
    )
    if matches:
        recommendation = (
            "<p>Рекомендации матчера — не нормативное утверждение. Проверьте профстандарт и "
            "подтвердите основание в таблице.</p><ul>" + matches + "</ul>"
        )
    elif trace.candidate_standard_codes:
        recommendation = "<p>Содержательно подходящий профстандарт не подтверждён матчер-критерием.</p>"
    else:
        recommendation = (
            "<p>Локальный снимок профстандартов не настроен или не дал кандидатов. "
            "Основание требуется заполнить и подтвердить методисту.</p>"
        )
    approved = (
        "<p>Одобрены методистом: "
        + escape(", ".join(trace.approved_standard_codes))
        + ".</p>"
        if trace.approved_standard_codes
        else ""
    )
    return (
        '<aside class="quality neutral"><div class="quality-title">Контур компетенций</div>'
        '<div class="quality-metrics">'
        f"Семейство: {escape(trace.family)} · статус: "
        f"{escape(status_labels.get(trace.status, trace.status))} · покрытие навыков УП: {coverage}"
        f"</div>{recommendation}{approved}</aside>"
    )


def _render_module_evidence(
    run_id: str,
    build: RpdBuild,
    *,
    evidence_kind: EvidenceKind | None = None,
    return_section: int = 0,
) -> str:
    """Render a module-grouped upload checklist outside the editable-table form."""
    requirements = [
        item
        for item in build.module_evidence
        if evidence_kind is None or item.kind == evidence_kind
    ]
    panel_title = (
        "Скриншоты примеров оценки по модулям"
        if evidence_kind == "control"
        else "Скриншоты заданий и контролей"
    )
    if not requirements:
        return (
            f'<aside class="evidence-panel"><div class="quality-title">{panel_title}</div>'
            '<p class="evidence-empty">Реестр скриншотов отсутствует — пересоберите РПД из исходного УП.</p></aside>'
        )
    uploaded = sum(item.status == "uploaded" for item in requirements)
    groups: dict[tuple[int, str], list[PlatformEvidenceRequirement]] = {}
    for requirement in requirements:
        groups.setdefault((requirement.module_index, requirement.module_title), []).append(requirement)

    modules: list[str] = []
    for group_index, ((module_index, module_title), items) in enumerate(groups.items()):
        cards: list[str] = []
        for requirement in items:
            safe_id = escape(requirement.evidence_id)
            kind = "Задание" if requirement.kind == "assignment" else "Контроль"
            subject = escape(requirement.subject)
            status = "Приложен" if requirement.status == "uploaded" else "Требуется"
            status_class = "ok" if requirement.status == "uploaded" else "pending"
            preview = (
                f'<img loading="lazy" src="/app/rpd/{run_id}/evidence/{safe_id}" '
                f'alt="Скриншот: {subject}">'
                if requirement.status == "uploaded"
                else ""
            )
            upload_label = "Заменить скриншот" if requirement.status == "uploaded" else "Загрузить скриншот"
            cards.append(
                '<article class="evidence-card">'
                f'<div class="evidence-card-head"><b>{kind}</b><span class="evidence-status {status_class}">{status}</span></div>'
                f'<div class="evidence-subject">{subject}</div>{preview}'
                f'<form method="post" enctype="multipart/form-data" action="/app/rpd/{run_id}/evidence/{safe_id}">'
                f'<input type="hidden" name="revision" value="{build.revision}">'
                f'<input type="hidden" name="return_section" value="{return_section}">'
                '<input required name="screenshot" type="file" accept="image/png,image/jpeg">'
                f'<button class="evidence-upload" type="submit">{upload_label}</button></form></article>'
            )
        modules.append(
            f'<details class="evidence-module"{" open" if group_index == 0 else ""}>'
            f'<summary>Модуль {module_index}: {escape(module_title)} · {sum(item.status == "uploaded" for item in items)}/{len(items)}</summary>'
            f'<div class="evidence-grid">{"".join(cards)}</div></details>'
        )
    requirement_text = (
        "В разделе 4.4 требуется отдельный скриншот каждого контроля каждого модуля."
        if evidence_kind == "control"
        else "Для каждого задания из LMS/ЭИОС нужны отдельные скрины самого задания и формы контроля."
    )
    return (
        f'<aside class="evidence-panel"><div class="quality-title">{panel_title}</div>'
        f'<p class="evidence-summary">Приложено {uploaded} из {len(requirements)}. '
        f"{requirement_text}</p>"
        f'{"".join(modules)}</aside>'
    )


def _render_metadata_form(run_id: str, build: RpdBuild) -> str:
    fields = build.document_fields
    inputs = []
    for name, label in RpdDocumentFields.required_fields():
        value = escape(str(getattr(fields, name)))
        inputs.append(
            f'<label class="meta-field"><span>{escape(label)}</span>'
            f'<input name="{name}" value="{value}"></label>'
        )
    return (
        '<details class="meta"><summary>Реквизиты обложки и паспорта</summary>'
        f'<form method="post" action="/app/rpd/{run_id}/metadata">'
        f'<input type="hidden" name="revision" value="{build.revision}">'
        f'<div class="meta-grid">{"".join(inputs)}</div>'
        '<button class="btn" type="submit">Сохранить реквизиты</button></form></details>'
    )


def _render_quality_summary(build: RpdBuild) -> str:
    report = build.quality
    evaluation = evaluate_rpd_build(build)
    state = "ready" if report.ready else "blocked"
    title = "РПД готова к финальному экспорту" if report.ready else f"Финальный экспорт заблокирован: {report.blocking_count}"
    items = "".join(
        f'<li><b>{escape(issue.code)}</b> — {escape(issue.message)}'
        f'{" · " + escape(issue.section_key) if issue.section_key else ""}</li>'
        for issue in report.issues[:20]
    )
    details = f'<ul>{items}</ul>' if items else ""
    acceptance = (
        f"{evaluation.accepted_without_edits_rate:.0%}"
        if evaluation.accepted_without_edits_rate is not None
        else "нет подтверждённых LLM-разделов"
    )
    metrics = (
        f'<div class="quality-metrics">Профиль: {escape(build.template_profile_id)} · '
        f'LLM-вызовов: {evaluation.llm_calls} · ошибок: {evaluation.llm_failed_calls} · '
        f'принято без правок: {escape(acceptance)}</div>'
    )
    return (
        f'<section class="quality {state}"><div class="quality-title">{escape(title)}</div>'
        f"{metrics}{details}</section>"
    )


def _render_export_actions(run_id: str, build: RpdBuild) -> str:
    draft = f'<a class="btn ghost" href="/app/rpd/{run_id}/download?draft=true">Скачать черновик DOCX</a>'
    evaluation = f'<a class="btn ghost" href="/app/rpd/{run_id}/evaluation">Метрики JSON</a>'
    if build.quality.ready:
        final = f'<a class="btn" href="/app/rpd/{run_id}/download">Скачать финальную РПД</a>'
    else:
        final = '<span class="btn disabled" title="Закройте блокирующие замечания">Финальный экспорт недоступен</span>'
    return evaluation + draft + final


def _render_check_prompt(run_id: str, active: int, done: int, total: int) -> str:
    """Компактная плашка вместо стены замечаний: валидация — по явной кнопке."""
    return (
        '<section class="quality neutral"><div class="quality-title">'
        f"Готово {done} из {total} разделов</div>"
        '<div class="quality-metrics">Пройдите разделы и отметьте «готово». '
        "Когда закончите — проверьте готовность к финальному экспорту.</div>"
        f'<a class="btn" href="/app/rpd/{run_id}?check=1&s={active}">Проверить готовность</a></section>'
    )


def _render_editor(run_id: str, build: RpdBuild, meta: dict[str, Any], active: int, checked: bool = False) -> str:
    sections = build.sections
    total = len(sections)
    active = max(0, min(active, total - 1))
    section = sections[active]
    if section.category == "up_modules":
        evidence_panel = _render_module_evidence(run_id, build, return_section=active)
    elif section.category == "assessment_evidence":
        evidence_panel = _render_module_evidence(
            run_id,
            build,
            evidence_kind="control",
            return_section=active,
        )
    else:
        evidence_panel = ""

    nav = "".join(
        '<a class="navitem{on}" href="/app/rpd/{rid}?s={i}">'
        '<span class="ndone">{done}</span><span class="nnum">{num}</span>'
        '<span class="ntitle">{title}</span>{flag}{chip}</a>'.format(
            on=" on" if i == active else "",
            rid=run_id,
            i=i,
            done="✓" if sec.done else "",
            num=escape(sec.number),
            title=escape(sec.title),
            flag=('<span class="nflag" title="Замечание валидатора">⚠</span>' if sec.flag else ""),
            chip=_label_chip(sec.label),
        )
        for i, sec in enumerate(sections)
    )
    done_count = sum(1 for sec in sections if sec.done)
    flag_note = (f'<p class="flagnote">⚠ Валидатор: {escape(section.flag)}</p>' if section.flag else "")

    if not section.editable:
        editor = _render_readonly_section(section)
        hint = (
            "Загрузите скриншоты контроля по каждому модулю; один и тот же файл используется "
            "в рабочей программе модуля и в примерах оценки."
            if section.category == "assessment_evidence"
            else (
                "Раздел задан профилем шаблона. Проверьте нормативные основания и отметьте «Готово»; "
                "при экспорте сохраняется исходная вёрстка DOCX."
            )
        )
    elif section.kind == "module_blocks":
        editor = _render_module_blocks_editor(section)
        hint = (
            "Для каждого модуля проверьте паспорт, темы и виды работ, затем загрузите "
            "отдельные скриншоты задания и контроля в области ниже."
        )
    elif section.kind == "table":
        editor = _render_table_editor(section)
        if section.category == "up_comp":
            editor = _render_competency_trace(build) + editor
        hint = (
            "Проверьте рабочие программы и загрузите отдельные скриншоты задания и контроля ниже."
            if section.category == "up_modules"
            else "Правьте ячейки; «+ строка» добавляет, «×» удаляет строку."
        )
    else:
        editor = f'<textarea name="text" class="prosein" rows="14">{escape(chr(10).join(section.paragraphs))}</textarea>'
        hint = "Один абзац — одна строка. Пустые строки игнорируются."

    if section.editable and section.preserve_template:
        # Раздел формы ДПО: правки необязательны и заменяют дословную вёрстку только если внесены.
        hint = (
            "Раздел из формы ДПО. Не трогаете — при экспорте сохранится исходная вёрстка шаблона; "
            "любые правки заменят её вашим текстом."
        )

    prev_btn = (f'<a class="navbtn" href="/app/rpd/{run_id}?s={active - 1}">← Назад</a>'
                if active > 0 else '<span class="navbtn off">← Назад</span>')
    next_btn = (f'<a class="navbtn" href="/app/rpd/{run_id}?s={active + 1}">Далее →</a>'
                if active < total - 1 else '<span class="navbtn off">Далее →</span>')
    name = escape(str(meta.get("name") or "РПД"))
    quality_block = (
        _render_quality_summary(build) if checked
        else _render_check_prompt(run_id, active, done_count, total)
    )

    return _EDITOR_TEMPLATE.format(
        run_id=run_id,
        name=name,
        nav=nav,
        pos=active + 1,
        total=total,
        done_count=done_count,
        quality_summary=quality_block,
        metadata_form=_render_metadata_form(run_id, build),
        number=escape(section.number),
        title=escape(section.title),
        chip=_label_chip(section.label),
        hint=escape(hint),
        flag_note=flag_note,
        evidence_panel=evidence_panel,
        index=active,
        revision=build.revision,
        editor=editor,
        save_label="Подтвердить раздел" if not section.editable else "Сохранить раздел",
        done_checked="checked" if section.done else "",
        prev_btn=prev_btn,
        next_btn=next_btn,
        export_actions=_render_export_actions(run_id, build),
    )


def _render_screen(state_key: str, active: dict[str, Any] | None) -> str:
    with _LOCK:
        recent = list(_RUNS.get(state_key, []))
    return _SCREEN_TEMPLATE.format(
        result=_render_result(active),
        recent=_render_recent(recent, active),
    )


def _render_waiting(run_id: str, job: dict[str, Any]) -> str:
    """Страница идущей сборки: открыть ссылку на прогон можно и до готовности."""
    safe_run_id = escape(run_id)
    name = escape(str(job.get("name") or "РПД"))
    failed = job.get("state") == "error"
    note = escape(str(job.get("error") or "")) if failed else ""
    return _WAITING_TEMPLATE.format(run_id=safe_run_id, name=name,
                                    hidden="" if failed else " hidden", error=note)


def _render_result(meta: dict[str, Any] | None) -> str:
    if meta is None:
        return ""
    run_id = escape(str(meta.get("run_id", "")))
    modules = int(meta.get("modules", 0) or 0)
    projects = int(meta.get("projects", 0) or 0)
    hours = escape(str(meta.get("total_hours") or "—"))
    comps = int(meta.get("competencies", 0) or 0)
    prose = bool(meta.get("prose"))
    up_ok = modules > 0

    stats = [("Модулей", modules), ("Проектов", projects), ("Часов", hours), ("Компетенций", comps)]
    tiles = "".join(
        f'<div class="stat"><div class="statv">{escape(str(v))}</div><div class="statl">{escape(k)}</div></div>'
        for k, v in stats
    )

    prose_chip = ('<span class="cs gen">✦ сгенерировано</span>' if prose
                  else '<span class="cs wait">⋯ плейсхолдер</span>')
    up_chip = '<span class="cs up">✓ из УП</span>' if up_ok else '<span class="cs gap">? нет данных УП</span>'
    catalog = [
        ("Учебный план · график · модули", up_chip),
        ("Профессиональные компетенции", up_chip),
        ("Актуальность · цели · паспорт", prose_chip),
        ("Нормативка · форма · условия", '<span class="cs form">форма ДПО</span>'),
        ("Оценочные материалы · разработчики", '<span class="cs gap">? методист</span>'),
        ("Примеры и скрины заданий", '<span class="cs gap">? с платформы</span>'),
    ]
    rows = "".join(f'<li><span class="nm">{escape(name)}</span>{chip}</li>' for name, chip in catalog)

    return f"""
    <section class="result">
      <div class="stats">{tiles}</div>
      <div class="cattitle">Что вошло в РПД</div>
      <ul class="cats">{rows}</ul>
      <p class="gapnote">Разделы «? методист» и «? с платформы» (примеры заданий, оценочные материалы)
        заполните вручную — авто их не закрывает.</p>
      <div class="actions">
        <a class="btn is-ready" href="/app/rpd/{run_id}/download">Скачать РПД (DOCX)</a>
      </div>
    </section>"""


def _render_recent(recent: list[dict[str, Any]], active: dict[str, Any] | None) -> str:
    if not recent:
        return ""
    active_id = active.get("run_id") if active else None
    rows = []
    for meta in recent[:_MAX_RUNS]:
        run_id = escape(str(meta.get("run_id", "")))
        name = escape(str(meta.get("name") or "Программа"))
        created = escape(str(meta.get("created_at") or "").replace("T", " ")[:16])
        hours = escape(str(meta.get("total_hours") or "—"))
        is_active = ' style="font-weight:700"' if meta.get("run_id") == active_id else ""
        rows.append(
            '<li style="display:flex;align-items:center;justify-content:space-between;gap:12px;">'
            f'<span><a href="/app/rpd/{run_id}"{is_active}>{name}</a>'
            f'<span class="muted"> · {hours} ч · {created}</span></span>'
            f'<form method="post" action="/app/rpd/{run_id}/delete" style="margin:0" '
            "onsubmit=\"return confirm('Удалить этот РПД?');\">"
            '<button type="submit" title="Удалить" class="del">&times;</button></form></li>'
        )
    return ('<section class="recent"><div class="rt">Недавние РПД</div>'
            f'<ul>{"".join(rows)}</ul></section>')


_WAITING_TEMPLATE = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>РПД — сборка идёт</title>
<style>
 :root{{--accent:#0e8f6f;--ink:#15221d;--muted:#5c6b64;--line:#dde5e1}}
 body{{font-family:system-ui,'Segoe UI',sans-serif;margin:0;background:#f4f7f5;color:var(--ink);
   display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px}}
 .card{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:28px 32px;max-width:520px;width:100%}}
 h1{{font-size:1.25rem;margin:0 0 .3rem}}
 .name{{color:var(--muted);margin:0 0 18px;font-size:.92rem}}
 .bar{{height:8px;background:#e8efeb;border-radius:999px;overflow:hidden;margin:16px 0 10px}}
 .bar i{{display:block;height:100%;width:35%;background:var(--accent);border-radius:999px;
   animation:slide 1.6s ease-in-out infinite}}
 @keyframes slide{{0%{{margin-left:-35%}}100%{{margin-left:100%}}}}
 .hint{{color:var(--muted);font-size:.9rem;margin:0}}
 .err{{color:#a8261a;font-size:.92rem;margin:14px 0 0}}
 a{{color:var(--accent)}}
</style></head>
<body><div class="card">
 <h1>Собираем РПД</h1>
 <p class="name">{name}</p>
 <div class="bar"><i></i></div>
 <p class="hint" id="rpd-wait-note" aria-live="polite">Идёт <span id="rpd-wait-time">0:00</span>.
   Обычно две-три минуты. Страницу можно не перезагружать — она откроется сама.</p>
 <p class="err" id="rpd-wait-error"{hidden}>{error}</p>
 <p class="hint" style="margin-top:16px"><a href="/app/rpd">← К списку РПД</a></p>
</div>
<script>
(function(){{
  var started=Date.now(),t=document.getElementById('rpd-wait-time'),
      note=document.getElementById('rpd-wait-note'),err=document.getElementById('rpd-wait-error');
  function tick(){{var s=Math.floor((Date.now()-started)/1000);
    t.textContent=Math.floor(s/60)+':'+String(s%60).padStart(2,'0');}}
  setInterval(tick,1000);
  function poll(){{
    fetch('/app/rpd/{run_id}/status',{{headers:{{'Accept':'application/json'}}}})
      .then(function(r){{return r.json();}})
      .then(function(d){{
        if(d&&d.state==='done'&&d.redirect){{window.location.href=d.redirect;return;}}
        if(d&&d.state==='error'){{note.textContent='Сборка не удалась.';
          err.hidden=false;err.textContent=d.error||'Причина неизвестна';return;}}
        setTimeout(poll,2000);
      }}).catch(function(){{setTimeout(poll,4000);}});
  }}
  setTimeout(poll,2000);
}})();
</script>
</body></html>"""


_SCREEN_TEMPLATE = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>РПД из программы</title>
<style>
 :root{{--accent:#0e8f6f;--ink:#15221d;--muted:#5c6b64;--line:#dde5e1;--soft:#e4f5ee;--gap:#a86616;--gapsoft:#f9efd9}}
 body{{font-family:system-ui,'Segoe UI',sans-serif;margin:0;background:#f4f7f5;color:var(--ink);padding:32px}}
 .wrap{{max-width:820px;margin:0 auto}}
 a.back{{color:var(--accent);text-decoration:none;font-size:.9rem}}
 h1{{font-size:1.5rem;margin:.4rem 0 .2rem}} .sub{{color:var(--muted);margin:0 0 22px}}
 .card{{background:#fff;border:1px solid var(--accent);border-radius:14px;padding:20px;margin-bottom:18px}}
 .drop{{border:1.5px dashed var(--accent);border-radius:10px;padding:16px;display:flex;align-items:center;gap:12px;cursor:pointer}}
 .drop input{{display:none}} .drop .name{{font-weight:600;font-size:.9rem}} .drop .ok{{margin-left:auto;color:var(--accent);font-weight:700;font-size:.85rem}}
 .btn{{display:inline-flex;align-items:center;gap:8px;background:var(--accent);color:#fff;border:none;border-radius:9px;
   padding:11px 18px;font:inherit;font-weight:650;font-size:14px;cursor:pointer;text-decoration:none;margin-top:14px;
   box-shadow:0 3px 12px -2px rgba(16,185,129,.5);opacity:.5;transition:opacity .15s}}
 .btn.is-ready{{opacity:1}} .btn:disabled{{opacity:.5;cursor:default}}
 .progress{{margin-top:12px}} .progress[hidden]{{display:none}}
 .plabel{{font-size:13px;font-weight:650;color:var(--accent);margin-bottom:6px}}
 .ptrack{{height:9px;border-radius:999px;background:var(--soft);overflow:hidden}} .ptrack>b{{display:block;height:100%;width:0;background:var(--accent);transition:width .2s}}
 .result{{margin-top:6px}}
 .barwrap{{display:flex;align-items:center;gap:12px;margin-bottom:14px}} .bar{{flex:1;height:9px;border-radius:999px;background:#eef3f0;overflow:hidden}} .bar>i{{display:block;height:100%;background:var(--accent);border-radius:999px}}
 .barval{{font-weight:700;color:var(--accent)}} .muted{{color:var(--muted);font-size:.85rem}}
 .slots{{list-style:none;padding:0;margin:0;border:1px solid var(--line);border-radius:11px;overflow:hidden}}
 .slots li{{display:flex;align-items:center;gap:12px;padding:10px 14px;border-top:1px solid var(--line)}} .slots li:first-child{{border-top:none}}
 .slots .code{{font-family:ui-monospace,Consolas,monospace;font-size:11.5px;color:#8798901;width:34px;color:var(--muted)}} .slots .nm{{font-size:14px}}
 .st{{margin-left:auto;font-size:12px;font-weight:650;padding:4px 10px;border-radius:999px}} .st.ok{{background:var(--soft);color:var(--accent)}} .st.gap{{background:var(--gapsoft);color:var(--gap)}}
 .gapnote{{color:var(--gap);font-size:13px;margin:12px 0 0}}
 .stats{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:6px}}
 .stat{{flex:1;min-width:90px;background:var(--soft);border-radius:10px;padding:10px 12px;text-align:center}}
 .statv{{font-weight:800;font-size:1.2rem;color:var(--accent)}} .statl{{font-size:.8rem;color:var(--muted)}}
 .titlein{{width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid var(--line);border-radius:9px;font:inherit;margin-bottom:12px}}
 .registry-label{{display:block;font-weight:700;margin-bottom:8px}}
 .registry-row{{display:flex;align-items:stretch;gap:10px}}
 .registry-select{{flex:1;min-width:0;padding:11px 38px 11px 12px;border:1px solid var(--line);border-radius:9px;
   background:#fff;color:var(--ink);font:inherit;font-size:14px;cursor:pointer}}
 .registry-select:disabled{{color:var(--muted);cursor:wait}}
 .registry-row .btn{{margin-top:0;white-space:nowrap}}
 .registry-state{{min-height:18px;margin-top:8px;color:var(--muted);font-size:.85rem}}
 @media (max-width:620px){{.registry-row{{flex-direction:column}} .registry-row .btn{{justify-content:center}}}}
 .cattitle{{font-weight:700;margin:16px 0 8px}}
 .cats{{list-style:none;padding:0;margin:0;border:1px solid var(--line);border-radius:11px;overflow:hidden}}
 .cats li{{display:flex;align-items:center;gap:12px;padding:11px 14px;border-top:1px solid var(--line)}} .cats li:first-child{{border-top:none}}
 .cats .nm{{font-size:14px}} .cs{{margin-left:auto;font-size:12px;font-weight:650;padding:4px 10px;border-radius:999px;white-space:nowrap}}
 .cs.up{{background:var(--soft);color:var(--accent)}} .cs.gen{{background:#eef2ff;color:#4f46e5}}
 .cs.form{{background:#eef1f4;color:#5f6f7d}} .cs.gap{{background:var(--gapsoft);color:var(--gap)}} .cs.wait{{background:var(--gapsoft);color:var(--gap)}}
 .actions{{margin-top:16px}}
 .recent{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px}}
 .recent .rt{{font-weight:700;margin-bottom:8px}} .recent ul{{list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:6px}}
 .recent a{{color:var(--accent);text-decoration:none;font-weight:600}} .del{{border:none;background:none;color:#b02a2a;cursor:pointer;font-size:1.15rem;line-height:1}}
</style></head><body><div class="wrap">
 <a class="back" href="/app">← к режимам</a>
 <h1>РПД из учебного плана</h1>
 <p class="sub">Сборка рабочей программы дисциплины (DOCX) из УП: учебный план, модули и компетенции —
   из УП; актуальность/цели — генерируются; форма ДПО — по шаблону.</p>
 <div class="card">
   <form id="rpd-form">
     <input class="titlein" id="rpd-title" name="title" type="text" placeholder="Название программы (напр. «ИИ для предпринимателя»)">
     <label class="drop" for="rpd-file">
       <span>📄</span>
       <span class="name" id="rpd-name">Загрузить УП (CSV-экспорт из Реестра)</span>
       <span class="ok" id="rpd-ok" hidden>на сервере ✓</span>
       <input id="rpd-file" name="program" type="file" accept=".csv">
     </label>
     <input type="hidden" name="upload_id" id="rpd-upload-id">
     <div class="progress" id="rpd-progress" hidden aria-live="polite">
       <div class="plabel" id="rpd-plabel">Загрузка…</div>
       <div class="ptrack"><b id="rpd-fill"></b></div>
     </div>
     <button class="btn" id="rpd-go" type="submit">Сформировать РПД →</button>
   </form>
 </div>
 <div class="card">
   <label class="registry-label" for="rpd-registry-select">…или выбрать УП из Реестра</label>
   <div class="registry-row">
     <select class="registry-select" id="rpd-registry-select" disabled>
       <option value="">Загружаю список…</option>
     </select>
     <button class="btn" id="rpd-registry-go" type="button" disabled>Сформировать РПД →</button>
   </div>
   <div class="registry-state" id="rpd-registry-state" aria-live="polite"></div>
 </div>
 {result}
 {recent}
</div>
<script>
(function(){{
  var form=document.getElementById('rpd-form'),file=document.getElementById('rpd-file'),
      nameEl=document.getElementById('rpd-name'),okEl=document.getElementById('rpd-ok'),
      hid=document.getElementById('rpd-upload-id'),prog=document.getElementById('rpd-progress'),
      lbl=document.getElementById('rpd-plabel'),fill=document.getElementById('rpd-fill'),
      go=document.getElementById('rpd-go'),defName=nameEl.textContent;
  function ready(r){{go.classList.toggle('is-ready',!!r);}}
  file.addEventListener('change',function(){{
    hid.value='';ready(false);okEl.hidden=true;
    if(!file.files||!file.files.length){{nameEl.textContent=defName;prog.hidden=true;return;}}
    var f=file.files[0];nameEl.textContent=f.name;prog.hidden=false;fill.style.width='0%';lbl.textContent='Загрузка: 0%';
    var body=new FormData();body.append('program',f);
    var xhr=new XMLHttpRequest();xhr.open('POST','/app/rpd/upload',true);
    xhr.upload.onprogress=function(e){{if(!e.lengthComputable)return;var p=Math.round(e.loaded/e.total*100);
      fill.style.width=p+'%';lbl.textContent=p<100?('Загрузка: '+p+'%'):'Обработка…';}};
    xhr.onload=function(){{var id='';if(xhr.status>=200&&xhr.status<300){{try{{id=(JSON.parse(xhr.responseText)||{{}}).upload_id||'';}}catch(e){{}}}}
      if(id){{hid.value=id;fill.style.width='100%';lbl.textContent='Файл на сервере ✓';okEl.hidden=false;ready(true);}}
      else{{lbl.textContent='Ошибка загрузки ('+xhr.status+')';}}}};
    xhr.onerror=function(){{lbl.textContent='Ошибка сети';}};xhr.send(body);
  }});
  form.addEventListener('submit',function(e){{
    e.preventDefault();if(!hid.value){{lbl.textContent='Сначала загрузите УП (.csv)';prog.hidden=false;return;}}
    go.disabled=true;go.textContent='Собираю РПД (генерю прозу)…';prog.hidden=false;
    var titleEl=document.getElementById('rpd-title');
    var body=new URLSearchParams();body.append('upload_id',hid.value);body.append('title',titleEl?titleEl.value:'');
    fetch('/app/rpd/generate',{{method:'POST',body:body,headers:{{'Content-Type':'application/x-www-form-urlencoded'}}}})
      .then(function(r){{return r.json();}}).then(function(d){{
        if(!d||!d.run_id){{throw 0;}}
        watch(d.run_id);
      }})
      .catch(function(){{go.disabled=false;go.textContent='Сформировать РПД →';lbl.textContent='Не удалось собрать РПД';}});
  }});
  // Сборка идёт на сервере; запрос уже отвечен, поэтому ждём по статусу, а не держим соединение.
  function watch(runId){{
    var started=Date.now();
    function elapsed(){{var s=Math.floor((Date.now()-started)/1000);
      return Math.floor(s/60)+':'+String(s%60).padStart(2,'0');}}
    function step(){{
      fetch('/app/rpd/'+encodeURIComponent(runId)+'/status')
        .then(function(r){{return r.json();}})
        .then(function(d){{
          if(d&&d.state==='done'&&d.redirect){{window.location.href=d.redirect;return;}}
          if(d&&d.state==='error'){{go.disabled=false;go.textContent='Сформировать РПД →';
            lbl.textContent='Сборка не удалась: '+(d.error||'причина неизвестна');return;}}
          lbl.textContent='Собираю РПД — '+elapsed()+'. Обычно две-три минуты, не перезагружайте страницу.';
          setTimeout(step,2000);
        }}).catch(function(){{setTimeout(step,4000);}});
    }}
    lbl.textContent='Собираю РПД — 0:00. Обычно две-три минуты, не перезагружайте страницу.';
    setTimeout(step,2000);
  }}
}})();
(function(){{
  var select=document.getElementById('rpd-registry-select'),go=document.getElementById('rpd-registry-go'),
      state=document.getElementById('rpd-registry-state');
  if(!select||!go||!state) return;
  function ready(value){{go.disabled=!value;go.classList.toggle('is-ready',!!value);}}
  fetch('/app/rpd/registry').then(function(r){{return r.json();}}).then(function(d){{
    var plans=(d&&d.plans)||[];
    select.innerHTML='';
    var placeholder=document.createElement('option');placeholder.value='';
    placeholder.textContent=plans.length?'Выберите учебный план':'В Реестре нет доступных УП';
    select.appendChild(placeholder);
    if(!plans.length){{state.textContent='Можно загрузить УП в формате CSV.';return;}}
    plans.forEach(function(p){{
      var option=document.createElement('option');option.value=String(p.id);
      option.textContent=p.title+(p.hours?(' · '+p.hours+' ч'):'');
      select.appendChild(option);
    }});
    select.disabled=false;state.textContent='Доступно УП: '+plans.length;
  }}).catch(function(){{select.innerHTML='<option value="">Не удалось загрузить список</option>';
    state.textContent='Проверьте доступность Реестра.';}});
  select.addEventListener('change',function(){{ready(select.value);state.textContent=select.value?'УП выбран. Можно формировать РПД.':'';}});
  go.addEventListener('click',function(){{
    var planId=select.value;if(!planId)return;
    select.disabled=true;go.disabled=true;go.textContent='Собираю РПД…';state.textContent='Формируется документ из выбранного УП.';
    fetch('/app/rpd/registry/'+encodeURIComponent(planId),{{method:'POST'}}).then(function(r){{return r.json();}})
      .then(function(x){{
        if(!x||!x.run_id){{throw 0;}}
        var started=Date.now();
        function step(){{
          fetch('/app/rpd/'+encodeURIComponent(x.run_id)+'/status').then(function(r){{return r.json();}})
            .then(function(d){{
              if(d&&d.state==='done'&&d.redirect){{window.location.href=d.redirect;return;}}
              if(d&&d.state==='error'){{select.disabled=false;go.textContent='Повторить →';ready(select.value);
                state.textContent='Сборка не удалась: '+(d.error||'причина неизвестна');return;}}
              var s=Math.floor((Date.now()-started)/1000);
              state.textContent='Собираю РПД — '+Math.floor(s/60)+':'+String(s%60).padStart(2,'0')
                +'. Обычно две-три минуты, не перезагружайте страницу.';
              setTimeout(step,2000);
            }}).catch(function(){{setTimeout(step,4000);}});
        }}
        state.textContent='Собираю РПД — 0:00. Обычно две-три минуты, не перезагружайте страницу.';
        setTimeout(step,2000);
      }})
      .catch(function(){{select.disabled=false;go.textContent='Повторить →';ready(select.value);
        state.textContent='Не удалось собрать РПД. Попробуйте ещё раз.';}});
  }});
}})();
</script>
</body></html>"""


_EDITOR_TEMPLATE = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>РПД — редактор</title>
<style>
 :root{{--accent:#0e8f6f;--ink:#15221d;--muted:#5c6b64;--line:#dde5e1;--soft:#e4f5ee;--gap:#a86616;--gapsoft:#f9efd9}}
 *{{box-sizing:border-box}}
 body{{font-family:system-ui,'Segoe UI',sans-serif;margin:0;background:#f4f7f5;color:var(--ink);padding:20px 28px}}
 .wrap{{max-width:1600px;margin:0 auto}}
 a.back{{color:var(--accent);text-decoration:none;font-size:.9rem}}
 h1{{font-size:1.35rem;margin:.4rem 0 .1rem}} .sub{{color:var(--muted);margin:0 0 18px;font-size:.9rem}}
 .grid{{display:grid;grid-template-columns:300px minmax(0,1fr);gap:18px;align-items:start}}
 .grid>div{{min-width:0}}
 @media (max-width:820px){{.grid{{grid-template-columns:1fr}}}}
 .nav{{background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden;max-height:74vh;overflow-y:auto}}
 @media (max-width:820px){{.nav{{max-height:35vh}}}}
 .navitem{{display:flex;flex-wrap:wrap;align-items:center;gap:6px 8px;padding:10px 12px;border-top:1px solid var(--line);text-decoration:none;color:var(--ink)}}
 .navitem:first-child{{border-top:none}} .navitem.on{{background:var(--soft)}}
 .navitem .nnum{{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:var(--muted);min-width:30px}}
 .navitem .ntitle{{font-size:13px;flex:1 1 55%;min-width:0;word-break:break-word}}
 .navitem .cs{{margin-left:auto}}
 .card{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:20px}}
 .sechead{{display:flex;align-items:center;gap:10px;margin-bottom:6px}} .sechead h2{{font-size:1.05rem;margin:0}}
 .sechead .snum{{font-family:ui-monospace,Consolas,monospace;color:var(--muted);font-size:.85rem}}
 .hint{{color:var(--muted);font-size:12.5px;margin:0 0 14px}}
 .prosein{{width:100%;padding:12px;border:1px solid var(--line);border-radius:9px;font:inherit;font-size:14px;line-height:1.5;resize:vertical}}
 .readonly-section{{padding:14px 18px;border:1px solid var(--line);border-radius:9px;background:#f8faf9;font-size:14px;line-height:1.5}}
 .readonly-section ul{{margin:0;padding-left:22px}} .readonly-section li+li{{margin-top:7px}}
 .readonly-table{{width:100%;border-collapse:collapse;background:#fff}} .readonly-table th,.readonly-table td{{border:1px solid var(--line);padding:8px;text-align:left;vertical-align:top}}
 .tblwrap{{overflow-x:auto;border:1px solid var(--line);border-radius:9px}}
 table.edit{{border-collapse:collapse;width:100%;min-width:640px}}
 table.edit th{{background:var(--soft);color:var(--accent);font-size:11.5px;text-align:left;padding:8px 10px;position:sticky;top:0}}
 table.edit td{{border-top:1px solid var(--line);padding:3px;vertical-align:top}}
 table.edit textarea{{width:100%;min-width:120px;border:none;padding:7px 8px;font:inherit;font-size:13px;background:transparent;
   resize:vertical;overflow:hidden;white-space:pre-wrap;word-break:break-word;line-height:1.35;box-sizing:border-box}}
 table.edit textarea:focus{{outline:2px solid var(--soft);border-radius:6px}}
 .cs{{font-size:11px;font-weight:650;padding:3px 9px;border-radius:999px;white-space:nowrap}}
 .cs.up{{background:var(--soft);color:var(--accent)}} .cs.gen{{background:#eef2ff;color:#4f46e5}}
 .cs.form{{background:#eef1f4;color:#5f6f7d}} .cs.gap{{background:var(--gapsoft);color:var(--gap)}}
 .btn{{display:inline-flex;align-items:center;gap:8px;background:var(--accent);color:#fff;border:none;border-radius:9px;
   padding:10px 18px;font:inherit;font-weight:650;font-size:14px;cursor:pointer;text-decoration:none}}
 .btn.ghost{{background:#fff;color:var(--accent);border:1px solid var(--accent)}}
 .btn.disabled{{background:#9aa8a1;box-shadow:none;cursor:not-allowed}}
 .rowbar{{display:flex;align-items:center;gap:10px;margin-top:16px;flex-wrap:wrap}}
 .navbtn{{color:var(--accent);text-decoration:none;font-weight:600;font-size:14px}} .navbtn.off{{color:#b9c2bd}}
 .spacer{{flex:1}}
 .exportbar{{margin-top:14px;display:flex;justify-content:flex-end;gap:10px;flex-wrap:wrap}}
 .navitem .ndone{{color:var(--accent);font-weight:800;min-width:12px;font-size:12px}}
 .addrow{{margin-top:8px;background:#fff;border:1px dashed var(--accent);color:var(--accent);border-radius:8px;padding:6px 12px;font:inherit;font-size:13px;cursor:pointer}}
 table.edit td.delc{{width:34px;text-align:center;padding:0}} .delrow{{border:none;background:none;color:#b02a2a;cursor:pointer;font-size:1.1rem;line-height:1}}
 .donebox{{display:inline-flex;align-items:center;gap:6px;font-size:13px;color:var(--muted);cursor:pointer}}
 .nflag{{color:var(--gap);font-weight:800;margin-left:4px}}
 .flagnote{{background:var(--gapsoft);color:var(--gap);border:1px solid var(--gap);border-radius:8px;padding:8px 12px;font-size:13px;margin:0 0 12px}}
 .quality{{border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:12px 0;background:#fff}}
 .quality.ready{{border-color:var(--accent);background:var(--soft)}} .quality.blocked{{border-color:var(--gap);background:var(--gapsoft)}}
 .quality.neutral{{border-color:var(--line);background:#fff}}
 .quality-title{{font-weight:750}} .quality ul{{margin:8px 0 0;padding-left:20px;font-size:12.5px}}
 .quality-metrics{{margin-top:6px;color:var(--muted);font-size:12px}} .quality .btn{{margin-top:10px}}
 .meta{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-bottom:16px}}
 .meta summary{{font-weight:700;cursor:pointer}} .meta-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin:12px 0}}
 .meta-field{{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--muted)}}
 .meta-field input{{padding:8px 9px;border:1px solid var(--line);border-radius:7px;font:inherit;color:var(--ink)}}
 .evidence-panel{{border:1px solid var(--gap);background:var(--gapsoft);border-radius:10px;padding:12px;margin:0 0 14px}}
 .evidence-summary,.evidence-empty{{font-size:12.5px;color:var(--muted);margin:6px 0 10px}}
 .evidence-module{{background:#fff;border:1px solid var(--line);border-radius:8px;margin-top:8px;padding:8px}}
 .evidence-module summary{{cursor:pointer;font-weight:700;font-size:13px}}
 .evidence-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:8px;margin-top:8px}}
 .evidence-card{{border:1px solid var(--line);border-radius:8px;padding:9px;background:#fff}}
 .evidence-card-head{{display:flex;justify-content:space-between;gap:8px;font-size:12px}}
 .evidence-status{{padding:2px 7px;border-radius:999px;font-size:11px;font-weight:700}}
 .evidence-status.ok{{background:var(--soft);color:var(--accent)}} .evidence-status.pending{{background:var(--gapsoft);color:var(--gap)}}
 .evidence-subject{{font-size:12px;margin:5px 0;line-height:1.35}}
 .evidence-card img{{display:block;max-width:100%;max-height:190px;object-fit:contain;border:1px solid var(--line);border-radius:6px;margin:7px auto}}
 .evidence-card form{{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-top:7px}}
 .evidence-card input[type=file]{{font-size:11px;max-width:180px}}
 .evidence-upload{{border:1px solid var(--accent);background:#fff;color:var(--accent);border-radius:6px;padding:5px 8px;cursor:pointer;font-weight:650;font-size:11px}}
 .module-programs{{display:grid;gap:14px}}
 .module-program{{border:1px solid var(--line);border-radius:10px;padding:10px;background:#f8faf9}}
 .module-program>summary{{cursor:pointer;font-weight:750;padding:2px 4px}}
 .module-program h3{{font-size:13px;margin:14px 2px 7px;color:var(--muted)}}
 .module-program .tblwrap{{background:#fff}}
 .module-evidence-area{{border:1px solid var(--gap);background:#fff8e8;border-radius:8px;margin-top:14px;padding:12px}}
 .module-evidence-title{{font-weight:750;font-size:13px;margin-bottom:8px}}
 .module-evidence-item{{border-top:1px solid #ead8b7;padding:8px 2px;font-size:12px;line-height:1.4}}
 .module-evidence-item.empty{{color:var(--muted)}}
</style></head><body><div class="wrap">
 <a class="back" href="/app/rpd">← к списку РПД</a>
 <h1>РПД: {name}</h1>
 <p class="sub">Раздел {pos} из {total} · готово {done_count} · правьте по разделам, затем экспорт</p>
 {quality_summary}
 {metadata_form}
 <div class="grid">
   <nav class="nav">{nav}</nav>
   <div>
     <div class="card">
       <div class="sechead"><span class="snum">{number}</span><h2>{title}</h2>{chip}</div>
       <p class="hint">{hint}</p>
       {flag_note}
       <form method="post" action="/app/rpd/{run_id}/section/{index}">
         <input type="hidden" name="revision" value="{revision}">
         {editor}
         <div class="rowbar">
           <button class="btn" type="submit">{save_label}</button>
           <label class="donebox"><input type="checkbox" name="done" {done_checked}> Готово</label>
           <span class="spacer"></span>
           {prev_btn}{next_btn}
         </div>
       </form>
       {evidence_panel}
     </div>
     <div class="exportbar">{export_actions}</div>
   </div>
 </div>
</div>
<script>
(function(){{
  var tables=Array.prototype.slice.call(document.querySelectorAll('table.edit[data-rpd-table]'));
  if(!tables.length) return;
  var form=tables[0].closest('form');
  function fit(el){{ el.style.height='auto'; el.style.height=(el.scrollHeight+2)+'px'; }}
  function autosize(scope){{ (scope||document).querySelectorAll('textarea').forEach(fit); }}
  function bindDelete(button){{ button.onclick=function(){{ button.closest('tr').remove(); }}; }}
  function serialize(table){{
    var rows=[];
    table.querySelectorAll('tbody tr').forEach(function(tr){{
      var row=[];
      tr.querySelectorAll('textarea').forEach(function(input){{ row.push(input.value); }});
      rows.push(row);
    }});
    return rows;
  }}
  tables.forEach(function(table){{
    table.querySelectorAll('.delrow').forEach(bindDelete);
    table.addEventListener('input',function(event){{
      if(event.target.tagName==='TEXTAREA') fit(event.target);
    }});
  }});
  document.querySelectorAll('.addrow[data-table-target]').forEach(function(button){{
    button.onclick=function(){{
      var table=document.getElementById(button.dataset.tableTarget);
      if(!table) return;
      var ncols=parseInt(table.dataset.ncols||'1',10);
      var tr=document.createElement('tr'), html='';
      for(var column=0;column<ncols;column++) html+='<td><textarea rows="1"></textarea></td>';
      html+='<td class="delc"><button type="button" class="delrow" title="Удалить строку">&times;</button></td>';
      tr.innerHTML=html;
      table.querySelector('tbody').appendChild(tr);
      tr.querySelectorAll('.delrow').forEach(bindDelete);
      autosize(tr);
    }};
  }});
  autosize();
  if(form) form.addEventListener('submit',function(){{
    var single=document.querySelector('table.edit[data-rpd-table="single"]');
    var singleHidden=document.getElementById('table-json');
    if(single && singleHidden) singleHidden.value=JSON.stringify(serialize(single));
    var moduleHidden=document.getElementById('module-blocks-json');
    if(moduleHidden){{
      var payload={{}};
      document.querySelectorAll('table.edit[data-rpd-table="module"]').forEach(function(table){{
        var moduleIndex=table.dataset.moduleIndex, role=table.dataset.role;
        if(!payload[moduleIndex]) payload[moduleIndex]={{}};
        payload[moduleIndex][role]=serialize(table);
      }});
      moduleHidden.value=JSON.stringify(payload);
    }}
  }});
}})();
</script>
</body></html>"""
