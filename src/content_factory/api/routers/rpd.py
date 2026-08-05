"""РПД из программы — временный самодостаточный режим.

Методист загружает утверждённую ДПО-программу (DOCX) → инструмент переносит содержание в РПД по
каркасу-шаблону и отдаёт готовый DOCX; отсутствующие разделы помечены пробелом-вопросом. Модуль
изолирован (свой роутер, своё хранилище под ``.tmp/rpd``), чтобы чисто ретайрить при переходе на
генерацию УП. Бизнес-логика — в пакете :mod:`content_factory.rpd`; здесь только web-обвязка.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from content_factory.api.integrations.auth_cookie import validate_request_user
from content_factory.api.integrations.project_paths import GENERATOR_ROOT
from content_factory.rpd import RpdBuild, UpPlan, build_rpd_sections, parse_up_csv, render_build
from content_factory.rpd.translate import russify_up

page_router = APIRouter(prefix="/app/rpd", include_in_schema=False, tags=["rpd-ui"])

RPD_ROOT = (GENERATOR_ROOT / ".tmp" / "rpd").resolve()
MAX_DOCX_BYTES = int(os.getenv("RPD_MAX_DOCX_BYTES", "50000000"))
OUTPUT_FILENAME = "РПД.docx"
BUILD_FILE = "build.json"
_META_FILE = "rpd_meta.json"
_MAX_RUNS = 12
_MAX_UPLOADS = 10

_LOCK = Lock()
# Загруженные, но ещё не собранные программы: upload_id -> {work_dir, path, name}.
_UPLOADS: dict[str, dict[str, Any]] = {}
# Готовые прогоны на пользователя: state_key -> [meta, ...] (новые первыми).
_RUNS: dict[str, list[dict[str, Any]]] = {}
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
    try:
        meta = await run_in_threadpool(_build_run, pending["path"], run_dir, program_title)
    finally:
        shutil.rmtree(pending["work_dir"], ignore_errors=True)

    meta.update({"run_id": run_id, "state_key": state_key, "run_dir": str(run_dir),
                 "created_at": datetime.now(UTC).isoformat(), "name": program_title})
    _persist_run(state_key, meta, run_dir)
    return JSONResponse({"run_id": run_id, "redirect": f"/app/rpd/{run_id}"})


def _build_run(csv_path: Path, run_dir: Path, program_title: str) -> dict[str, Any]:
    """Тяжёлая часть (вне event loop): разбор УП-CSV → генерация прозы (LLM) → рендер РПД на диск."""
    up = parse_up_csv(csv_path, title=program_title)
    client = _llm_client()
    up = russify_up(up, client)  # англоязычные названия тем/модулей → русский (термины оставляем)
    build = build_rpd_sections(up, None, client)
    (run_dir / BUILD_FILE).write_text(build.model_dump_json(), encoding="utf-8")  # для редактора (HITL)
    (run_dir / OUTPUT_FILENAME).write_bytes(render_build(build))
    return _up_meta(up, prose_generated=client is not None)


def _llm_client() -> Any:
    """Клиент LLM для генерации прозы; None при недоступности → рендер подставит плейсхолдеры."""
    try:
        from content_factory.platform.llm.factory import create_llm_client

        return create_llm_client(default_role="default", enable_cache=True)
    except Exception:  # noqa: BLE001 - без LLM просто рендерим с плейсхолдерами
        return None


def _load_build(meta: dict[str, Any]) -> RpdBuild | None:
    path = Path(str(meta.get("run_dir", ""))) / BUILD_FILE
    if not path.exists():
        return None
    try:
        return RpdBuild.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save_build(meta: dict[str, Any], build: RpdBuild) -> None:
    (Path(str(meta["run_dir"])) / BUILD_FILE).write_text(build.model_dump_json(), encoding="utf-8")


@page_router.get("/{run_id}")
async def rpd_view(request: Request, run_id: str, s: int = 0) -> HTMLResponse:
    user = await validate_request_user(request)
    _ensure_loaded()
    state_key = _user_key(user)
    meta = _find_run(state_key, run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Прогон РПД не найден")
    build = _load_build(meta)
    if build is None or not build.sections:  # старый прогон без черновика — итог + скачать
        return HTMLResponse(_render_screen(state_key, active=meta), headers={"Cache-Control": "no-store"})
    return HTMLResponse(_render_editor(run_id, build, meta, s), headers={"Cache-Control": "no-store"})


@page_router.post("/{run_id}/section/{index}")
async def rpd_save_section(request: Request, run_id: str, index: int) -> RedirectResponse:
    user = await validate_request_user(request)
    _ensure_loaded()
    state_key = _user_key(user)
    meta = _find_run(state_key, run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Прогон РПД не найден")
    build = _load_build(meta)
    if build is not None and 0 <= index < len(build.sections):
        section = build.sections[index]
        form = await request.form()
        if section.kind == "table":
            rows = _rows_from_form(form)
            if rows:  # пустая форма (не та секция) не должна стирать таблицу
                section.table_rows = rows
        else:
            text = str(form.get("text", ""))
            section.paragraphs = [line.strip() for line in text.splitlines() if line.strip()] or [""]
        _save_build(meta, build)
    return RedirectResponse(f"/app/rpd/{run_id}?s={index}", status_code=303)


def _rows_from_form(form: Any) -> list[list[str]]:
    rows: list[list[str]] = []
    row_index = 0
    while f"cell_{row_index}_0" in form:
        row: list[str] = []
        col_index = 0
        while f"cell_{row_index}_{col_index}" in form:
            row.append(str(form.get(f"cell_{row_index}_{col_index}", "")))
            col_index += 1
        rows.append(row)
        row_index += 1
    return rows


@page_router.get("/{run_id}/download")
async def rpd_download(request: Request, run_id: str) -> Response:
    user = await validate_request_user(request)
    _ensure_loaded()
    state_key = _user_key(user)
    meta = _find_run(state_key, run_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Прогон РПД не найден")
    build = _load_build(meta)
    if build is not None:  # рендерим из (отредактированного) черновика — свежий DOCX с правками
        data = await run_in_threadpool(render_build, build)
    else:
        path = (Path(str(meta.get("run_dir", ""))) / OUTPUT_FILENAME).resolve()
        if not path.exists():
            raise HTTPException(status_code=404, detail="Файл РПД не найден")
        data = path.read_bytes()
    download = f"{Path(str(meta.get('name', 'РПД'))).stem}_РПД.docx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(download)}"},
    )


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


_LABEL_CLASS = {"из УП": "up", "сгенерировано": "gen", "с платформы": "gap", "методист": "gap"}


def _label_chip(label: str) -> str:
    if not label:
        return ""
    return f'<span class="cs {_LABEL_CLASS.get(label, "form")}">{escape(label)}</span>'


def _render_table_editor(section: Any) -> str:
    ths = "".join(f"<th>{escape(str(h))}</th>" for h in section.table_header)
    body = []
    for row_index, row in enumerate(section.table_rows):
        cells = "".join(
            f'<td><input name="cell_{row_index}_{col_index}" value="{escape(str(cell))}"></td>'
            for col_index, cell in enumerate(row)
        )
        body.append(f"<tr>{cells}</tr>")
    return (f'<div class="tblwrap"><table class="edit"><thead><tr>{ths}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def _render_editor(run_id: str, build: RpdBuild, meta: dict[str, Any], active: int) -> str:
    sections = build.sections
    total = len(sections)
    active = max(0, min(active, total - 1))
    section = sections[active]

    nav = "".join(
        '<a class="navitem{on}" href="/app/rpd/{rid}?s={i}">'
        '<span class="nnum">{num}</span><span class="ntitle">{title}</span>{chip}</a>'.format(
            on=" on" if i == active else "",
            rid=run_id,
            i=i,
            num=escape(sec.number),
            title=escape(sec.title),
            chip=_label_chip(sec.label),
        )
        for i, sec in enumerate(sections)
    )

    if section.kind == "table":
        editor = _render_table_editor(section)
        hint = "Правьте ячейки таблицы. Пустые оставляйте для методиста."
    else:
        editor = f'<textarea name="text" class="prosein" rows="14">{escape(chr(10).join(section.paragraphs))}</textarea>'
        hint = "Один абзац — одна строка. Пустые строки игнорируются."

    prev_btn = (f'<a class="navbtn" href="/app/rpd/{run_id}?s={active - 1}">← Назад</a>'
                if active > 0 else '<span class="navbtn off">← Назад</span>')
    next_btn = (f'<a class="navbtn" href="/app/rpd/{run_id}?s={active + 1}">Далее →</a>'
                if active < total - 1 else '<span class="navbtn off">Далее →</span>')
    name = escape(str(meta.get("name") or "РПД"))

    return _EDITOR_TEMPLATE.format(
        run_id=run_id,
        name=name,
        nav=nav,
        pos=active + 1,
        total=total,
        number=escape(section.number),
        title=escape(section.title),
        chip=_label_chip(section.label),
        hint=escape(hint),
        index=active,
        editor=editor,
        prev_btn=prev_btn,
        next_btn=next_btn,
    )


def _render_screen(state_key: str, active: dict[str, Any] | None) -> str:
    with _LOCK:
        recent = list(_RUNS.get(state_key, []))
    return _SCREEN_TEMPLATE.format(
        result=_render_result(active),
        recent=_render_recent(recent, active),
    )


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
    go.disabled=true;go.textContent='Собираю РПД (генерю прозу)…';
    var titleEl=document.getElementById('rpd-title');
    var body=new URLSearchParams();body.append('upload_id',hid.value);body.append('title',titleEl?titleEl.value:'');
    fetch('/app/rpd/generate',{{method:'POST',body:body,headers:{{'Content-Type':'application/x-www-form-urlencoded'}}}})
      .then(function(r){{return r.json();}}).then(function(d){{if(d&&d.redirect){{window.location.href=d.redirect;}}else{{throw 0;}}}})
      .catch(function(){{go.disabled=false;go.textContent='Сформировать РПД →';lbl.textContent='Не удалось собрать РПД';}});
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
 body{{font-family:system-ui,'Segoe UI',sans-serif;margin:0;background:#f4f7f5;color:var(--ink);padding:24px}}
 .wrap{{max-width:1120px;margin:0 auto}}
 a.back{{color:var(--accent);text-decoration:none;font-size:.9rem}}
 h1{{font-size:1.35rem;margin:.4rem 0 .1rem}} .sub{{color:var(--muted);margin:0 0 18px;font-size:.9rem}}
 .grid{{display:grid;grid-template-columns:300px 1fr;gap:18px;align-items:start}}
 @media (max-width:820px){{.grid{{grid-template-columns:1fr}}}}
 .nav{{background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden;max-height:74vh;overflow-y:auto}}
 .navitem{{display:flex;align-items:center;gap:8px;padding:10px 12px;border-top:1px solid var(--line);text-decoration:none;color:var(--ink)}}
 .navitem:first-child{{border-top:none}} .navitem.on{{background:var(--soft)}}
 .navitem .nnum{{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:var(--muted);min-width:30px}}
 .navitem .ntitle{{font-size:13px;flex:1}}
 .card{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:20px}}
 .sechead{{display:flex;align-items:center;gap:10px;margin-bottom:6px}} .sechead h2{{font-size:1.05rem;margin:0}}
 .sechead .snum{{font-family:ui-monospace,Consolas,monospace;color:var(--muted);font-size:.85rem}}
 .hint{{color:var(--muted);font-size:12.5px;margin:0 0 14px}}
 .prosein{{width:100%;padding:12px;border:1px solid var(--line);border-radius:9px;font:inherit;font-size:14px;line-height:1.5;resize:vertical}}
 .tblwrap{{overflow-x:auto;border:1px solid var(--line);border-radius:9px}}
 table.edit{{border-collapse:collapse;width:100%;min-width:640px}}
 table.edit th{{background:var(--soft);color:var(--accent);font-size:11.5px;text-align:left;padding:8px 10px;position:sticky;top:0}}
 table.edit td{{border-top:1px solid var(--line);padding:3px}}
 table.edit input{{width:100%;min-width:90px;border:none;padding:7px 8px;font:inherit;font-size:13px;background:transparent}}
 table.edit input:focus{{outline:2px solid var(--soft);border-radius:6px}}
 .cs{{font-size:11px;font-weight:650;padding:3px 9px;border-radius:999px;white-space:nowrap}}
 .cs.up{{background:var(--soft);color:var(--accent)}} .cs.gen{{background:#eef2ff;color:#4f46e5}}
 .cs.form{{background:#eef1f4;color:#5f6f7d}} .cs.gap{{background:var(--gapsoft);color:var(--gap)}}
 .btn{{display:inline-flex;align-items:center;gap:8px;background:var(--accent);color:#fff;border:none;border-radius:9px;
   padding:10px 18px;font:inherit;font-weight:650;font-size:14px;cursor:pointer;text-decoration:none}}
 .btn.ghost{{background:#fff;color:var(--accent);border:1px solid var(--accent)}}
 .rowbar{{display:flex;align-items:center;gap:10px;margin-top:16px;flex-wrap:wrap}}
 .navbtn{{color:var(--accent);text-decoration:none;font-weight:600;font-size:14px}} .navbtn.off{{color:#b9c2bd}}
 .spacer{{flex:1}}
 .exportbar{{margin-top:14px;display:flex;justify-content:flex-end}}
</style></head><body><div class="wrap">
 <a class="back" href="/app/rpd">← к списку РПД</a>
 <h1>РПД: {name}</h1>
 <p class="sub">Раздел {pos} из {total} · правьте по разделам, затем экспорт</p>
 <div class="grid">
   <nav class="nav">{nav}</nav>
   <div>
     <div class="card">
       <div class="sechead"><span class="snum">{number}</span><h2>{title}</h2>{chip}</div>
       <p class="hint">{hint}</p>
       <form method="post" action="/app/rpd/{run_id}/section/{index}">
         {editor}
         <div class="rowbar">
           <button class="btn" type="submit">Сохранить раздел</button>
           <span class="spacer"></span>
           {prev_btn}{next_btn}
         </div>
       </form>
     </div>
     <div class="exportbar"><a class="btn ghost" href="/app/rpd/{run_id}/download">Экспорт РПД (DOCX)</a></div>
   </div>
 </div>
</div></body></html>"""
