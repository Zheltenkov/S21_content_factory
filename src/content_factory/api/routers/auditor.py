"""FastAPI adapter for the content auditor project."""

from __future__ import annotations

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
_MAX_BRANCH_RUNS = 10


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
.intake-card .button{align-self:flex-start;margin-top:auto}
.intake-card input[type=text]{width:100%}
.branch-card{border-color:#34d399;background:#f0fdf9}
.branch-progress{margin-top:10px}
.branch-progress-label{font-size:12px;color:#0e8f6f;margin-bottom:6px}
.branch-progress-track{height:6px;border-radius:999px;background:#d1fae5;overflow:hidden}
.branch-progress-track>i{display:block;height:100%;width:40%;border-radius:999px;background:#10b981;animation:branchslide 1.1s ease-in-out infinite}
@keyframes branchslide{0%{margin-left:-42%}100%{margin-left:100%}}
#run-progress{grid-column:1 / -1}
@media (max-width:860px){.intake-grid{grid-template-columns:1fr}}
</style>
"""

_BRANCH_FORM_HTML = """
    <form method="post" action="/app/auditor/branch" enctype="multipart/form-data" class="intake-card branch-card branch-run-form">
      <div class="intake-card-title">Ветка</div>
      <label class="upload-zone" for="branch_archive_input">
        <input id="branch_archive_input" name="branch_archive" type="file" accept=".zip,.tar,.gz,.tgz">
        <span class="upload-title">Загрузить ветку</span>
        <span class="upload-name">Архив архивов/папок проектов (в каждом — README)</span>
      </label>
      <button class="button" type="submit">Проверить ветку</button>
      <div class="branch-progress" hidden aria-live="polite">
        <div class="branch-progress-label">Обработка ветки… полный аудит ≈ 2 минуты на проект, не закрывайте вкладку</div>
        <div class="branch-progress-track"><i></i></div>
      </div>
    </form>
    <script>
      (function () {
        var form = document.querySelector(".branch-run-form");
        if (!form || form.dataset.progressBound) return;
        form.dataset.progressBound = "1";
        form.addEventListener("submit", function () {
          var progress = form.querySelector(".branch-progress");
          if (progress) progress.hidden = false;
          var button = form.querySelector("button[type=submit]");
          if (button) { button.disabled = true; button.textContent = "Обрабатываю ветку…"; }
        });
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


def _run_branch_from_dir(
    branch_dir: Path,
    extract_root: Path,
    out_root: Path,
    *,
    use_model: bool,
    allow_network: bool,
    include_unknown: bool,
) -> tuple[Any, dict[str, Any]]:
    """Enumerate + audit every project in an extracted branch. Returns (BranchReport, out_dir->report)."""

    from content_factory.audit import branch as branch_mod

    entries = branch_mod.enumerate_branch(branch_dir, extract_root)
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

    branch_report = branch_mod.run_branch_audit(entries, out_root, run_project)
    return branch_report, reports_by_outdir


def _evict_old_branch_runs() -> None:
    """Cap process-local branch runs; reclaim the work dir of anything evicted. Call under _UI_LOCK."""

    while len(_BRANCH_RUNS) > _MAX_BRANCH_RUNS:
        oldest = next(iter(_BRANCH_RUNS))
        _BRANCH_RUNS.pop(oldest, None)
        _BRANCH_REPORTS.pop(oldest, None)
        work_dir = _BRANCH_WORK_DIRS.pop(oldest, None)
        if work_dir is not None:
            shutil.rmtree(work_dir, ignore_errors=True)


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


def _render_branch_summary(branch_id: str, report: Any) -> str:
    """Self-contained summary page: per-project verdicts (drill-down links) + the skip list."""

    from html import escape

    rows: list[str] = []
    for index, project in enumerate(report.projects):
        if project.status == "failed":
            rows.append(
                f'<tr class="failed"><td>{escape(project.name)}</td><td>✗ ошибка</td><td>—</td>'
                f'<td>{escape(project.error or "")}</td><td>—</td></tr>'
            )
            continue
        verdict, _findings, severity = _project_verdict(project.summary)
        link = f"/app/auditor/branch/{branch_id}/project/{index}"
        rows.append(
            f'<tr><td>{escape(project.name)}</td><td>{escape(verdict)}</td>'
            f'<td>{escape(severity) or "—"}</td><td class="muted">—</td>'
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
        '<th>Замечание</th><th></th></tr></thead><tbody>' + "".join(rows) + "</tbody></table>"
        if rows
        else '<p class="muted">В ветке не найдено ни одного проекта с README.</p>'
    )
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Аудит ветки</title><link rel="stylesheet" href="/static/css/fonts.css">
<style>
 body{{font-family:'Inter',system-ui,sans-serif;margin:0;background:#f6f8f7;color:#1c2b27;padding:32px;}}
 .wrap{{max-width:1080px;margin:0 auto;}}
 h1{{font-size:1.5rem;margin:0 0 4px;}} .sub{{color:#5b6b66;margin:0 0 24px;}}
 a.back{{color:#0e8f6f;text-decoration:none;font-size:.9rem;}}
 table{{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06);}}
 th,td{{text-align:left;padding:12px 16px;border-bottom:1px solid #eef2f0;font-size:.92rem;}}
 th{{background:#f0f4f2;font-weight:600;color:#3a4a45;}}
 tr.failed td{{background:#fff6f5;}} .muted{{color:#8a9691;}}
 a.open{{color:#0e8f6f;text-decoration:none;font-weight:600;white-space:nowrap;}}
 .skips{{margin-top:24px;background:#fffaf0;border-left:3px solid #f0b429;border-radius:8px;padding:12px 18px;}}
 .skips h2{{font-size:1rem;margin:.2rem 0 .5rem;}} .skips li{{margin:.25rem 0;color:#5b5138;}}
</style></head><body><div class="wrap">
<a class="back" href="/app/auditor">← к аудитору</a>
<h1>Аудит ветки</h1>
<p class="sub">Проверено проектов: <strong>{report.completed_count}</strong> ·
 с ошибкой: <strong>{report.failed_count}</strong> · пропущено: <strong>{len(report.skipped)}</strong></p>
{table}
{skip_block}
</div></body></html>"""


@page_router.post("/branch")
async def run_auditor_branch(
    request: Request,
    branch_archive: UploadFile | None = File(None),
    use_model: bool = Form(True),
    allow_network: bool = Form(True),
    include_unknown: bool = Form(True),
) -> HTMLResponse:
    """Audit a branch (archive of project archives/folders) and render the summary.

    Always a full audit — the model (LLM: facts, market, accuracy) and network link checks
    match the single-project run; there is no shallow mode.
    """

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

    branch_dir = work_dir / "branch"
    extract_root = work_dir / "extract"
    out_root = work_dir / "reports"
    try:
        _extract_archive(archive_path, branch_dir)
        branch_report, reports_by_outdir = await run_in_threadpool(
            _run_branch_from_dir,
            branch_dir,
            extract_root,
            out_root,
            use_model=use_model,
            allow_network=allow_network,
            include_unknown=include_unknown,
        )
    except HTTPException:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    except Exception as exc:  # noqa: BLE001 - surface branch-level failures to the UI.
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=f"Не удалось обработать ветку: {exc}") from exc

    branch_id = uuid.uuid4().hex
    index_map = {
        index: reports_by_outdir[project.report_dir]
        for index, project in enumerate(branch_report.projects)
        if project.report_dir and project.report_dir in reports_by_outdir
    }
    with _UI_LOCK:
        _BRANCH_RUNS[branch_id] = branch_report
        _BRANCH_REPORTS[branch_id] = index_map
        _BRANCH_WORK_DIRS[branch_id] = work_dir
        _evict_old_branch_runs()
    return HTMLResponse(_render_branch_summary(branch_id, branch_report), headers={"Cache-Control": "no-store"})


@page_router.get("/branch/{branch_id}")
async def get_auditor_branch_summary(request: Request, branch_id: str) -> HTMLResponse:
    """Re-render the summary of a completed branch run."""

    await validate_request_user(request)
    with _UI_LOCK:
        report = _BRANCH_RUNS.get(branch_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Ветка не найдена или устарела")
    return HTMLResponse(_render_branch_summary(branch_id, report), headers={"Cache-Control": "no-store"})


@page_router.get("/branch/{branch_id}/project/{index}")
async def get_auditor_branch_project(request: Request, branch_id: str, index: int) -> HTMLResponse:
    """Full single-project report for one project inside the branch (drill-down)."""

    user = await validate_request_user(request)
    _state_key, state = _auditor_page_state(user)
    with _UI_LOCK:
        report_obj = _BRANCH_REPORTS.get(branch_id, {}).get(index)
        branch_report = _BRANCH_RUNS.get(branch_id)
        if report_obj is None or branch_report is None:
            raise HTTPException(status_code=404, detail="Отчёт проекта не найден")
        # Point the per-user state at this project's report dir so downloads resolve to it.
        project = branch_report.projects[index]
        if project.report_dir:
            state.report_dir = Path(project.report_dir)
    web_app = _load_auditor_web_app()
    html = web_app.render_page(report_obj, state)
    # A visible "back to branch" pill placed inside the report body (under the results heading),
    # not floated above the topbar where it is easy to miss. Fall back to the top for reports that
    # have no results table (e.g. an empty project).
    back = (
        f'<a href="/app/auditor/branch/{branch_id}" style="display:inline-block;'
        "margin:4px 0 14px;padding:8px 14px;background:#10b981;color:#fff;border-radius:999px;"
        'text-decoration:none;font-weight:600;font-size:13px;">← к ветке</a>'
    )
    page = _rewrite_auditor_page_html(html)
    results_heading = "<h2>Таблица результата</h2>"
    if results_heading in page:
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
