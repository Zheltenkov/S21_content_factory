"""Concept runs: durable JSON snapshots of one version of the brief and its interpretation, design and review.

Everything the concept stage exposes over HTTP lives here as a router; app.py only mounts it.
"""

import asyncio
import hashlib
import logging
import os
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from ..core import isolated, usage
from ..core.access import current_owner
from ..core.llm import ModelClient, model_configuration, reviewer_model
from ..core.storage import parse_stored, read_owned, write_model
from . import skills
from .ingestion import MAX_FILE_BYTES, InputError, extract_material, parse_curriculum
from .models import Event, FeedbackRequest, FindingDecision, Material, Run, StartRequest, WorkloadBudget
from .planning import fingerprint, prepare, readable_export
from .validation import apply_demo_policy, input_review_findings, revision_changes
from .workload import WorkloadQuery, overflow, overflow_text
from .workload import budget as workload_budget

MODULE = Path(__file__).resolve().parent
PACKAGE = MODULE.parent
RUNS_DIR = Path(os.getenv("PROJECT_LAB_RUNS_DIR", PACKAGE / "data" / "runs"))
logger = logging.getLogger("project_generator_lab")
active: dict[str, asyncio.Task[None]] = {}
owners: dict[str, str] = {}  # run id → who started it, for the per-person limit
# Two preparations at a time per person; a ceiling for the whole server (one CPU). The old limit of two for everyone
# let one person hold the queue for all (audit 30.09.2026).
RUNS_PER_OWNER = 2
RUNS_ALL = 6
router = APIRouter()


def room() -> None:
    """Refuse a new preparation when this person already has two running, or the server has its ceiling."""
    mine = sum(1 for run_id in active if owners.get(run_id) == current_owner.get())
    if mine >= RUNS_PER_OWNER:
        raise HTTPException(429, "У вас уже готовятся два замысла. Дождитесь завершения одного из них.")
    if len(active) >= RUNS_ALL:
        raise HTTPException(429, "Сервер сейчас готовит много замыслов. Попробуйте через несколько минут.")


def reviewer_client() -> ModelClient | None:
    # A separately configured reviewer (ideally another model family) avoids self-review by one model.
    model = reviewer_model()
    return ModelClient(model=model) if model else None


def launch(run: Run) -> None:
    # The calls of the job are written into the account of its run (core/usage.py) — whichever request started it:
    # the one that creates a run has no id in its address yet.
    task = asyncio.create_task(prepare(run, save_run, ModelClient(), reviewer_client()), context=usage.under(run.id))
    active[run.id], owners[run.id] = task, run.owner_id

    def done(_: object) -> None:
        active.pop(run.id, None)
        owners.pop(run.id, None)

    task.add_done_callback(done)


def save_run(run: Run) -> None:
    write_model(RUNS_DIR / f"{run.id}.json", run)
    logger.info("run=%s status=%s stage=%s", run.id, run.status, run.events[-1].stage if run.events else "queued")


def load_run(run_id: UUID) -> Run:
    # UUID knowledge does not grant access to another methodologist's uploads.
    run = read_owned(RUNS_DIR / f"{run_id}.json", Run, current_owner.get(), "Запуск не найден",
                     "Файл этой подготовки не читается текущей версией. Начните подготовку заново; файл сохранён.")
    # Apply current preview readiness to pending cards without rewriting saved results.
    if run.status == "input_review":
        run.input_findings = input_review_findings(run)
    apply_demo_policy(run)
    return run


def recover_interrupted() -> None:
    """An interrupted process must not leave a permanently spinning UI after restart."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    for path in RUNS_DIR.glob("*.json"):
        try:
            run = parse_stored(Run, path.read_text(encoding="utf-8"))
            if run.status == "running":
                run.status = "interrupted"
                run.error = "Сервер перезапущен. Параметры сохранены; запустите подготовку повторно."
                save_run(run)
        except (ValueError, OSError):
            logger.warning("Unreadable run snapshot: %s", path.name)


async def stop_all() -> None:
    for task in active.values():
        task.cancel()
    if active:
        await asyncio.gather(*active.values(), return_exceptions=True)


@router.post("/api/workload")
async def workload(query: WorkloadQuery) -> WorkloadBudget:
    return workload_budget(query)


async def uploaded_bytes(upload: UploadFile) -> bytes:
    data = await upload.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, "Файл больше 4 МБ")
    return data


@router.post("/api/curriculum")
async def curriculum(file: Annotated[UploadFile, File()], skills_tab: Annotated[str, Form(max_length=200)] = ""):
    """skills_tab — the skills tab of the workbook the methodologist chose, when it has several for this plan."""
    name = file.filename or "plan.csv"
    try:
        data = await uploaded_bytes(file)
        if Path(name).suffix.lower() == ".xlsx":
            # A workbook is parsed like any uploaded Office file: in a child process under memory and CPU limits.
            return await asyncio.get_running_loop().run_in_executor(isolated.POOL, isolated.run, parse_curriculum, name, data, skills_tab)
        return await asyncio.to_thread(parse_curriculum, name, data)
    except (InputError, isolated.ParseRefused) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/api/example")
async def example():
    path = MODULE / "examples" / "curriculum.csv"
    return parse_curriculum("Пример УП.csv", path.read_bytes())


@router.post("/api/materials")
async def material(file: Annotated[UploadFile, File()]):
    data = await uploaded_bytes(file)
    try:
        # Parsed in a child process under memory and CPU limits: a crafted PDF or XLSX hits the limit there, not the service.
        return await asyncio.get_running_loop().run_in_executor(isolated.POOL, isolated.run, extract_material,
                                                                 file.filename or "material.txt", data)
    except HTTPException:
        raise
    except Exception as exc:
        detail = (str(exc) if isinstance(exc, (InputError, isolated.ParseRefused))
                  else "Не удалось прочитать файл. Проверьте формат и целостность документа.")
        # Retain failed uploads in the input set so the next stage cannot overlook them.
        digest = hashlib.sha256(data).hexdigest()
        return Material(id=digest[:16], name=Path(file.filename or "material.txt").name,
                        sha256=digest, passages=[], warnings=[detail], read_status="failed")


@router.post("/api/runs", status_code=202)
async def start(request: StartRequest, auto_continue: bool = False):
    run_id = str(request.request_id)
    path = RUNS_DIR / f"{run_id}.json"
    if path.exists():
        run = load_run(request.request_id)
        if fingerprint(run.request) != fingerprint(request):
            raise HTTPException(409, "Идентификатор запуска уже использован для других параметров")
        return {"id": run.id, "status": run.status}
    room()
    if not model_configuration()[0]:
        raise HTTPException(503, "На сервере не настроено подключение к модели")
    previous = load_run(request.parent_run_id) if request.parent_run_id else None
    run = Run(id=run_id, owner_id=current_owner.get(), fingerprint=fingerprint(request), request=request,
              changes=revision_changes(previous, request) if previous else [],
              baseline_concept=previous.concept if previous else None,
              baseline_approved_at=previous.approved_at if previous else None, auto_continue=auto_continue)
    run.events.append(Event(stage="context", status="running", message="Запуск принят. Параметры сохранены."))
    save_run(run)
    launch(run)
    return {"id": run_id, "status": "running"}


@router.get("/api/runs/{run_id}")
async def status(run_id: UUID):
    return load_run(run_id)


@router.post("/api/runs/{run_id}/approve")
async def approve(run_id: UUID):
    run = load_run(run_id)
    if run.status == "approved":
        return run
    if run.status != "review" or not run.concept or not run.review:
        raise HTTPException(409, "Дождитесь подготовки замысла и проверки")
    if run.prompt_version != "0.1" and not run.input_approved_at:
        raise HTTPException(409, "Сначала согласуйте входные данные")
    run.status = "approved"
    run.approved_at = datetime.now(timezone.utc).isoformat()
    run.events.append(Event(stage="approval", status="done", message="Демонстрационный замысел согласован; предупреждения сохранены для обсуждения."))
    save_run(run)
    return run


@router.post("/api/runs/{run_id}/confirm-inputs", status_code=202)
async def confirm_inputs(run_id: UUID, keep_hours: bool = False):
    """`keep_hours` — the methodologist keeps hours more than one project holds (workload.overflow); without it such a
    run is not designed: the chat asks first (concept/route.py), and so does this route for any other caller."""
    run = load_run(run_id)
    # Repeated confirmation does not repeat paid model calls or change the timestamp.
    if run.input_approved_at:
        return run
    if run.status != "input_review":
        raise HTTPException(409, "Дождитесь завершения разбора входов")
    if not run.hours_kept and (capacity := overflow(run.workload_budget)) is not None:
        if not keep_hours:
            assert run.workload_budget is not None
            raise HTTPException(409, overflow_text(run.workload_budget, capacity))
        run.hours_kept = True
    room()
    if not model_configuration()[0]:
        raise HTTPException(503, "На сервере не настроено подключение к модели")
    # An old source-blocked run may not have a card yet: resume interpretation first.
    if run.input_card:
        run.input_approved_at = datetime.now(timezone.utc).isoformat()
    run.status = "running"
    message = "Продолжаем разбор доступных входов в демонстрационном режиме." if not run.input_card else "Методолог разрешил подготовку демонстрационного замысла. Предупреждения сохранены."
    run.events.append(Event(stage="inputs_approval", status="done", message=message))
    save_run(run)
    launch(run)
    return run


@router.post("/api/runs/{run_id}/cancel")
async def cancel(run_id: UUID):
    """Stop at a safe point: finished stages stay saved, the unfinished one is marked, nothing is reset."""
    run = load_run(run_id)
    if run.status != "running":
        raise HTTPException(409, "Подготовка сейчас не выполняется")
    task = active.get(run.id)
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError, asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=5)
    run = load_run(run_id)
    if run.status == "running":
        stage = next((e.stage for e in reversed(run.events) if e.status == "running"), "context")
        run.status = "interrupted"
        run.error = "Остановлено методологом. Готовые этапы сохранены; незавершённый можно продолжить."
        run.events.append(Event(stage=stage, status="failed", message="Остановлено методологом."))
        save_run(run)
    return run


@router.post("/api/runs/{run_id}/resume", status_code=202)
async def resume(run_id: UUID):
    """Continue from the first unfinished stage instead of repeating finished paid calls."""
    run = load_run(run_id)
    if run.status not in {"interrupted", "failed"}:
        raise HTTPException(409, "Продолжить можно только остановленную или незавершённую подготовку")
    if run.input_card and not run.input_approved_at:
        run.status = "input_review"
        run.error = ""
        run.events.append(Event(stage="context", status="done", message="Карточка входов сохранена. Проверьте её и подтвердите входы."))
        save_run(run)
        return run
    room()
    if not model_configuration()[0]:
        raise HTTPException(503, "На сервере не настроено подключение к модели")
    run.status = "running"
    run.error = ""
    stage = "review" if run.concept and not run.review else "design" if run.input_approved_at else "context"
    run.events.append(Event(stage=stage, status="running", message="Продолжаем с незавершённого этапа."))
    save_run(run)
    launch(run)
    return run


@router.post("/api/runs/{run_id}/feedback")
async def finding_feedback(run_id: UUID, request: FeedbackRequest):
    run = load_run(run_id)
    if run.status != "review" or not run.review:
        raise HTTPException(409, "Решения по замечаниям доступны до согласования замысла")
    if request.finding_index >= len(run.review.findings):
        raise HTTPException(422, "Замечание не найдено")
    decision = FindingDecision(**request.model_dump())
    last = next((d for d in reversed(run.finding_decisions) if d.finding_index == request.finding_index), None)
    if last and last.verdict == request.verdict and last.reason == request.reason:
        return run
    run.finding_decisions.append(decision)
    save_run(run)
    return run


@router.get("/api/runs/{run_id}/export/{format}")
async def export(run_id: UUID, format: str):
    run = load_run(run_id)
    if format == "json":
        content = run.model_dump_json(indent=2)
        media_type = "application/json"
    elif format == "md":
        content = readable_export(run)
        media_type = "text/markdown"
    elif format == "skills":
        if not run.skill_block:
            raise HTTPException(404, "У этого запуска нет развесовки навыков по заданиям")
        # With a byte-order mark: without it Excel reads the Cyrillic of the header as another encoding.
        return Response("\ufeff" + skills.sheet(run.skill_block), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="skills.csv"'})
    else:
        raise HTTPException(404, "Формат не поддерживается")
    return Response(content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="project-concept.{format}"'})


