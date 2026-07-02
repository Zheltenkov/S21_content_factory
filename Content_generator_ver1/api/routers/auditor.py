"""FastAPI adapter for the content auditor project."""

from __future__ import annotations

import importlib
import os
import shutil
import tarfile
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from types import ModuleType
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from api.db.session import get_db_session
from api.db.tool_runs_db import create_tool_run, get_tool_run, update_tool_run
from api.dependencies import get_current_user
from api.integrations.auth_cookie import clear_auth_cookie, validate_request_user
from api.integrations.project_paths import GENERATOR_ROOT, ensure_import_path, proverka_src_root

router = APIRouter(prefix="/auditor", tags=["auditor"])
page_router = APIRouter(prefix="/app/auditor", include_in_schema=False, tags=["auditor-ui"])

AUDITOR_ROOT = (GENERATOR_ROOT / ".tmp" / "auditor").resolve()
MAX_ARCHIVE_BYTES = int(os.getenv("AUDITOR_MAX_ARCHIVE_BYTES", "250000000"))
SAFE_DOWNLOADS = {"evaluation.json", "report.json", "run_summary.json", "report.csv", "report.xlsx"}


@dataclass
class AuditorJobState:
    """Process-local runtime view for one auditor job."""

    run_id: str
    status: str
    stage: str
    progress: int
    input_ref: str
    output_dir: Path
    summary: dict[str, Any] | None = None
    error: str | None = None
    downloads: list[str] = field(default_factory=list)


_JOBS: dict[str, AuditorJobState] = {}
_JOBS_LOCK = Lock()
_UI_STATES: dict[str, Any] = {}
_UI_REPORTS: dict[str, Any] = {}
_UI_LOCK = Lock()


@page_router.get("")
@page_router.get("/")
async def render_auditor_page(request: Request) -> HTMLResponse:
    """Render the original auditor page inside the shared authenticated service."""

    user = await validate_request_user(request)
    state_key, state = _auditor_page_state(user)
    web_app = _load_auditor_web_app()
    html = web_app.render_page(_UI_REPORTS.get(state_key), state)
    return HTMLResponse(_rewrite_auditor_page_html(html), headers={"Cache-Control": "no-store"})


@page_router.post("/run")
async def run_auditor_page(
    request: Request,
    project_archive: UploadFile | None = File(None),
    input_path: str = Form(""),
) -> HTMLResponse:
    """Run the original auditor workflow and return its full HTML report screen."""

    user = await validate_request_user(request)
    state_key, state = _auditor_page_state(user)
    web_app = _load_auditor_web_app()
    form_values = {"input_path": input_path or ""}
    upload_dir: Path | None = None

    try:
        if project_archive is not None and project_archive.filename:
            upload_dir = _new_ui_work_dir(state_key, "upload")
            archive_name = Path(project_archive.filename).name or "project_archive"
            archive_path = upload_dir / _safe_upload_name(archive_name)
            await _store_upload(project_archive, archive_path)
            form_values[web_app.INTERNAL_ARCHIVE_PATH_FIELD] = str(archive_path)
            form_values[web_app.INTERNAL_ARCHIVE_NAME_FIELD] = archive_name
            form_values[web_app.INTERNAL_UPLOAD_DIR_FIELD] = str(upload_dir)

        report = await run_in_threadpool(web_app.run_from_form, form_values, state)
        state.last_error = None
        with _UI_LOCK:
            _UI_REPORTS[state_key] = report
        html = web_app.render_page(report, state, form_values=form_values)
        return HTMLResponse(_rewrite_auditor_page_html(html), headers={"Cache-Control": "no-store"})
    except Exception as exc:  # noqa: BLE001 - original UI shows domain errors inline.
        if upload_dir is not None:
            shutil.rmtree(upload_dir, ignore_errors=True)
        state.last_error = str(exc)
        with _UI_LOCK:
            _UI_REPORTS.pop(state_key, None)
        html = web_app.render_page(None, state, form_values=form_values)
        return HTMLResponse(
            _rewrite_auditor_page_html(html),
            status_code=400,
            headers={"Cache-Control": "no-store"},
        )


@page_router.get("/download")
async def download_auditor_page_file(
    request: Request,
    file_name: str = Query(alias="file"),
) -> FileResponse:
    """Download report artifacts from the current original-UI report directory."""

    user = await validate_request_user(request)
    _, state = _auditor_page_state(user)
    if file_name not in SAFE_DOWNLOADS:
        raise HTTPException(status_code=400, detail="Недопустимый файл отчёта")

    report_dir = state.report_dir.resolve()
    path = (report_dir / file_name).resolve()
    if report_dir not in path.parents and path != report_dir:
        raise HTTPException(status_code=400, detail="Недопустимый путь отчёта")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Файл отчёта не найден")
    return FileResponse(str(path), filename=file_name)


@page_router.get("/logout")
async def logout_auditor_page() -> RedirectResponse:
    """Use the generator session cookie for logout from the embedded auditor UI."""

    response = RedirectResponse("/", status_code=303)
    clear_auth_cookie(response)
    return response


@router.post("/runs")
async def start_auditor_run(
    background_tasks: BackgroundTasks,
    project_archive: UploadFile | None = File(None),
    input_path: str | None = Form(None),
    use_model: bool = Form(False),
    allow_network: bool = Form(False),
    include_unknown: bool = Form(True),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Create an auditor job and schedule execution after the upload is stored."""

    run_id = str(uuid.uuid4())
    work_dir = (AUDITOR_ROOT / run_id).resolve()
    output_dir = work_dir / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)

    if project_archive is not None and project_archive.filename:
        upload_dir = work_dir / "upload"
        input_dir = work_dir / "input"
        upload_dir.mkdir(parents=True, exist_ok=True)
        input_dir.mkdir(parents=True, exist_ok=True)
        input_ref = upload_dir / _safe_upload_name(project_archive.filename)
        await _store_upload(project_archive, input_ref)
        _extract_archive(input_ref, input_dir)
    else:
        input_dir = _resolve_input_path(input_path)
        input_ref = input_dir

    create_tool_run(
        run_id=run_id,
        tool_name="auditor",
        user_id=str(user.get("id") or ""),
        input_ref=str(input_ref),
        output_ref=str(output_dir),
    )
    state = AuditorJobState(
        run_id=run_id,
        status="pending",
        stage="queued",
        progress=5,
        input_ref=str(input_ref),
        output_dir=output_dir,
    )
    _set_job(state)
    background_tasks.add_task(
        _run_auditor_job,
        run_id,
        input_dir,
        output_dir,
        use_model,
        allow_network,
        include_unknown,
    )
    return _job_payload(state)


def _resolve_input_path(input_path: str | None) -> Path:
    """Validate a local project path submitted from the original auditor menu."""

    raw_path = (input_path or "").strip()
    if not raw_path:
        raise HTTPException(status_code=400, detail="Укажите путь к проекту или приложите архив")
    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        raise HTTPException(status_code=400, detail="Путь к проекту не найден")
    return path


def _load_auditor_web_app() -> ModuleType:
    """Import the original auditor web module after its src directory is on sys.path."""

    ensure_import_path(proverka_src_root())
    return importlib.import_module("content_audit.web_app")


def _auditor_page_state(user: dict[str, Any]) -> tuple[str, Any]:
    """Return the process-local original UI state for a generator user."""

    state_key = _auditor_ui_user_key(user)
    with _UI_LOCK:
        state = _UI_STATES.get(state_key)
        if state is not None:
            return state_key, state

        web_app = _load_auditor_web_app()
        report_dir = (AUDITOR_ROOT / "ui" / state_key / "reports").resolve()
        report_dir.mkdir(parents=True, exist_ok=True)
        state = web_app.WebState(
            default_input=None,
            report_dir=report_dir,
            env_values=web_app.load_env_file(GENERATOR_ROOT / ".env"),
        )
        _UI_STATES[state_key] = state
        return state_key, state


def _auditor_ui_user_key(user: dict[str, Any]) -> str:
    """Build a filesystem-safe key for per-user auditor UI state."""

    raw_key = str(user.get("id") or user.get("email") or user.get("username") or "anonymous")
    safe_key = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in raw_key)
    return safe_key[:80] or "anonymous"


def _new_ui_work_dir(state_key: str, prefix: str) -> Path:
    """Create an isolated work directory for one original-UI upload."""

    work_dir = (AUDITOR_ROOT / "ui" / state_key / f"{prefix}_{uuid.uuid4().hex}").resolve()
    work_dir.mkdir(parents=True, exist_ok=False)
    return work_dir


def _rewrite_auditor_page_html(html: str) -> str:
    """Rewrite absolute links emitted by the original auditor UI for the shared app prefix."""

    replacements = (
        ('action="/run"', 'action="/app/auditor/run"'),
        ('href="/download?file=', 'href="/app/auditor/download?file='),
        ('href="/logout"', 'href="/app/auditor/logout"'),
        ('src="/assets/avatar-placeholder.jpg"', 'src="/static/assets/avatar-placeholder.jpg"'),
    )
    rewritten = html
    for source, target in replacements:
        rewritten = rewritten.replace(source, target)
    return rewritten


@router.get("/runs/{run_id}")
async def get_auditor_run(
    run_id: str,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return the latest runtime or database state for one auditor job."""

    state = _get_job(run_id)
    if state is not None:
        return _job_payload(state)
    run = get_tool_run(db, run_id, user_id=str(user.get("id") or ""))
    if run is None:
        raise HTTPException(status_code=404, detail="Запуск аудитора не найден")
    return {
        "run_id": run.run_id,
        "status": run.status,
        "stage": "restored",
        "progress": 100 if run.status in {"completed", "failed"} else 0,
        "summary": run.summary or {},
        "error": run.error,
        "downloads": _available_downloads(Path(str(run.output_ref or ""))),
    }


@router.get("/runs/{run_id}/download/{file_name}")
async def download_auditor_report(
    run_id: str,
    file_name: str,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> FileResponse:
    """Download one generated auditor artifact."""

    if file_name not in SAFE_DOWNLOADS:
        raise HTTPException(status_code=400, detail="Недопустимый файл отчёта")
    run = get_tool_run(db, run_id, user_id=str(user.get("id") or ""))
    if run is None:
        raise HTTPException(status_code=404, detail="Запуск аудитора не найден")
    output_dir = Path(str(run.output_ref or ""))
    path = (output_dir / file_name).resolve()
    if output_dir and output_dir.resolve() not in path.parents and path != output_dir.resolve():
        raise HTTPException(status_code=400, detail="Недопустимый путь отчёта")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Файл отчёта не найден")
    return FileResponse(str(path), filename=file_name)


async def _store_upload(upload: UploadFile, archive_path: Path) -> None:
    """Stream the uploaded archive to disk with a strict size limit."""

    total = 0
    with archive_path.open("wb") as handle:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_ARCHIVE_BYTES:
                raise HTTPException(status_code=413, detail="Архив слишком большой")
            handle.write(chunk)


def _run_auditor_job(
    run_id: str,
    input_dir: Path,
    output_dir: Path,
    use_model: bool,
    allow_network: bool,
    include_unknown: bool,
) -> None:
    """Run the legacy auditor domain pipeline and persist job state."""

    try:
        _update_job(run_id, status="running", stage="audit", progress=35)
        ensure_import_path(proverka_src_root())
        domain = importlib.import_module("content_audit.domain")
        env = importlib.import_module("content_audit.env")
        exporters = importlib.import_module("content_audit.exporters")
        orchestrator = importlib.import_module("content_audit.orchestrator")

        env_values = env.load_env_file(GENERATOR_ROOT / ".env")
        settings = domain.AuditSettings(
            input_path=input_dir,
            output_path=output_dir,
            allow_network=allow_network,
            use_model=use_model,
            include_unknown=include_unknown,
            openrouter_api_key=env.get_env_value(("OPENROUTER_API_KEY", "OPEN_ROUTER_API_KEY"), env_values),
            openrouter_model=env.get_env_value(("OPENROUTER_MODEL", "OPEN_ROUTER_MODEL"), env_values),
            openrouter_fact_model=env.get_env_value(("OPENROUTER_FACT_MODEL", "OPEN_ROUTER_FACT_MODEL"), env_values),
            openrouter_tech_model=env.get_env_value(("OPENROUTER_TECH_MODEL", "OPEN_ROUTER_TECH_MODEL"), env_values),
        )
        report = orchestrator.AuditRunner(settings).run()
        _update_job(run_id, status="running", stage="export", progress=82)
        exporters.write_report(report, output_dir)
        summary = report.summary.model_dump(mode="json")
        _update_job(
            run_id,
            status="completed",
            stage="completed",
            progress=100,
            summary=summary,
            downloads=_available_downloads(output_dir),
        )
        update_tool_run(run_id, status="completed", summary=summary, output_ref=str(output_dir))
    except Exception as exc:  # noqa: BLE001 - worker errors must be returned to UI and persisted.
        message = str(exc)
        _update_job(run_id, status="failed", stage="failed", progress=100, error=message)
        update_tool_run(run_id, status="failed", error=message, output_ref=str(output_dir))


def _extract_archive(archive_path: Path, target_dir: Path) -> None:
    """Extract supported archives while preventing path traversal."""

    suffixes = "".join(archive_path.suffixes).lower()
    if suffixes.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                _ensure_safe_member(target_dir, member.filename)
            archive.extractall(target_dir)
        return
    if suffixes.endswith((".tar", ".tar.gz", ".tgz")):
        with tarfile.open(archive_path) as archive:
            tar_members = archive.getmembers()
            for tar_member in tar_members:
                if tar_member.issym() or tar_member.islnk():
                    raise HTTPException(status_code=400, detail="Архив содержит недопустимые ссылки")
                _ensure_safe_member(target_dir, tar_member.name)
            archive.extractall(target_dir)
        return
    raise HTTPException(status_code=400, detail="Поддерживаются ZIP, TAR и TAR.GZ архивы")


def _ensure_safe_member(root: Path, member_name: str) -> None:
    """Reject archive members that would escape the extraction directory."""

    target = (root / member_name).resolve()
    if root not in target.parents and target != root:
        raise HTTPException(status_code=400, detail="Архив содержит недопустимые пути")


def _safe_upload_name(name: str) -> str:
    """Normalize uploaded archive names for local storage."""

    clean = Path(name).name.strip() or "project.zip"
    return "".join(char if char.isalnum() or char in {".", "-", "_"} else "_" for char in clean)


def _available_downloads(output_dir: Path) -> list[str]:
    """Return downloadable artifacts that were produced for the job."""

    if not output_dir.exists():
        return []
    return [name for name in sorted(SAFE_DOWNLOADS) if (output_dir / name).exists()]


def _set_job(state: AuditorJobState) -> None:
    with _JOBS_LOCK:
        _JOBS[state.run_id] = state


def _get_job(run_id: str) -> AuditorJobState | None:
    with _JOBS_LOCK:
        return _JOBS.get(run_id)


def _update_job(
    run_id: str,
    *,
    status: str,
    stage: str,
    progress: int,
    summary: dict[str, Any] | None = None,
    downloads: list[str] | None = None,
    error: str | None = None,
) -> None:
    with _JOBS_LOCK:
        state = _JOBS.get(run_id)
        if state is None:
            return
        state.status = status
        state.stage = stage
        state.progress = progress
        state.summary = summary if summary is not None else state.summary
        state.downloads = downloads if downloads is not None else state.downloads
        state.error = error


def _job_payload(state: AuditorJobState) -> dict[str, Any]:
    """Return a stable browser contract for auditor polling."""

    return {
        "run_id": state.run_id,
        "status": state.status,
        "stage": state.stage,
        "progress": state.progress,
        "summary": state.summary or {},
        "error": state.error,
        "downloads": state.downloads,
    }
