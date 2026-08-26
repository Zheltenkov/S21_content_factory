"""FastAPI adapter for the content auditor project."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock, Thread
from types import ModuleType
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from content_factory.api.db.session import get_db_session
from content_factory.api.db.tool_runs_db import create_tool_run, get_tool_run, update_tool_run
from content_factory.api.dependencies import get_current_user
from content_factory.api.integrations.auth_cookie import clear_auth_cookie, validate_request_user
from content_factory.api.integrations.project_paths import GENERATOR_ROOT, WORKSPACE_ROOT
from content_factory.audit.env import load_env_file

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

# Branch runs (a branch = an archive whose entries are projects). Kept process-local like the
# single-run UI: the BranchReport for the summary, the per-project AuditReport objects for the
# full-report drill-down, and the work dir to reclaim on eviction. _UI_LOCK guards all three.
_BRANCH_RUNS: dict[str, Any] = {}
_BRANCH_REPORTS: dict[str, dict[int, Any]] = {}
_BRANCH_WORK_DIRS: dict[str, Path] = {}
# Live progress for in-flight (and finished) branch jobs: branch_id -> mutable status dict.
_BRANCH_JOBS: dict[str, dict[str, Any]] = {}
# Metadata for finished runs (created_at, archive name), for the "recent branches" list + reload.
_BRANCH_CREATED: dict[str, dict[str, str]] = {}
# Archives uploaded on file-select but not yet audited: upload_id -> {work_dir, archive_path, archive_name}.
_BRANCH_UPLOADS: dict[str, dict[str, Any]] = {}
# Same, for single-project runs — the "Архив проекта" card uploads on file-select (live %) like the
# branch card, then the run consumes the upload_id instead of re-posting the file on submit.
_RUN_UPLOADS: dict[str, dict[str, Any]] = {}
_MAX_BRANCH_RUNS = 300
_MAX_PENDING_UPLOADS = 10
_BRANCH_META_FILE = "branch_meta.json"
_BRANCH_JOB_FILE = "branch_job.json"
_BRANCHES_LOADED = False

# Single-project runs (archive/path) persisted like branches, so a finished audit is findable after
# a refresh / sleep / restart — not just the last one held in _UI_REPORTS. Per-user: keyed by the
# same state_key as _UI_STATES. Each run gets its own report dir under ui/{state_key}/runs/{run_id}.
_RECENT_RUNS: dict[str, list[dict[str, Any]]] = {}
# The report dir backing the currently-shown (_UI_REPORTS) report per user, so the main page's
# downloads keep targeting it even after a branch/old-run view mutated state.report_dir.
_UI_REPORT_DIR: dict[str, Path] = {}
_MAX_SINGLE_RUNS = 12  # per-user disk retention cap
_MAX_RECENT_RUNS_PANEL = 60  # runs shown in the shared 'recent runs' panel (across all users)
_RUN_META_FILE = "run_meta.json"


@page_router.get("")
@page_router.get("/")
async def render_auditor_page(request: Request) -> HTMLResponse:
    """Render the original auditor page inside the shared authenticated service."""

    user = await validate_request_user(request)
    _ensure_branches_loaded()
    state_key, state = _auditor_page_state(user)
    # Point downloads at the shown report's own dir (a prior branch/old-run view may have moved it).
    with _UI_LOCK:
        current_dir = _UI_REPORT_DIR.get(state_key)
    if current_dir is not None:
        state.report_dir = current_dir
    web_app = _load_auditor_web_app()
    html = web_app.render_page(_UI_REPORTS.get(state_key), state)
    page = _rewrite_auditor_page_html(html)
    recent_html = _render_recent_branches() + _render_recent_runs()
    if recent_html:
        recent_html += _RECENT_PANEL_SCRIPT  # client-side search/sort for both panels
    page = page.replace("<!--RECENT_BRANCHES_SLOT-->", recent_html, 1)
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@page_router.post("/run/upload")
async def upload_auditor_run(
    request: Request,
    project_archive: UploadFile | None = File(None),
) -> Response:
    """Receive the single-project archive the moment it is chosen (so the browser can show upload %),
    stash it, and return an upload_id the run request references. Mirrors the branch upload flow."""

    user = await validate_request_user(request)
    state_key, _state = _auditor_page_state(user)
    if project_archive is None or not project_archive.filename:
        raise HTTPException(status_code=400, detail="Не выбран архив проекта")

    work_dir = _new_ui_work_dir(state_key, "upload")
    archive_name = Path(project_archive.filename).name or "project_archive"
    archive_path = work_dir / _safe_upload_name(archive_name)
    await _store_upload(project_archive, archive_path)
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=413, detail="Архив слишком большой")

    upload_id = uuid.uuid4().hex
    with _UI_LOCK:
        _RUN_UPLOADS[upload_id] = {
            "work_dir": work_dir,
            "archive_path": archive_path,
            "archive_name": archive_name,
        }
        _evict_old_run_uploads()
    return JSONResponse({"upload_id": upload_id, "archive_name": archive_name})


@page_router.post("/run")
async def run_auditor_page(
    request: Request,
    project_archive: UploadFile | None = File(None),
    input_path: str = Form(""),
    upload_id: str = Form(""),
) -> HTMLResponse:
    """Run the original auditor workflow and return its full HTML report screen.

    The archive can arrive two ways: pre-uploaded on file-select (``upload_id``, the card's default
    path — no re-post on submit) or inline as ``project_archive`` (fallback). ``upload_id`` wins.
    """

    user = await validate_request_user(request)
    _ensure_branches_loaded()
    state_key, state = _auditor_page_state(user)
    web_app = _load_auditor_web_app()
    form_values = {"input_path": input_path or ""}
    upload_dir: Path | None = None

    # Each run writes into its OWN report dir (ui/{state_key}/runs/{run_id}) rather than the shared
    # per-user reports dir — so finished runs persist side by side (recent list, survive restart)
    # instead of overwriting each other, and a prior branch/old-run view can't leak its dir here.
    run_id = uuid.uuid4().hex
    run_dir = _single_run_dir(state_key, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    state.report_dir = run_dir

    pending = None
    if upload_id:
        with _UI_LOCK:
            pending = _RUN_UPLOADS.pop(upload_id, None)

    try:
        if pending is not None:
            # Archive already on the server (uploaded on file-select) — audit it in place.
            upload_dir = pending["work_dir"]
            form_values[web_app.INTERNAL_ARCHIVE_PATH_FIELD] = str(pending["archive_path"])
            form_values[web_app.INTERNAL_ARCHIVE_NAME_FIELD] = pending["archive_name"]
            form_values[web_app.INTERNAL_UPLOAD_DIR_FIELD] = str(upload_dir)
        elif project_archive is not None and project_archive.filename:
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
            _UI_REPORT_DIR[state_key] = run_dir
        _persist_single_run(state_key, run_id, run_dir, report)
        html = web_app.render_page(report, state, form_values=form_values)
        return HTMLResponse(_rewrite_auditor_page_html(html), headers={"Cache-Control": "no-store"})
    except Exception as exc:  # noqa: BLE001 - original UI shows domain errors inline.
        if upload_dir is not None:
            shutil.rmtree(upload_dir, ignore_errors=True)
        shutil.rmtree(run_dir, ignore_errors=True)  # failed run persisted nothing worth keeping
        state.last_error = str(exc)
        with _UI_LOCK:
            _UI_REPORTS.pop(state_key, None)
        html = web_app.render_page(None, state, form_values=form_values)
        return HTMLResponse(
            _rewrite_auditor_page_html(html),
            status_code=400,
            headers={"Cache-Control": "no-store"},
        )


@page_router.get("/run/{run_id}")
async def open_auditor_run(request: Request, run_id: str) -> HTMLResponse:
    """Reopen a persisted single-project run (from the 'recent runs' list) after a refresh/restart."""

    user = await validate_request_user(request)
    _ensure_branches_loaded()
    _state_key, state = _auditor_page_state(user)
    meta = _find_single_run(run_id)  # shared across users, like branch runs
    if meta is None:
        raise HTTPException(status_code=404, detail="Проверка не найдена или устарела")
    report_dir = Path(str(meta.get("report_dir") or ""))
    report = _load_single_report(report_dir)
    if report is None:
        raise HTTPException(status_code=404, detail="Отчёт проверки не найден")
    # Point downloads at this run's own dir for as long as its page is shown; the main page restores
    # the current report's dir on its next GET, so this does not stick.
    state.report_dir = report_dir
    web_app = _load_auditor_web_app()
    html = web_app.render_page(report, state)
    back = (
        '<a href="/app/auditor" onclick="event.stopPropagation()" '
        'style="display:inline-flex;align-items:center;margin-right:10px;padding:8px 14px;'
        "background:#10b981;color:#fff;border-radius:999px;text-decoration:none;font-weight:600;"
        'font-size:13px;">← К аудитору</a>'
    )
    page = _rewrite_auditor_page_html(html)
    actions_anchor = '<span class="run-bar-actions">'
    results_heading = "<h2>Таблица результата</h2>"
    if actions_anchor in page:
        page = page.replace(actions_anchor, actions_anchor + back, 1)
    elif results_heading in page:
        page = page.replace(results_heading, results_heading + back, 1)
    else:
        page = f'<div style="max-width:1080px;margin:12px auto;padding:0 16px;">{back}</div>' + page
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@page_router.post("/run/{run_id}/delete")
async def delete_auditor_run(request: Request, run_id: str) -> RedirectResponse:
    """Remove one single-project run from the recent list and reclaim its report dir."""

    await validate_request_user(request)
    _ensure_branches_loaded()
    with _UI_LOCK:
        for owner_key, runs in _RECENT_RUNS.items():
            meta = next((item for item in runs if item.get("run_id") == run_id), None)
            if meta is None:
                continue
            runs.remove(meta)  # shared: delete any user's run, like a branch run
            report_dir = Path(str(meta.get("report_dir") or ""))
            shutil.rmtree(report_dir, ignore_errors=True)
            if _UI_REPORT_DIR.get(owner_key) == report_dir:
                _UI_REPORT_DIR.pop(owner_key, None)
                _UI_REPORTS.pop(owner_key, None)
            break
    return RedirectResponse("/app/auditor", status_code=303)


def _download_name(report_dir: Path, file_name: str) -> str:
    """Default download filename to the project name when the report dir carries one.

    Branch drill-down points the report dir at a project folder (e.g. ``DSB6_...-master.zip``),
    so ``report.xlsx`` downloads as ``DSB6_..._report.xlsx``. Single runs use a generic
    ``reports`` dir — keep the plain filename there.
    """
    project = report_dir.name
    if project in {"reports", "", "."}:
        return file_name
    lowered = project.lower()
    for suffix in (".tar.gz", ".tgz", ".tar", ".zip"):
        if lowered.endswith(suffix):
            project = project[: -len(suffix)]
            break
    project = project.strip("._-") or "report"
    stem, ext = os.path.splitext(file_name)
    return f"{project}_{stem}{ext}"


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
    return FileResponse(str(path), filename=_download_name(report_dir, file_name))


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

    from content_factory.audit import web_app

    return web_app


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
            env_values=load_env_file(GENERATOR_ROOT / ".env"),
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


# The branch "card" that fills the <!--BRANCH_CARD_SLOT--> the run panel leaves as the first cell
# of its .intake-grid. The branch route (/app/auditor/branch) stays here rather than in the shared
# web_rendering module, so the audit engine's own UI does not learn about the S21 app mount.
# The grid styles are injected once alongside it; #run-form is display:contents so its own two
# cards (Архив / Путь) become grid cells next to this one — three cards in one row.
_INTAKE_GRID_STYLE = """
<style id="intake-grid-style">
.intake-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;align-items:stretch;margin-top:12px}
.intake-run-form{display:contents}
.intake-card{display:flex;flex-direction:column;gap:10px;padding:16px;border:1px solid #e2e8f0;border-radius:12px;background:#fff}
.intake-card-title{font-weight:700;font-size:14px;color:#0f172a}
.intake-card-hint{font-size:12px;color:#64748b}
.intake-card .upload-zone{margin:0;flex:1 1 auto;display:flex;flex-direction:column;justify-content:center}
.intake-card .button{align-self:flex-start;margin-top:auto;opacity:.45;box-shadow:none;transition:opacity .15s ease,box-shadow .15s ease}
.intake-card .button.is-ready{opacity:1;box-shadow:0 2px 10px rgba(16,185,129,.45)}
.intake-card input[type=text]{width:100%}
.branch-card{border-color:#34d399;background:#f0fdf9}
.branch-progress{margin-top:10px}
.branch-progress-label{font-size:13px;font-weight:700;color:#0e8f6f;margin-bottom:6px}
.branch-progress-track{height:10px;border-radius:999px;background:#d1fae5;overflow:hidden}
.branch-progress-track>b{display:block;height:100%;width:0;border-radius:999px;background:#10b981;transition:width .2s ease}
#run-progress{grid-column:1 / -1}
@media (max-width:860px){.intake-grid{grid-template-columns:1fr}}
</style>
"""

_BRANCH_FORM_HTML = """
    <form method="post" action="/app/auditor/branch" enctype="multipart/form-data" class="intake-card branch-card branch-run-form">
      <div class="intake-card-title">Ветка</div>
      <label class="upload-zone" for="branch_archive_input">
        <input id="branch_archive_input" name="branch_archive" type="file" accept=".zip,.tar,.gz,.tgz,.rar">
        <span class="upload-title">Загрузить ветку</span>
        <span class="upload-name">Архив (ZIP/TAR/RAR) архивов/папок проектов — в каждом README</span>
      </label>
      <button class="button" type="submit">Проверить ветку</button>
      <div class="branch-progress" hidden aria-live="polite">
        <div class="branch-progress-label" id="branch-upload-label">Загрузка архива…</div>
        <div class="branch-progress-track"><b id="branch-upload-fill"></b></div>
      </div>
    </form>
    <script>
      (function () {
        // Branch flow: upload the archive to the server the moment it is chosen (live percent),
        // light the button only once it is on the server, then start the audit on click.
        var form = document.querySelector(".branch-run-form");
        if (!form || form.dataset.uploadBound) return;
        form.dataset.uploadBound = "1";
        var fileInput = form.querySelector("input[type=file]");
        var button = form.querySelector("button[type=submit]");
        var progress = form.querySelector(".branch-progress");
        var label = form.querySelector("#branch-upload-label");
        var fill = form.querySelector("#branch-upload-fill");
        var nameSpan = form.querySelector(".upload-name");
        var defaultName = nameSpan ? nameSpan.textContent : "";
        function setReady(r) { if (button) button.classList.toggle("is-ready", !!r); }
        setReady(false);
        if (fileInput) fileInput.addEventListener("change", function () {
          form.dataset.uploadId = "";
          setReady(false);
          if (!fileInput.files || !fileInput.files.length) {
            if (nameSpan) nameSpan.textContent = defaultName;
            if (progress) progress.hidden = true;
            return;
          }
          var file = fileInput.files[0];
          if (nameSpan) nameSpan.textContent = file.name;
          if (progress) progress.hidden = false;
          if (fill) fill.style.width = "0%";
          if (label) label.textContent = "Загрузка архива: 0%";
          var body = new FormData();
          body.append("branch_archive", file);
          var xhr = new XMLHttpRequest();
          xhr.open("POST", "/app/auditor/branch/upload", true);
          xhr.upload.onprogress = function (ev) {
            if (!ev.lengthComputable) return;
            var pct = Math.round(ev.loaded / ev.total * 100);
            if (fill) fill.style.width = pct + "%";
            if (label) label.textContent = pct < 100 ? ("Загрузка архива: " + pct + "%") : "Обработка загрузки…";
          };
          xhr.onload = function () {
            var uploadId = "";
            if (xhr.status >= 200 && xhr.status < 300) {
              try { uploadId = (JSON.parse(xhr.responseText) || {}).upload_id || ""; } catch (e) {}
            }
            if (uploadId) {
              form.dataset.uploadId = uploadId;
              if (fill) fill.style.width = "100%";
              if (label) label.textContent = "Архив на сервере ✓ — можно запускать";
              setReady(true);
            } else if (label) {
              label.textContent = "Ошибка загрузки архива (" + xhr.status + ")";
            }
          };
          xhr.onerror = function () { if (label) label.textContent = "Ошибка сети при загрузке"; };
          xhr.send(body);
        });
        form.addEventListener("submit", function (e) {
          e.preventDefault();
          var uploadId = form.dataset.uploadId;
          if (!uploadId) { if (label) { if (progress) progress.hidden = false; label.textContent = "Сначала загрузите архив ветки"; } return; }
          if (button) { button.disabled = true; button.textContent = "Запускаю аудит…"; }
          var body = new URLSearchParams();
          body.append("upload_id", uploadId);
          fetch(form.getAttribute("action"), {
            method: "POST", body: body,
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
          }).then(function (r) { return r.json(); })
            .then(function (d) { if (d && d.progress_url) { window.location.href = d.progress_url; } else { throw new Error("no url"); } })
            .catch(function () {
              if (label) label.textContent = "Не удалось запустить аудит";
              if (button) { button.disabled = false; button.textContent = "Проверить ветку"; }
            });
        });
      })();
    </script>
    <script>
      (function () {
        // Each single-audit card's run button stays dimmed until its input is filled, then lights
        // up. The branch and archive cards manage their own readiness (after their upload finishes),
        // so skip them here.
        document.querySelectorAll(".intake-card").forEach(function (card) {
          if (card.dataset.readyBound || card.classList.contains("branch-card") || card.classList.contains("archive-card")) return;
          card.dataset.readyBound = "1";
          var button = card.querySelector("button[type=submit], .button");
          var fileInput = card.querySelector("input[type=file]");
          var textInput = card.querySelector("input[type=text]");
          function setReady(ready) { if (button) button.classList.toggle("is-ready", !!ready); }
          if (fileInput) {
            var nameSpan = card.querySelector(".upload-name");
            var defaultName = nameSpan ? nameSpan.textContent : "";
            fileInput.addEventListener("change", function () {
              var has = fileInput.files && fileInput.files.length;
              if (nameSpan) nameSpan.textContent = has ? fileInput.files[0].name : defaultName;
              setReady(has);
            });
          }
          if (textInput) {
            var sync = function () { setReady(textInput.value.trim().length > 0); };
            textInput.addEventListener("input", sync);
            sync();
          }
        });
      })();
    </script>
    <script>
      (function () {
        // "Архив проекта" mirrors the branch card: upload the file to the server the moment it is
        // chosen (live percent), light the run button only once it is on the server, and carry an
        // upload_id on submit so the file is not re-posted. Falls back to a plain multipart submit
        // if scripting is off (the file input still posts normally).
        function initArchiveUpload() {
          var card = document.querySelector(".archive-card");
          if (!card || card.dataset.uploadBound) return;
          var fileInput = card.querySelector("input[type=file]");
          var button = card.querySelector("button[type=submit]");
          var hidden = document.getElementById("archive-upload-id");
          var progress = document.getElementById("archive-upload-progress");
          var label = document.getElementById("archive-upload-label");
          var fill = document.getElementById("archive-upload-fill");
          var nameSpan = card.querySelector(".upload-name");
          var defaultName = nameSpan ? nameSpan.textContent : "";
          if (!fileInput || !hidden) return;
          card.dataset.uploadBound = "1";
          function setReady(r) { if (button) button.classList.toggle("is-ready", !!r); }
          function reset() {
            hidden.value = "";
            setReady(false);
            if (progress) progress.hidden = true;
            if (nameSpan) nameSpan.textContent = defaultName;
          }
          setReady(false);
          fileInput.addEventListener("change", function () {
            hidden.value = "";
            setReady(false);
            if (!fileInput.files || !fileInput.files.length) { reset(); return; }
            var file = fileInput.files[0];
            if (nameSpan) nameSpan.textContent = file.name;
            if (progress) progress.hidden = false;
            if (fill) fill.style.width = "0%";
            if (label) label.textContent = "Загрузка архива: 0%";
            var body = new FormData();
            body.append("project_archive", file);
            var xhr = new XMLHttpRequest();
            xhr.open("POST", "/app/auditor/run/upload", true);
            xhr.upload.onprogress = function (ev) {
              if (!ev.lengthComputable) return;
              var pct = Math.round(ev.loaded / ev.total * 100);
              if (fill) fill.style.width = pct + "%";
              if (label) label.textContent = pct < 100 ? ("Загрузка архива: " + pct + "%") : "Обработка загрузки…";
            };
            xhr.onload = function () {
              var uploadId = "";
              if (xhr.status >= 200 && xhr.status < 300) {
                try { uploadId = (JSON.parse(xhr.responseText) || {}).upload_id || ""; } catch (e) {}
              }
              if (uploadId) {
                hidden.value = uploadId;
                fileInput.value = "";  // don't re-post the file on submit; upload_id carries it
                if (fill) fill.style.width = "100%";
                if (label) label.textContent = "Архив на сервере ✓ — можно запускать";
                if (nameSpan) nameSpan.textContent = file.name;
                setReady(true);
              } else if (label) {
                label.textContent = "Ошибка загрузки архива (" + xhr.status + ")";
              }
            };
            xhr.onerror = function () { if (label) label.textContent = "Ошибка сети при загрузке"; };
            xhr.send(body);
          });
          // Typing a local path clears a stale archive upload so the path wins on submit.
          var pathInput = document.getElementById("input_path");
          if (pathInput) pathInput.addEventListener("input", function () {
            if (pathInput.value.trim().length && hidden.value) { reset(); fileInput.value = ""; }
          });
        }
        if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initArchiveUpload);
        else initArchiveUpload();
      })();
    </script>
"""


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
    # Fill the branch-card slot the run panel leaves in its .intake-grid (idempotent: the marker
    # exists once and is consumed). Inject the grid styles alongside it, once.
    if "<!--BRANCH_CARD_SLOT-->" in rewritten:
        rewritten = rewritten.replace("<!--BRANCH_CARD_SLOT-->", _INTAKE_GRID_STYLE + _BRANCH_FORM_HTML, 1)
    return rewritten


def _run_single_project_audit(
    project_dir: Path,
    output_dir: Path,
    *,
    use_model: bool,
    allow_network: bool,
    include_unknown: bool,
) -> Any:
    """Run the single-project audit engine on one project dir. Mirrors _run_auditor_job settings."""

    from content_factory.audit import domain, env, exporters, orchestrator

    env_values = env.load_env_file(WORKSPACE_ROOT / ".env")
    settings = domain.AuditSettings(
        input_path=project_dir,
        output_path=output_dir,
        allow_network=allow_network,
        use_model=use_model,
        include_unknown=include_unknown,
        openrouter_api_key=env.get_env_value(("POLZA_AI_API_KEY", "OPENROUTER_API_KEY", "OPEN_ROUTER_API_KEY"), env_values),
        openrouter_model=env.get_env_value(("OPENROUTER_MODEL", "OPEN_ROUTER_MODEL"), env_values),
        openrouter_fact_model=env.get_env_value(("OPENROUTER_FACT_MODEL", "OPEN_ROUTER_FACT_MODEL"), env_values),
        openrouter_tech_model=env.get_env_value(("OPENROUTER_TECH_MODEL", "OPEN_ROUTER_TECH_MODEL"), env_values),
    )
    report = orchestrator.AuditRunner(settings).run()
    exporters.write_report(report, output_dir)
    return report



def _evict_old_branch_runs() -> None:
    """Cap process-local branch runs; reclaim the work dir of anything evicted. Call under _UI_LOCK."""

    while len(_BRANCH_RUNS) > _MAX_BRANCH_RUNS:
        oldest = next(iter(_BRANCH_RUNS))
        _BRANCH_RUNS.pop(oldest, None)
        _BRANCH_REPORTS.pop(oldest, None)
        _BRANCH_JOBS.pop(oldest, None)
        _BRANCH_CREATED.pop(oldest, None)
        work_dir = _BRANCH_WORK_DIRS.pop(oldest, None)
        if work_dir is not None:
            shutil.rmtree(work_dir, ignore_errors=True)


def _prune_branch_workdir(work_dir: Path, out_root: Path) -> None:
    """After a branch audit, drop the intermediate artifacts (raw archive, extracted branch and
    inner-archive scratch) and keep only what the summary/drill-down need: the reports + meta."""
    keep = {out_root.name, _BRANCH_META_FILE}
    try:
        children = list(work_dir.iterdir())
    except OSError:
        return
    for child in children:
        if child.name in keep:
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                child.unlink()
            except OSError:
                pass


def _evict_old_branch_uploads() -> None:
    """Cap pending (uploaded-but-not-audited) archives; reclaim evicted work dirs. Under _UI_LOCK."""
    while len(_BRANCH_UPLOADS) > _MAX_PENDING_UPLOADS:
        oldest = next(iter(_BRANCH_UPLOADS))
        upload = _BRANCH_UPLOADS.pop(oldest, None)
        if upload is not None:
            shutil.rmtree(upload["work_dir"], ignore_errors=True)


def _evict_old_run_uploads() -> None:
    """Cap pending single-run archives; reclaim evicted work dirs. Call under _UI_LOCK."""
    while len(_RUN_UPLOADS) > _MAX_PENDING_UPLOADS:
        oldest = next(iter(_RUN_UPLOADS))
        upload = _RUN_UPLOADS.pop(oldest, None)
        if upload is not None:
            shutil.rmtree(upload["work_dir"], ignore_errors=True)


def _delete_branch(branch_id: str) -> bool:
    """Drop a branch run from every store and reclaim its work dir (incl. persisted meta)."""
    with _UI_LOCK:
        existed = branch_id in _BRANCH_RUNS or branch_id in _BRANCH_JOBS
        _BRANCH_RUNS.pop(branch_id, None)
        _BRANCH_REPORTS.pop(branch_id, None)
        _BRANCH_JOBS.pop(branch_id, None)
        _BRANCH_CREATED.pop(branch_id, None)
        work_dir = _BRANCH_WORK_DIRS.pop(branch_id, None)
    if work_dir is not None:
        shutil.rmtree(work_dir, ignore_errors=True)
    return existed


def _flush_job_file(branch_id: str, work_dir: Path, archive_name: str, created_at: str) -> None:
    """Persist the live job progress so a page refresh (even after a restart) can read it.

    The audit thread cannot resume across a restart, but writing progress lets the status endpoint
    return the last known state (marked interrupted on reload) instead of a bare 404.
    """
    with _UI_LOCK:
        job = dict(_BRANCH_JOBS.get(branch_id) or {})
    if not job:
        return
    payload = {"branch_id": branch_id, "archive_name": archive_name, "created_at": created_at, "job": job}
    try:
        (work_dir / _BRANCH_JOB_FILE).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _persist_branch(branch_id: str, report: Any, work_dir: Path, archive_name: str, created_at: str) -> None:
    """Write a branch run's index to disk so its summary/drill-down survive a restart.

    The per-project report artifacts already live under ``work_dir``; this only records the light
    index (verdicts + report_dir per project) that the in-memory store would otherwise lose.
    """
    meta = {
        "branch_id": branch_id,
        "created_at": created_at,
        "archive_name": archive_name,
        "report": report.as_dict(),
    }
    try:
        (work_dir / _BRANCH_META_FILE).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # persistence is best-effort; the in-memory copy still serves this process.


def _load_persisted_branches() -> None:
    """Repopulate the in-memory branch stores from any branch_meta.json left on disk (startup).

    Loads only the newest ``_MAX_BRANCH_RUNS`` and never deletes anything — reload must not evict
    on-disk artifacts, only re-index what is already there.
    """
    from content_factory.audit.branch import BranchReport

    root = AUDITOR_ROOT
    if not root.exists():
        return
    loaded: list[tuple[str, str, BranchReport, Path, dict[str, str]]] = []
    for meta_path in root.rglob(_BRANCH_META_FILE):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            branch_id = str(meta["branch_id"])
            report = BranchReport.from_dict(meta["report"])
            created_at = str(meta.get("created_at", ""))
            info = {"created_at": created_at, "archive_name": str(meta.get("archive_name", ""))}
            loaded.append((branch_id, created_at, report, meta_path.parent, info))
        except (OSError, ValueError, KeyError):
            continue
    for branch_id, _created, report, work_dir, info in sorted(loaded, key=lambda item: item[1], reverse=True)[
        :_MAX_BRANCH_RUNS
    ]:
        with _UI_LOCK:
            if branch_id in _BRANCH_RUNS:
                continue
            _BRANCH_RUNS[branch_id] = report
            _BRANCH_WORK_DIRS[branch_id] = work_dir
            _BRANCH_CREATED[branch_id] = info

    # A job file without a completed meta means the run was cut off by a restart — the audit thread
    # is gone and cannot resume, so surface it as "interrupted" instead of a bare 404.
    for job_path in root.rglob(_BRANCH_JOB_FILE):
        try:
            payload = json.loads(job_path.read_text(encoding="utf-8"))
            branch_id = str(payload["branch_id"])
            job = dict(payload.get("job") or {})
        except (OSError, ValueError, KeyError):
            continue
        with _UI_LOCK:
            if branch_id in _BRANCH_RUNS or branch_id in _BRANCH_JOBS:
                continue
            job["state"] = "interrupted"
            _BRANCH_JOBS[branch_id] = job
            _BRANCH_WORK_DIRS[branch_id] = job_path.parent


def _ensure_branches_loaded() -> None:
    """Reload persisted branch runs once per process, on the first auditor request."""
    global _BRANCHES_LOADED
    if _BRANCHES_LOADED:
        return
    _BRANCHES_LOADED = True
    _load_persisted_branches()
    _load_persisted_runs()


_RECENT_SORT_BTN_STYLE = (
    "border:1px solid #cfe0da;background:#fff;color:#2a3b36;border-radius:999px;"
    "padding:4px 10px;font:12px system-ui;cursor:pointer;"
)
_RECENT_SEARCH_STYLE = (
    "height:30px;border:1px solid #cfe0da;border-radius:999px;padding:0 12px;"
    "font:13px system-ui;outline:none;min-width:150px;"
)
_RECENT_LIST_STYLE = "list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:6px;"


def _recent_panel_head(title: str) -> str:
    """Toolbar (search by name + sort by name/date/errors) shared by both recent panels."""
    from html import escape

    return (
        '<div class="recent-head" style="display:flex;justify-content:space-between;align-items:center;'
        'gap:12px;margin-bottom:8px;flex-wrap:wrap;">'
        f'<div style="font-weight:700;">{escape(title)}</div>'
        '<div class="recent-controls" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">'
        f'<input type="search" class="recent-search" placeholder="Поиск по названию…" style="{_RECENT_SEARCH_STYLE}">'
        f'<button type="button" class="recent-sort" data-sort="name" style="{_RECENT_SORT_BTN_STYLE}">Название</button>'
        f'<button type="button" class="recent-sort" data-sort="date" style="{_RECENT_SORT_BTN_STYLE}">Дата</button>'
        f'<button type="button" class="recent-sort" data-sort="errors" style="{_RECENT_SORT_BTN_STYLE}">Ошибки</button>'
        "</div></div>"
    )


# Client-side filter/sort for both recent panels: search by name, sort by name/date/errors (toggling
# direction on re-click). Reorders the existing <li> rows in place — no server round-trip.
_RECENT_PANEL_SCRIPT = """
<script>
(function () {
  function initPanel(panel) {
    var list = panel.querySelector('.recent-list');
    if (!list) return;
    var rows = Array.prototype.slice.call(list.querySelectorAll('.recent-row'));
    var search = panel.querySelector('.recent-search');
    var state = { key: null, dir: 1 };
    function applyFilter() {
      var q = (search && search.value || '').trim().toLowerCase();
      rows.forEach(function (r) {
        var name = r.getAttribute('data-name') || '';
        r.style.display = (!q || name.indexOf(q) !== -1) ? '' : 'none';
      });
    }
    function sortBy(key) {
      state.dir = (state.key === key) ? -state.dir : 1;
      state.key = key;
      rows.slice().sort(function (a, b) {
        if (key === 'errors') return ((+a.getAttribute('data-errors') || 0) - (+b.getAttribute('data-errors') || 0)) * state.dir;
        if (key === 'date') { var da = a.getAttribute('data-ts') || '', db = b.getAttribute('data-ts') || ''; return (da < db ? -1 : da > db ? 1 : 0) * state.dir; }
        return (a.getAttribute('data-name') || '').localeCompare(b.getAttribute('data-name') || '') * state.dir;
      }).forEach(function (r) { list.appendChild(r); });
      panel.querySelectorAll('.recent-sort').forEach(function (btn) {
        var active = btn.getAttribute('data-sort') === key;
        btn.style.fontWeight = active ? '700' : '400';
        btn.textContent = btn.textContent.replace(/ [\\u25B2\\u25BC]$/, '') + (active ? (state.dir > 0 ? ' \\u25B2' : ' \\u25BC') : '');
      });
    }
    if (search) search.addEventListener('input', applyFilter);
    panel.querySelectorAll('.recent-sort').forEach(function (btn) {
      btn.addEventListener('click', function () { sortBy(btn.getAttribute('data-sort')); });
    });
  }
  document.querySelectorAll('.recent-panel').forEach(initPanel);
})();
</script>
"""


def _render_recent_branches() -> str:
    """A compact 'recent branches' panel for the main auditor page, so a finished branch run is
    findable after a refresh/restart even without its URL. Empty string when there are none."""
    from html import escape

    with _UI_LOCK:
        items = [
            (branch_id, dict(_BRANCH_CREATED.get(branch_id, {})), report)
            for branch_id, report in _BRANCH_RUNS.items()
        ]
    if not items:
        return ""
    items.sort(key=lambda item: item[1].get("created_at", ""), reverse=True)
    rows = []
    for branch_id, info, report in items[:_MAX_BRANCH_RUNS]:
        raw_name = info.get("archive_name") or "Ветка"
        name = escape(raw_name)
        created_raw = info.get("created_at") or ""
        created = escape(created_raw.replace("T", " ")[:16])
        link = f"/app/auditor/branch/{branch_id}"
        delete_action = f"/app/auditor/branch/{branch_id}/delete"
        rows.append(
            f'<li class="recent-row" data-name="{escape(raw_name.lower(), quote=True)}" '
            f'data-ts="{escape(created_raw, quote=True)}" data-errors="{report.failed_count}" '
            'style="display:flex;align-items:center;justify-content:space-between;gap:12px;">'
            f'<span><a href="{link}" style="color:#0e8f6f;text-decoration:none;font-weight:600;">{name}</a>'
            f'<span style="color:#8a9691;font-size:.85rem;"> · {report.completed_count} проверено · '
            f"{report.failed_count} с ошибкой · {created}</span></span>"
            f'<form method="post" action="{delete_action}" style="margin:0;" '
            "onsubmit=\"return confirm('Удалить эту ветку из списка?');\">"
            '<button type="submit" title="Удалить" style="border:none;background:none;color:#b02a2a;'
            'cursor:pointer;font-size:1.15rem;line-height:1;">&times;</button></form></li>'
        )
    return (
        '<section class="filter-panel recent-panel" data-recent="branches" style="margin-top:16px;">'
        f"{_recent_panel_head('Недавние ветки')}"
        f'<ul class="recent-list" style="{_RECENT_LIST_STYLE}">{"".join(rows)}</ul>'
        "</section>"
    )


def _single_run_dir(state_key: str, run_id: str) -> Path:
    """Isolated report dir for one single-project run (holds report.json/csv/xlsx + run_meta.json)."""
    return (AUDITOR_ROOT / "ui" / state_key / "runs" / run_id).resolve()


def _persist_single_run(state_key: str, run_id: str, run_dir: Path, report: Any) -> None:
    """Record a finished single-project run so it survives a restart and lists in 'recent runs'.

    The report artifacts already live in ``run_dir`` (write_report wrote report.json/csv/xlsx);
    this only drops a light index beside them and registers it in the per-user store.
    """
    summary = report.summary.model_dump(mode="json")
    name = str(summary.get("input_path") or "").strip() or "Проверка"
    meta = {
        "run_id": run_id,
        "state_key": state_key,
        "created_at": datetime.now(UTC).isoformat(),
        "name": name,
        "report_dir": str(run_dir),
        "summary": summary,
    }
    try:
        (run_dir / _RUN_META_FILE).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # persistence is best-effort; the in-memory copy still serves this process.
    with _UI_LOCK:
        runs = _RECENT_RUNS.setdefault(state_key, [])
        runs[:] = [item for item in runs if item.get("run_id") != run_id]
        runs.insert(0, meta)
        _evict_old_single_runs(state_key)


def _evict_old_single_runs(state_key: str) -> None:
    """Cap a user's single runs; reclaim the report dir of anything evicted. Call under _UI_LOCK."""
    runs = _RECENT_RUNS.get(state_key, [])
    while len(runs) > _MAX_SINGLE_RUNS:
        old = runs.pop()
        shutil.rmtree(Path(str(old.get("report_dir", ""))), ignore_errors=True)


def _load_persisted_runs() -> None:
    """Repopulate the per-user single-run store from run_meta.json left on disk (startup).

    Trusts each meta's on-disk location over its stored ``report_dir`` (the tree may have moved),
    keeps only the newest ``_MAX_SINGLE_RUNS`` per user, and never deletes anything on reload.
    """
    root = AUDITOR_ROOT / "ui"
    if not root.exists():
        return
    by_key: dict[str, list[dict[str, Any]]] = {}
    for meta_path in root.rglob(_RUN_META_FILE):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            state_key = str(meta["state_key"])
        except (OSError, ValueError, KeyError):
            continue
        meta["report_dir"] = str(meta_path.parent)
        by_key.setdefault(state_key, []).append(meta)
    with _UI_LOCK:
        for state_key, metas in by_key.items():
            if state_key in _RECENT_RUNS:
                continue
            metas.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
            _RECENT_RUNS[state_key] = metas[:_MAX_SINGLE_RUNS]


def _find_single_run(run_id: str) -> dict[str, Any] | None:
    """Locate a persisted single-project run by id across every user's store.

    Single runs are shared like branch runs: any authenticated user can reopen or delete any run,
    so lookups scan all per-user buckets rather than just the requester's."""
    with _UI_LOCK:
        for runs in _RECENT_RUNS.values():
            for meta in runs:
                if meta.get("run_id") == run_id:
                    return meta
    return None


def _all_recent_runs_newest_first() -> list[dict[str, Any]]:
    """Every user's recent runs merged into one list, newest first (shared 'recent runs' view)."""
    with _UI_LOCK:
        merged = [meta for runs in _RECENT_RUNS.values() for meta in runs]
    merged.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
    return merged


def _render_recent_runs() -> str:
    """A compact 'recent runs' panel for the main auditor page, so a finished single audit is
    findable after a refresh/restart even without its URL. Shared across all users (like branch
    runs); empty string when there are none."""
    from html import escape

    runs = _all_recent_runs_newest_first()
    if not runs:
        return ""
    rows = []
    for meta in runs[:_MAX_RECENT_RUNS_PANEL]:
        run_id = escape(str(meta.get("run_id", "")))
        raw_name = str(meta.get("name") or "Проверка")
        name = escape(raw_name)
        created_raw = str(meta.get("created_at") or "")
        created = escape(created_raw.replace("T", " ")[:16])
        verdict, findings, severity = _project_verdict(meta.get("summary"))
        detail = escape(verdict) + (f" · {escape(severity)}" if severity else "")
        link = f"/app/auditor/run/{run_id}"
        delete_action = f"/app/auditor/run/{run_id}/delete"
        rows.append(
            f'<li class="recent-row" data-name="{escape(raw_name.lower(), quote=True)}" '
            f'data-ts="{escape(created_raw, quote=True)}" data-errors="{findings}" '
            'style="display:flex;align-items:center;justify-content:space-between;gap:12px;">'
            f'<span><a href="{link}" style="color:#0e8f6f;text-decoration:none;font-weight:600;">{name}</a>'
            f'<span style="color:#8a9691;font-size:.85rem;"> · {detail} · {created}</span></span>'
            f'<form method="post" action="{delete_action}" style="margin:0;" '
            "onsubmit=\"return confirm('Удалить эту проверку из списка?');\">"
            '<button type="submit" title="Удалить" style="border:none;background:none;color:#b02a2a;'
            'cursor:pointer;font-size:1.15rem;line-height:1;">&times;</button></form></li>'
        )
    return (
        '<section class="filter-panel recent-panel" data-recent="runs" style="margin-top:16px;">'
        f"{_recent_panel_head('Недавние проверки')}"
        f'<ul class="recent-list" style="{_RECENT_LIST_STYLE}">{"".join(rows)}</ul>'
        "</section>"
    )


def _load_single_report(report_dir: Path) -> Any | None:
    """Load a single run's AuditReport back from its on-disk report.json (for a reopened run)."""
    report_json = report_dir / "report.json"
    if not report_json.exists():
        return None
    from content_factory.audit import domain

    try:
        return domain.AuditReport.model_validate_json(report_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _load_project_report(project: Any) -> Any | None:
    """Load a project's AuditReport back from its on-disk report.json (for reloaded runs)."""
    if not project.report_dir:
        return None
    report_json = Path(project.report_dir) / "report.json"
    if not report_json.exists():
        return None
    from content_factory.audit import domain

    try:
        return domain.AuditReport.model_validate_json(report_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _project_verdict(summary: dict[str, Any] | None) -> tuple[str, int, str]:
    """(verdict label, findings count, severity breakdown) for one project's summary row."""

    if not isinstance(summary, dict):
        return ("—", 0, "")
    findings = int(summary.get("findings_total") or 0)
    raw_severity = summary.get("by_severity")
    by_severity: dict[str, Any] = raw_severity if isinstance(raw_severity, dict) else {}
    severity = ", ".join(f"{name}: {count}" for name, count in by_severity.items() if count)
    verdict = "✓ чисто" if findings == 0 else f"⚠ {findings}"
    return (verdict, findings, severity)


_ACTIVITY_CSS = {
    "активен": "active",
    "застывает": "fading",
    "архив": "archived",
    "нет данных": "nodata",
}


def _activity_cell(info: Any) -> tuple[str, str]:
    """(activity badge html, students-per-year cell html) for one project's DWH activity."""

    from html import escape

    css = _ACTIVITY_CSS.get(info.activity, "nodata")
    label = escape(info.activity)
    if info.ambiguous:
        label += " *"
    badge = f'<span class="act act-{css}">{label}</span>'
    students = f'<span class="students">{info.students_last_365d}</span>' if info.matched else '<span class="muted">—</span>'
    return badge, students


def _render_branch_activity_bar(summary: Any) -> str:
    """High-level branch actuality banner: how many projects are live / fading / archived and the
    total active students over the last year — the top-level report the head of audit asked for."""

    from content_factory.audit.activity import DWH_SNAPSHOT_DATE

    def metric(value: object, caption: str, extra: str = "") -> str:
        return f'<div class="metric"{extra}><b>{value}</b><span>{caption}</span></div>'

    return (
        '<section class="actbar">'
        + metric(summary.active, "активных")
        + metric(summary.fading, "застывают")
        + metric(summary.archived, "архив")
        + metric(summary.no_data, "нет данных")
        + '<div class="sep"></div>'
        + metric(f"{summary.students_last_365d:,}".replace(",", " "), "активных учеников за год")
        + f'<div class="src">актуальность по данным School&nbsp;21 DWH · {DWH_SNAPSHOT_DATE}</div>'
        + "</section>"
    )


def _render_branch_summary(branch_id: str, report: Any) -> str:
    """Self-contained summary page: per-project verdicts (drill-down links) + the skip list."""

    from html import escape

    from content_factory.audit import activity as _activity

    activity_by_name = {project.name: _activity.lookup(project.name) for project in report.projects}
    branch_activity = _activity.summarize_branch([project.name for project in report.projects])

    rows: list[str] = []
    for index, project in enumerate(report.projects):
        act_badge, act_students = _activity_cell(activity_by_name[project.name])
        if project.status == "failed":
            rows.append(
                f'<tr class="failed"><td>{escape(project.name)}</td><td>✗ ошибка</td><td>—</td>'
                f'<td>{act_badge}</td><td>{act_students}</td>'
                f'<td>{escape(project.error or "")}</td><td>—</td></tr>'
            )
            continue
        verdict, _findings, severity = _project_verdict(project.summary)
        link = f"/app/auditor/branch/{branch_id}/project/{index}"
        rows.append(
            f'<tr><td>{escape(project.name)}</td><td>{escape(verdict)}</td>'
            f'<td>{escape(severity) or "—"}</td><td>{act_badge}</td><td>{act_students}</td>'
            f'<td class="muted">—</td>'
            f'<td><a class="open" href="{escape(link)}">Открыть отчёт →</a></td></tr>'
        )
    skips = "".join(
        f'<li><strong>{escape(skip.name)}</strong> — {escape(skip.message)}</li>' for skip in report.skipped
    )
    skip_block = (
        f'<section class="skips"><h2>Пропущено ({len(report.skipped)})</h2><ul>{skips}</ul></section>'
        if report.skipped
        else ""
    )
    table = (
        '<table><thead><tr><th>Проект</th><th>Вердикт</th><th>Severity</th>'
        '<th>Активность</th><th>Ученики/год</th><th>Замечание</th><th></th></tr></thead><tbody>'
        + "".join(rows)
        + "</tbody></table>"
        if rows
        else '<p class="muted">В ветке не найдено ни одного проекта с README.</p>'
    )
    activity_bar = _render_branch_activity_bar(branch_activity) if report.projects else ""
    all_button = (
        f'<a class="all-branch" href="/app/auditor/branch/{branch_id}/all">▦ Аудит всей ветки</a>'
        if report.completed_count
        else ""
    )
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Аудит ветки</title><link rel="stylesheet" href="/static/css/fonts.css">
<style>
 body{{font-family:'Inter',system-ui,sans-serif;margin:0;background:#f6f8f7;color:#1c2b27;padding:32px;}}
 .wrap{{max-width:1080px;margin:0 auto;}}
 h1{{font-size:1.5rem;margin:0 0 4px;}} .sub{{color:#5b6b66;margin:0 0 24px;}}
 a.back{{color:#0e8f6f;text-decoration:none;font-size:.9rem;}}
 .head-row{{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-top:6px;}}
 a.all-branch{{display:inline-flex;align-items:center;gap:6px;padding:9px 16px;background:#10b981;color:#fff;
   border-radius:999px;text-decoration:none;font-weight:600;font-size:.9rem;white-space:nowrap;}}
 a.all-branch:hover{{background:#0e8f6f;}}
 table{{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06);}}
 th,td{{text-align:left;padding:12px 16px;border-bottom:1px solid #eef2f0;font-size:.92rem;}}
 th{{background:#f0f4f2;font-weight:600;color:#3a4a45;}}
 tr.failed td{{background:#fff6f5;}} .muted{{color:#8a9691;}}
 a.open{{color:#0e8f6f;text-decoration:none;font-weight:600;white-space:nowrap;}}
 .skips{{margin-top:24px;background:#fffaf0;border-left:3px solid #f0b429;border-radius:8px;padding:12px 18px;}}
 .skips h2{{font-size:1rem;margin:.2rem 0 .5rem;}} .skips li{{margin:.25rem 0;color:#5b5138;}}
 .actbar{{display:flex;flex-wrap:wrap;gap:20px;align-items:center;background:#fff;border-radius:10px;
   padding:14px 20px;margin:0 0 20px;box-shadow:0 1px 3px rgba(0,0,0,.06);}}
 .actbar .metric{{display:flex;flex-direction:column;line-height:1.15;}}
 .actbar .metric b{{font-size:1.35rem;color:#1c2b27;}} .actbar .metric span{{font-size:.72rem;color:#6b7a75;}}
 .actbar .sep{{width:1px;align-self:stretch;background:#eef2f0;}}
 .actbar .src{{margin-left:auto;font-size:.72rem;color:#9aa7a2;max-width:180px;text-align:right;}}
 .act{{display:inline-block;padding:2px 10px;border-radius:999px;font-size:.78rem;font-weight:600;white-space:nowrap;}}
 .act-active{{background:#dcfce7;color:#166534;}} .act-fading{{background:#fef3c7;color:#92400e;}}
 .act-archived{{background:#e5e7eb;color:#4b5563;}} .act-nodata{{background:#f1f5f9;color:#94a3b8;}}
 .students{{font-variant-numeric:tabular-nums;font-weight:600;color:#1c2b27;}}
</style></head><body><div class="wrap">
<a class="back" href="/app/auditor">← к аудитору</a>
<div class="head-row">
  <h1>Аудит ветки</h1>
  {all_button}
</div>
<p class="sub">Проверено проектов: <strong>{report.completed_count}</strong> ·
 с ошибкой: <strong>{report.failed_count}</strong> · пропущено: <strong>{len(report.skipped)}</strong></p>
{activity_bar}
{table}
{skip_block}
</div></body></html>"""


def _load_branch_projects(branch_id: str, report: Any) -> list[tuple[str, Any]]:
    """(project_name, AuditReport) for every completed project — from the in-memory store when a
    fresh run still holds it, else re-read from each project's report.json on disk (persisted runs
    after a restart keep only the summary index in memory)."""
    with _UI_LOCK:
        index_map = dict(_BRANCH_REPORTS.get(branch_id) or {})
    projects: list[tuple[str, Any]] = []
    for index, project in enumerate(report.projects):
        if project.status != "completed":
            continue
        project_report = index_map.get(index) or _load_project_report(project)
        if project_report is not None:
            projects.append((project.name, project_report))
    return projects


def _render_branch_view(branch_id: str, report: Any) -> str:
    """Default branch view: the per-project verdict summary (with a link to the consolidated view)."""
    return _render_branch_summary(branch_id, report)


def _render_branch_all(branch_id: str, report: Any) -> str:
    """Consolidated 'whole branch' dashboard: one findings table across every project, with the
    project filter plaque, criterion/severity filters, search and downloads. Loads per-project
    reports from memory or disk; falls back to the summary when none can be read."""
    projects = _load_branch_projects(branch_id, report)
    if not projects:
        return _render_branch_summary(branch_id, report)
    from content_factory.audit import web_rendering

    # Reuse the single-view link rewrite so the shared /app prefix serves the logo asset and logout
    # route (the consolidated page emits the same absolute /assets and /logout links).
    page = _rewrite_auditor_page_html(web_rendering.render_branch_dashboard(branch_id, projects, report.skipped))
    return _inject_branch_all_back_link(page, branch_id)


def _inject_branch_all_back_link(page: str, branch_id: str) -> str:
    """Add a '← к списку проектов' control to the consolidated page's top action bar."""
    back = (
        f'<a class="link-button" href="/app/auditor/branch/{branch_id}" '
        'style="background:#10b981;color:#fff;font-weight:600;">← к списку проектов</a>'
    )
    anchor = '<a class="link-button" href="#summary">Сводка</a>'
    if anchor in page:
        return page.replace(anchor, back + anchor, 1)
    return page.replace('<main class="shell">', f'<main class="shell"><div style="margin:10px 0;">{back}</div>', 1)


def _merged_branch_audit_report(branch_id: str, report: Any, only_branch: str | None = None) -> Any:
    """One AuditReport spanning every completed project of the branch, each finding tagged with its
    project name, for a consolidated CSV/XLSX export (reuses the single-report exporters).

    ``only_branch`` scopes the export to a single project (the table's active project filter);
    ``None`` keeps the whole branch. Reads per-project reports from memory or disk."""

    from datetime import UTC, datetime

    from content_factory.audit.domain import AuditReport, RunSummary

    findings: list[Any] = []
    units: list[Any] = []
    for name, project_report in _load_branch_projects(branch_id, report):
        if only_branch is not None and name != only_branch:
            continue
        for finding in getattr(project_report, "findings", []) or []:
            findings.append(finding.model_copy(update={"branch": name}))
        units.extend(getattr(project_report, "units", []) or [])
    summary = RunSummary(started_at=datetime.now(UTC), input_path=branch_id, findings_total=len(findings))
    return AuditReport(summary=summary, units=units, entities=[], findings=findings)


def _run_branch_job(
    branch_id: str,
    branch_dir: Path,
    extract_root: Path,
    out_root: Path,
    work_dir: Path,
    archive_name: str,
    *,
    use_model: bool,
    allow_network: bool,
    include_unknown: bool,
) -> None:
    """Background thread: enumerate + audit a branch, publishing live progress into _BRANCH_JOBS.

    Runs off the request so a long branch (~2 min per project) never blocks the HTTP response or
    hits the proxy timeout. Progress is polled via the status endpoint; the finished BranchReport
    lands in the same stores the summary/drill-down already read.
    """

    from content_factory.audit import branch as branch_mod

    started_at = datetime.now(UTC).isoformat()

    def set_job(**changes: Any) -> None:
        with _UI_LOCK:
            job = _BRANCH_JOBS.get(branch_id)
            if job is not None:
                job.update(changes)

    try:
        entries = branch_mod.enumerate_branch(branch_dir, extract_root)
        total = sum(1 for entry in entries if entry.is_project and entry.project_dir is not None)
        set_job(total=total)
        _flush_job_file(branch_id, work_dir, archive_name, started_at)
        reports_by_outdir: dict[str, Any] = {}

        def run_project(project_dir: Path, out_dir: Path) -> dict[str, Any]:
            report = _run_single_project_audit(
                project_dir,
                out_dir,
                use_model=use_model,
                allow_network=allow_network,
                include_unknown=include_unknown,
            )
            reports_by_outdir[str(out_dir)] = report
            summary: dict[str, Any] = report.summary.model_dump(mode="json")
            return summary

        def on_progress(done: int, total_count: int, name: str, status: str) -> None:
            with _UI_LOCK:
                job = _BRANCH_JOBS.get(branch_id)
                if job is None:
                    return
                job["completed"] = done
                job["total"] = total_count
                job["current"] = name
                if status == "failed":
                    job["failed"] = int(job.get("failed", 0)) + 1
            _flush_job_file(branch_id, work_dir, archive_name, started_at)

        branch_report = branch_mod.run_branch_audit(entries, out_root, run_project, on_progress)
        index_map = {
            index: reports_by_outdir[project.report_dir]
            for index, project in enumerate(branch_report.projects)
            if project.report_dir and project.report_dir in reports_by_outdir
        }
        created_at = datetime.now(UTC).isoformat()
        with _UI_LOCK:
            _BRANCH_RUNS[branch_id] = branch_report
            _BRANCH_REPORTS[branch_id] = index_map
            _BRANCH_WORK_DIRS[branch_id] = work_dir
            _BRANCH_CREATED[branch_id] = {"created_at": created_at, "archive_name": archive_name}
            job = _BRANCH_JOBS.get(branch_id)
            if job is not None:
                job["state"] = "done"
            _evict_old_branch_runs()
        _persist_branch(branch_id, branch_report, work_dir, archive_name, created_at)
        _prune_branch_workdir(work_dir, out_root)  # keep only reports + meta on disk
    except Exception as exc:  # noqa: BLE001 - report the branch-level failure to the poller.
        shutil.rmtree(work_dir, ignore_errors=True)
        set_job(state="error", error=str(exc))


def _render_branch_gone() -> str:
    """Shown when a progress URL points at a run the server no longer knows (usually a restart)."""
    return (
        '<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        '<title>Ветка недоступна</title><link rel="stylesheet" href="/static/css/fonts.css">'
        "<style>body{font-family:'Inter',system-ui,sans-serif;margin:0;background:#f6f8f7;color:#1c2b27;padding:32px;}"
        ".wrap{max-width:640px;margin:0 auto;}h1{font-size:1.4rem;margin:0 0 8px;}"
        ".card{background:#fff;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.06);padding:24px;}"
        "a.back{display:inline-block;margin-top:14px;color:#0e8f6f;text-decoration:none;font-weight:600;}</style>"
        '</head><body><div class="wrap"><div class="card"><h1>Прогон не найден или прерван</h1>'
        "<p>Скорее всего, сервер перезапускался во время проверки. Запустите проверку ветки заново.</p>"
        '<a class="back" href="/app/auditor">← Вернуться к аудитору</a></div></div></body></html>'
    )


def _render_branch_processing(branch_id: str) -> str:
    """Self-contained processing page: a determinate 'X of N' bar that polls the status endpoint
    and redirects to the summary once the background job finishes."""

    status_url = f"/app/auditor/branch/{branch_id}/status"
    summary_url = f"/app/auditor/branch/{branch_id}"
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Аудит ветки — обработка</title><link rel="stylesheet" href="/static/css/fonts.css">
<style>
 body{{font-family:'Inter',system-ui,sans-serif;margin:0;background:#f6f8f7;color:#1c2b27;padding:32px;}}
 .wrap{{max-width:720px;margin:0 auto;}}
 h1{{font-size:1.5rem;margin:0 0 4px;}} .sub{{color:#5b6b66;margin:0 0 24px;}}
 a.back{{color:#0e8f6f;text-decoration:none;font-size:.9rem;}}
 .card{{background:#fff;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.06);padding:24px;}}
 .bar-track{{height:12px;border-radius:999px;background:#d1fae5;overflow:hidden;margin:14px 0 10px;}}
 .bar-fill{{height:100%;width:0;border-radius:999px;background:#10b981;transition:width .4s ease;}}
 .bar-label{{font-size:.95rem;color:#3a4a45;font-weight:600;}}
 .bar-note{{font-size:.82rem;color:#8a9691;margin-top:6px;}}
 .err{{color:#b02a2a;font-weight:600;}}
</style></head><body><div class="wrap">
<a class="back" href="/app/auditor">← к аудитору</a>
<h1>Обработка ветки</h1>
<p class="sub">Полный аудит: ≈ 2 минуты на проект. Можно не закрывать вкладку — прогресс обновляется сам.</p>
<div class="card">
  <div class="bar-label" id="bar-label">Подготовка…</div>
  <div class="bar-track"><div class="bar-fill" id="bar-fill"></div></div>
  <div class="bar-note" id="bar-note">Идёт распаковка и перечисление проектов…</div>
</div>
</div>
<script>
(function(){{
  var statusUrl={status_url!r}, summaryUrl={summary_url!r};
  var fill=document.getElementById('bar-fill');
  var label=document.getElementById('bar-label');
  var note=document.getElementById('bar-note');
  function terminal(text, detail){{
    label.className='bar-label err';
    label.textContent=text;
    note.textContent=detail||'';
  }}
  function tick(){{
    fetch(statusUrl,{{headers:{{'X-Requested-With':'fetch'}}}}).then(function(r){{
      if(r.status===404){{ terminal('Прогон не найден или прерван', 'Возможно, сервер перезапускался. Запустите проверку заново.'); return null; }}
      if(!r.ok) throw new Error('status '+r.status);
      return r.json();
    }}).then(function(j){{
      if(!j) return;  // stopped on 404
      if(j.state==='error'){{ terminal('Ошибка обработки ветки', j.error||'не удалось обработать ветку'); return; }}
      if(j.state==='interrupted'){{ terminal('Прогон прерван', 'Сервер перезапускался во время проверки. Запустите её заново.'); return; }}
      var total=j.total||0, done=j.completed||0, failed=j.failed||0;
      var pct=total?Math.round(done/total*100):0;
      fill.style.width=pct+'%';
      label.textContent=total?('Готово '+done+' из '+total+' проектов'):'Подготовка…';
      note.textContent=(failed?('с ошибкой: '+failed+' · '):'')+(j.current?('сейчас: '+j.current):'перечисление проектов…');
      if(j.done){{ window.location.href=summaryUrl; return; }}
      setTimeout(tick,2000);
    }}).catch(function(){{ setTimeout(tick,3000); }});
  }}
  tick();
}})();
</script>
</body></html>"""


@page_router.post("/branch/upload")
async def upload_auditor_branch(
    request: Request,
    branch_archive: UploadFile | None = File(None),
) -> Response:
    """Receive the branch archive the moment it is chosen (so the browser can show upload %),
    stash it, and return an upload_id the run request references. The audit starts later."""

    user = await validate_request_user(request)
    state_key, _state = _auditor_page_state(user)
    if branch_archive is None or not branch_archive.filename:
        raise HTTPException(status_code=400, detail="Не выбран архив ветки")

    work_dir = _new_ui_work_dir(state_key, "branch")
    archive_name = Path(branch_archive.filename).name or "branch_archive"
    archive_path = work_dir / _safe_upload_name(archive_name)
    await _store_upload(branch_archive, archive_path)
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=413, detail="Архив ветки слишком большой")

    upload_id = uuid.uuid4().hex
    with _UI_LOCK:
        _BRANCH_UPLOADS[upload_id] = {
            "work_dir": work_dir,
            "archive_path": archive_path,
            "archive_name": archive_name,
        }
        _evict_old_branch_uploads()
    return JSONResponse({"upload_id": upload_id, "archive_name": archive_name})


@page_router.post("/branch")
async def run_auditor_branch(
    request: Request,
    upload_id: str = Form(""),
    use_model: bool = Form(True),
    allow_network: bool = Form(True),
    include_unknown: bool = Form(True),
) -> Response:
    """Start a branch audit from a previously uploaded archive; return the live-progress URL.

    Always a full audit — the model (LLM: facts, market, accuracy) and network link checks
    match the single-project run. The heavy work runs off-request (one project ≈ 2 min), so a
    large branch never blocks the response or trips the proxy timeout; the page polls for progress.
    """

    await validate_request_user(request)
    with _UI_LOCK:
        upload = _BRANCH_UPLOADS.pop(upload_id, None)
    if upload is None:
        raise HTTPException(status_code=400, detail="Архив ветки не найден — загрузите его заново")

    work_dir = upload["work_dir"]
    archive_path = upload["archive_path"]
    archive_name = upload["archive_name"]
    branch_dir = work_dir / "branch"
    extract_root = work_dir / "extract"
    out_root = work_dir / "reports"
    try:
        _extract_archive(archive_path, branch_dir)
    except HTTPException:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    except Exception as exc:  # noqa: BLE001 - surface outer-archive failures to the UI.
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=f"Не удалось распаковать архив ветки: {exc}") from exc

    branch_id = uuid.uuid4().hex
    with _UI_LOCK:
        _BRANCH_JOBS[branch_id] = {
            "state": "running",
            "total": 0,
            "completed": 0,
            "failed": 0,
            "current": "",
            "error": None,
        }
    Thread(
        target=_run_branch_job,
        args=(branch_id, branch_dir, extract_root, out_root, work_dir, archive_name),
        kwargs={"use_model": use_model, "allow_network": allow_network, "include_unknown": include_unknown},
        daemon=True,
    ).start()
    # Return the progress URL (not the page) so the client navigates to a stable GET route —
    # refreshing it re-renders the live progress instead of re-POSTing (which would start a new run).
    return JSONResponse({"progress_url": f"/app/auditor/branch/{branch_id}/progress"})


@page_router.get("/branch/{branch_id}/progress")
async def get_auditor_branch_progress(request: Request, branch_id: str) -> Response:
    """Stable, refresh-safe progress page for an in-flight branch job; redirects when finished."""

    await validate_request_user(request)
    _ensure_branches_loaded()
    with _UI_LOCK:
        job = _BRANCH_JOBS.get(branch_id)
        finished = branch_id in _BRANCH_RUNS or (job is not None and job.get("state") == "done")
        exists = job is not None or branch_id in _BRANCH_RUNS
    if finished:
        return RedirectResponse(f"/app/auditor/branch/{branch_id}", status_code=303)
    if not exists:
        # Unknown/expired (usually the server was restarted mid-run): show a real page, not raw JSON.
        return HTMLResponse(_render_branch_gone(), status_code=200, headers={"Cache-Control": "no-store"})
    return HTMLResponse(_render_branch_processing(branch_id), headers={"Cache-Control": "no-store"})


@page_router.get("/branch/{branch_id}")
async def get_auditor_branch_summary(request: Request, branch_id: str) -> HTMLResponse:
    """Re-render the summary of a completed branch run."""

    await validate_request_user(request)
    _ensure_branches_loaded()
    with _UI_LOCK:
        report = _BRANCH_RUNS.get(branch_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Ветка не найдена или устарела")
    return HTMLResponse(_render_branch_view(branch_id, report), headers={"Cache-Control": "no-store"})


@page_router.get("/branch/{branch_id}/all")
async def get_auditor_branch_all(request: Request, branch_id: str) -> HTMLResponse:
    """Consolidated 'whole branch' dashboard — one findings table across every project of the branch
    with the per-project filter plaque (reads reports from memory or disk)."""

    await validate_request_user(request)
    _ensure_branches_loaded()
    with _UI_LOCK:
        report = _BRANCH_RUNS.get(branch_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Ветка не найдена или устарела")
    return HTMLResponse(_render_branch_all(branch_id, report), headers={"Cache-Control": "no-store"})


_BRANCH_DOWNLOADS: dict[str, str] = {
    "report.csv": "text/csv; charset=utf-8",
    "report.xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@page_router.get("/branch/{branch_id}/download")
async def download_auditor_branch_report(
    request: Request,
    branch_id: str,
    file_name: str = Query(alias="file"),
    branch: str | None = Query(None),
) -> Response:
    """Consolidated CSV/XLSX across the branch, or scoped to one project via the ``branch`` filter.

    ``branch`` mirrors the table's active project chip: absent → whole branch (all projects),
    a project name → only that project's findings. Always carries the 'Ветка' column."""

    await validate_request_user(request)
    _ensure_branches_loaded()
    media_type = _BRANCH_DOWNLOADS.get(file_name)
    if media_type is None:
        raise HTTPException(status_code=400, detail="Недопустимый файл отчёта")
    with _UI_LOCK:
        report = _BRANCH_RUNS.get(branch_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Ветка не найдена или устарела")
    only_branch = branch if branch and branch != "all" else None

    from content_factory.audit import exporters

    merged = exporters._without_pass_findings(_merged_branch_audit_report(branch_id, report, only_branch))
    scope = _branch_download_scope(only_branch)
    with tempfile.TemporaryDirectory(prefix="branch_report_") as tmp_dir:
        path = Path(tmp_dir) / file_name
        if file_name == "report.csv":
            exporters._write_csv(merged, path)
        else:
            exporters._write_xlsx(merged, path)
        payload = path.read_bytes()
    return Response(
        content=payload,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{scope}_{file_name}"',
            "Cache-Control": "no-store",
        },
    )


def _branch_download_scope(only_branch: str | None) -> str:
    """A short filesystem-safe stem for the download filename reflecting the export scope."""

    if only_branch is None:
        return "branch_all"
    stem = os.path.splitext(only_branch)[0]
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._-")
    return safe[:48] or "project"


@page_router.post("/branch/{branch_id}/delete")
async def delete_auditor_branch(request: Request, branch_id: str) -> Response:
    """Remove one branch run (from the recent list) and reclaim its artifacts."""

    await validate_request_user(request)
    _ensure_branches_loaded()
    _delete_branch(branch_id)
    return RedirectResponse("/app/auditor", status_code=303)


@page_router.get("/branch/{branch_id}/status")
async def get_auditor_branch_status(request: Request, branch_id: str) -> JSONResponse:
    """Live progress of a background branch job, polled by the processing page."""

    await validate_request_user(request)
    _ensure_branches_loaded()
    with _UI_LOCK:
        job = _BRANCH_JOBS.get(branch_id)
        snapshot = dict(job) if job is not None else None
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Задача ветки не найдена или устарела")
    state = str(snapshot.get("state", "running"))
    return JSONResponse(
        {
            "state": state,
            "total": snapshot.get("total", 0),
            "completed": snapshot.get("completed", 0),
            "failed": snapshot.get("failed", 0),
            "current": snapshot.get("current", ""),
            "error": snapshot.get("error"),
            "done": state == "done",
            "summary_url": f"/app/auditor/branch/{branch_id}",
        }
    )


@page_router.get("/branch/{branch_id}/project/{index}")
async def get_auditor_branch_project(request: Request, branch_id: str, index: int) -> HTMLResponse:
    """Full single-project report for one project inside the branch (drill-down)."""

    user = await validate_request_user(request)
    _ensure_branches_loaded()
    _state_key, state = _auditor_page_state(user)
    with _UI_LOCK:
        branch_report = _BRANCH_RUNS.get(branch_id)
        report_obj = _BRANCH_REPORTS.get(branch_id, {}).get(index)
    if branch_report is None or index < 0 or index >= len(branch_report.projects):
        raise HTTPException(status_code=404, detail="Отчёт проекта не найден")
    project = branch_report.projects[index]
    if report_obj is None:
        # Reloaded run (after restart): the in-memory report is gone but its report.json remains.
        report_obj = _load_project_report(project)
        if report_obj is None:
            raise HTTPException(status_code=404, detail="Отчёт проекта не найден")
        with _UI_LOCK:
            _BRANCH_REPORTS.setdefault(branch_id, {})[index] = report_obj
    if project.report_dir:
        state.report_dir = Path(project.report_dir)
    web_app = _load_auditor_web_app()
    html = web_app.render_page(report_obj, state)
    # A "back to branch" pill placed in the report's top action bar next to Перезапустить/Изменить.
    # stopPropagation keeps a click from toggling the <details> the bar lives in. Fall back to the
    # results heading, then the page top, for reports without that bar.
    back = (
        f'<a href="/app/auditor/branch/{branch_id}" onclick="event.stopPropagation()" '
        'style="display:inline-flex;align-items:center;margin-right:10px;padding:8px 14px;'
        "background:#10b981;color:#fff;border-radius:999px;text-decoration:none;font-weight:600;"
        'font-size:13px;">← Вернуться к ветке</a>'
    )
    page = _rewrite_auditor_page_html(html)
    actions_anchor = '<span class="run-bar-actions">'
    results_heading = "<h2>Таблица результата</h2>"
    if actions_anchor in page:
        page = page.replace(actions_anchor, actions_anchor + back, 1)
    elif results_heading in page:
        page = page.replace(results_heading, results_heading + back, 1)
    else:
        page = f'<div style="max-width:1080px;margin:12px auto;padding:0 16px;">{back}</div>' + page
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


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
        from content_factory.audit import domain, env, exporters, orchestrator

        env_values = env.load_env_file(WORKSPACE_ROOT / ".env")
        settings = domain.AuditSettings(
            input_path=input_dir,
            output_path=output_dir,
            allow_network=allow_network,
            use_model=use_model,
            include_unknown=include_unknown,
            openrouter_api_key=env.get_env_value(("POLZA_AI_API_KEY", "OPENROUTER_API_KEY", "OPEN_ROUTER_API_KEY"), env_values),
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
            for member in archive.infolist():
                _extract_zip_member(archive, member, target_dir)
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
    if suffixes.endswith(".rar"):
        # RAR needs an external tool (unar); reuse the auditor's path-traversal-safe extractor.
        from content_factory.audit.web_app import _extract_rar_archive

        try:
            _extract_rar_archive(archive_path, target_dir)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return
    raise HTTPException(status_code=400, detail="Поддерживаются ZIP, TAR, TAR.GZ и RAR архивы")


_MAX_PATH_COMPONENT_BYTES = 255  # ext4/most filesystems cap a single name at 255 bytes


def _truncate_component(component: str) -> str:
    """Shorten one path component to the filesystem byte limit, keeping any extension and a short
    content hash so two distinct long names never collide after truncation."""
    if len(component.encode("utf-8")) <= _MAX_PATH_COMPONENT_BYTES:
        return component
    stem, dot, ext = component.rpartition(".")
    if not dot:
        stem, ext = component, ""
    else:
        ext = "." + ext.encode("utf-8")[:32].decode("utf-8", "ignore")
    digest = hashlib.sha1(component.encode("utf-8")).hexdigest()[:8]
    budget = _MAX_PATH_COMPONENT_BYTES - len(ext.encode("utf-8")) - 9  # "_" + 8 hex chars
    stem = stem.encode("utf-8")[: max(1, budget)].decode("utf-8", "ignore")
    return f"{stem}_{digest}{ext}"


def _sanitize_member_path(member_name: str) -> str:
    """Map an archive member path to a relative path whose components fit the filesystem limit."""
    parts = [part for part in member_name.replace("\\", "/").split("/") if part not in ("", ".")]
    return "/".join(_truncate_component(part) for part in parts)


def _extract_zip_member(archive: zipfile.ZipFile, member: zipfile.ZipInfo, target_dir: Path) -> None:
    """Extract one zip member, truncating any over-long component so a single unreadable-name file
    (e.g. a long Cyrillic materials doc) cannot abort the whole branch with ENAMETOOLONG."""
    rel = _sanitize_member_path(member.filename)
    if not rel:
        return
    dest = target_dir / rel
    if member.is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(member) as source, dest.open("wb") as out:
        shutil.copyfileobj(source, out)


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
