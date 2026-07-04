"""Локальный веб-интерфейс для запуска аудита и просмотра отчёта."""

from __future__ import annotations

import hmac
import html
import json
import re
import secrets
import shutil
import subprocess
import tarfile
import zipfile
from collections import Counter
from collections.abc import Iterable
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import quote, unquote

from content_factory.audit.domain import (
    CRITERION_LABELS,
    ISSUE_KIND_LABELS,
    SEVERITY_LABELS,
    VERDICT_LABELS,
    AuditReport,
    AuditSettings,
    Criterion,
    Finding,
    Severity,
)
from content_factory.audit.env import get_env_value
from content_factory.audit.exporters import write_report
from content_factory.audit.orchestrator import AuditRunner
from content_factory.audit.report_formatting import (
    format_finding_explanation_html,
    format_finding_fragment,
)

DEFAULT_REPORT_DIR = Path("reports") / "ui_latest"
DEFAULT_MODEL = "openai/gpt-5.4-mini"
DEFAULT_FACT_MODEL = "perplexity/sonar"
DEFAULT_TECH_MODEL = "qwen/qwen3-coder"
AUTH_COOKIE_NAME = "audit_auth"
AVATAR_PATH = Path(__file__).resolve().parent / "avatar-placeholder.jpg"
AVATAR_ROUTE = "/assets/avatar-placeholder.jpg"
ARCHIVE_FIELD_NAME = "project_archive"
INTERNAL_ARCHIVE_PATH_FIELD = "__archive_path"
INTERNAL_ARCHIVE_NAME_FIELD = "__archive_name"
INTERNAL_UPLOAD_DIR_FIELD = "__upload_dir"
MAX_ARCHIVE_BYTES = 250_000_000
WEB_TEMP_DIR = Path(".tmp") / "web"


class WebState:
    """Состояние локального веб-сервера."""

    def __init__(self, default_input: Path | None, report_dir: Path, env_values: dict[str, str]) -> None:
        self.default_input = default_input
        self.report_dir = report_dir
        self.env_values = env_values
        self.last_error: str | None = None
        self.auth_username = get_env_value(("AUTH_USERNAME",), env_values) or ""
        self.auth_password = get_env_value(("AUTH_PASSWORD",), env_values) or ""
        self.auth_sessions: set[str] = set()

    @property
    def auth_enabled(self) -> bool:
        """Возвращает True, если в окружении заданы статические учётные данные."""

        return bool(self.auth_username and self.auth_password)


def run_from_form(form: dict[str, str], state: WebState) -> AuditReport:
    """Создаёт настройки из формы, запускает аудит и сохраняет отчёт."""

    cleanup_dirs = _cleanup_dirs_from_form(form)
    try:
        input_path, source_label, extracted_dir = _resolve_run_input(form, state)
        if extracted_dir is not None:
            cleanup_dirs.append(extracted_dir)
        model_name = get_env_value(("OPENROUTER_MODEL", "OPEN_ROUTER_MODEL"), state.env_values) or DEFAULT_MODEL
        fact_model_name = get_env_value(("OPENROUTER_FACT_MODEL", "OPEN_ROUTER_FACT_MODEL"), state.env_values) or DEFAULT_FACT_MODEL
        tech_model_name = get_env_value(("OPENROUTER_TECH_MODEL", "OPEN_ROUTER_TECH_MODEL"), state.env_values) or DEFAULT_TECH_MODEL
        api_key = get_env_value(("POLZA_AI_API_KEY", "OPENROUTER_API_KEY", "OPEN_ROUTER_API_KEY"), state.env_values)
        settings = AuditSettings(
            input_path=input_path,
            output_path=state.report_dir,
            allow_network=True,
            use_model=True,
            include_unknown=True,
            openrouter_api_key=api_key,
            openrouter_model=model_name,
            openrouter_fact_model=fact_model_name,
            openrouter_tech_model=tech_model_name,
        )
        report = AuditRunner(settings).run()
        if source_label:
            report = report.model_copy(update={"summary": report.summary.model_copy(update={"input_path": source_label})})
        write_report(report, state.report_dir)
        return report
    finally:
        _cleanup_directories(cleanup_dirs)


def _resolve_run_input(form: dict[str, str], state: WebState) -> tuple[Path, str | None, Path | None]:
    """Определяет источник проверки: загруженный архив или локальный путь."""

    archive_path_value = (form.get(INTERNAL_ARCHIVE_PATH_FIELD) or "").strip()
    if archive_path_value:
        archive_path = Path(archive_path_value).expanduser().resolve()
        archive_name = (form.get(INTERNAL_ARCHIVE_NAME_FIELD) or archive_path.name).strip()
        extracted_dir = _make_web_temp_dir("audit_project_")
        try:
            _extract_archive(archive_path, extracted_dir)
            input_path = _select_extracted_project_root(extracted_dir)
        except Exception:
            shutil.rmtree(extracted_dir, ignore_errors=True)
            raise
        return input_path, f"Архив: {archive_name}", extracted_dir

    raw_input = (form.get("input_path") or "").strip()
    if not raw_input and state.default_input is not None:
        raw_input = str(state.default_input)
    if not raw_input:
        raise ValueError("Укажите путь к проекту или загрузите архив.")
    return Path(raw_input).expanduser().resolve(), None, None


def _cleanup_dirs_from_form(form: dict[str, str]) -> list[Path]:
    """Берёт из формы только внутренние временные папки, созданные загрузчиком."""

    upload_dir = (form.get(INTERNAL_UPLOAD_DIR_FIELD) or "").strip()
    return [Path(upload_dir)] if upload_dir else []


def _cleanup_directories(paths: list[Path]) -> None:
    """Удаляет временные данные запуска, не трогая папку отчёта."""

    for path in paths:
        shutil.rmtree(path, ignore_errors=True)


def _make_web_temp_dir(prefix: str) -> Path:
    """Создаёт временную папку внутри рабочего каталога приложения."""

    WEB_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    for _attempt in range(100):
        path = WEB_TEMP_DIR / f"{prefix}{secrets.token_hex(8)}"
        try:
            path.mkdir()
            return path.resolve()
        except FileExistsError:
            continue
    raise RuntimeError("Не удалось создать временную папку загрузки.")


def _read_multipart_form(body: bytes, content_type: str) -> dict[str, str]:
    """Разбирает multipart-форму и сохраняет загруженный архив во временную папку."""

    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
    )
    form: dict[str, str] = {}
    upload_dir: Path | None = None
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        payload = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename:
            if not payload or name != ARCHIVE_FIELD_NAME:
                continue
            upload_dir = upload_dir or _make_web_temp_dir("audit_upload_")
            safe_name = Path(filename).name or "project_archive"
            archive_path = upload_dir / safe_name
            archive_path.write_bytes(payload)
            form[INTERNAL_ARCHIVE_PATH_FIELD] = str(archive_path)
            form[INTERNAL_ARCHIVE_NAME_FIELD] = safe_name
            form[INTERNAL_UPLOAD_DIR_FIELD] = str(upload_dir)
            continue
        charset = part.get_content_charset() or "utf-8"
        form[name] = payload.decode(charset, errors="replace")
    return form


def _extract_archive(archive_path: Path, target_dir: Path) -> None:
    """Безопасно распаковывает архив без выхода за пределы временной папки."""

    if not archive_path.exists():
        raise ValueError("Загруженный архив не найден.")
    suffixes = "".join(archive_path.suffixes).lower()
    if zipfile.is_zipfile(archive_path):
        _extract_zip_archive(archive_path, target_dir)
    elif tarfile.is_tarfile(archive_path) or suffixes in {".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz"}:
        _extract_tar_archive(archive_path, target_dir)
    elif archive_path.suffix.lower() == ".rar":
        _extract_rar_archive(archive_path, target_dir)
    else:
        raise ValueError("Поддерживаются архивы ZIP, RAR, TAR, TAR.GZ, TGZ, TAR.BZ2 и TAR.XZ.")
    if not any(target_dir.rglob("*")):
        raise ValueError("Архив пустой или не содержит файлов проекта.")


def _extract_zip_archive(archive_path: Path, target_dir: Path) -> None:
    """Распаковывает ZIP с защитой от путей вида ../file."""

    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            destination = _safe_archive_destination(target_dir, member.filename)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)


def _extract_tar_archive(archive_path: Path, target_dir: Path) -> None:
    """Распаковывает TAR, не извлекая ссылки и специальные файлы."""

    with tarfile.open(archive_path) as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            destination = _safe_archive_destination(target_dir, member.name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                continue
            with source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)


def _extract_rar_archive(archive_path: Path, target_dir: Path) -> None:
    """Распаковывает RAR через доступные инструменты после проверки списка файлов."""

    tools = _find_rar_tools()
    if not tools:
        raise ValueError(
            "Для RAR нужен установленный распаковщик: 7zz/7z, unrar, unar или bsdtar. "
            "Установите один из них на сервере или загрузите ZIP/TAR."
        )

    errors: list[str] = []
    for tool in tools:
        try:
            _extract_rar_with_tool(tool, archive_path, target_dir)
            return
        except ValueError as exc:
            errors.append(f"{tool}: {exc}")
            _clear_directory(target_dir)

    raise ValueError("Не удалось распаковать RAR-архив. Попытки: " + " | ".join(errors))


def _extract_rar_with_tool(tool: str, archive_path: Path, target_dir: Path) -> None:
    """Пробует один RAR-инструмент; ошибки оставляет вызывающему коду для fallback."""

    list_result = subprocess.run(_rar_list_command(tool, archive_path), capture_output=True, text=True, timeout=120)
    if list_result.returncode != 0:
        raise ValueError(f"не удалось прочитать список файлов: {_short_process_error(list_result)}")

    members = _parse_rar_listing(tool, list_result.stdout)
    if not members:
        raise ValueError("архив пустой или список файлов не удалось прочитать")
    for member_name in members:
        _safe_archive_destination(target_dir, member_name)

    extract_result = subprocess.run(_rar_extract_command(tool, archive_path, target_dir), capture_output=True, text=True, timeout=300)
    if extract_result.returncode != 0:
        raise ValueError(f"не удалось распаковать: {_short_process_error(extract_result)}")


def _find_rar_tool() -> str | None:
    """Возвращает первый доступный распаковщик RAR для обратной совместимости."""

    tools = _find_rar_tools()
    return tools[0] if tools else None


def _find_rar_tools() -> list[str]:
    """Ищет все доступные распаковщики RAR в порядке предпочтения."""

    tools: list[str] = []
    for tool in ("7zz", "unrar", "unar", "7z", "bsdtar"):
        if tool == "unar" and not shutil.which("lsar"):
            continue
        if shutil.which(tool):
            tools.append(tool)
    return tools


def _rar_list_command(tool: str, archive_path: Path) -> list[str]:
    if tool in {"7z", "7zz"}:
        return [tool, "l", "-slt", str(archive_path)]
    if tool == "unar":
        return ["lsar", "-json", str(archive_path)]
    if tool == "unrar":
        return [tool, "lb", str(archive_path)]
    return [tool, "-tf", str(archive_path)]


def _rar_extract_command(tool: str, archive_path: Path, target_dir: Path) -> list[str]:
    target_dir.mkdir(parents=True, exist_ok=True)
    if tool in {"7z", "7zz"}:
        return [tool, "x", "-y", f"-o{target_dir}", str(archive_path)]
    if tool == "unar":
        return [tool, "-f", "-D", "-o", str(target_dir), str(archive_path)]
    if tool == "unrar":
        return [tool, "x", "-o+", str(archive_path), str(target_dir)]
    return [tool, "-xf", str(archive_path), "-C", str(target_dir)]


def _parse_rar_listing(tool: str, output: str) -> list[str]:
    """Достаёт пути файлов из вывода выбранного распаковщика."""

    if tool in {"7z", "7zz"}:
        members: list[str] = []
        in_entries = False
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if line.startswith("----------"):
                in_entries = True
                continue
            if in_entries and line.startswith("Path = "):
                value = line.removeprefix("Path = ").strip()
                if value:
                    members.append(value)
        return members
    if tool == "unar":
        return _parse_lsar_json_listing(output)
    return [line.strip() for line in output.splitlines() if line.strip()]


def _parse_lsar_json_listing(output: str) -> list[str]:
    """Достаёт пути файлов из JSON-вывода lsar."""

    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return []
    members: list[str] = []
    for item in _walk_json_values(payload):
        if not isinstance(item, dict):
            continue
        raw_name = item.get("XADFileName") or item.get("filename") or item.get("name") or item.get("path")
        if isinstance(raw_name, str) and raw_name.strip():
            members.append(raw_name.strip())
    return members


def _walk_json_values(value: object) -> Iterable[object]:
    """Обходит вложенные структуры JSON без привязки к точной версии lsar."""

    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_json_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json_values(child)


def _short_process_error(result: subprocess.CompletedProcess[str]) -> str:
    text = (result.stderr or result.stdout or "").strip()
    return text[:500] or f"код {result.returncode}"


def _clear_directory(path: Path) -> None:
    """Очищает временную папку между попытками распаковки."""

    if not path.exists():
        return
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def _safe_archive_destination(target_dir: Path, member_name: str) -> Path:
    """Проверяет, что путь из архива не выходит из временной папки."""

    normalized_name = member_name.replace("\\", "/").strip()
    if normalized_name.startswith(("/", "//")) or re.match(r"^[A-Za-z]:", normalized_name):
        raise ValueError("Архив содержит небезопасный путь.")
    destination = (target_dir / normalized_name).resolve()
    target_root = target_dir.resolve()
    if not destination.is_relative_to(target_root):
        raise ValueError("Архив содержит небезопасный путь.")
    return destination


def _select_extracted_project_root(extracted_dir: Path) -> Path:
    """Выбирает корень проекта внутри архива."""

    entries = [path for path in extracted_dir.iterdir() if path.name != "__MACOSX"]
    directories = [path for path in entries if path.is_dir()]
    files = [path for path in entries if path.is_file()]
    if len(directories) == 1 and not files:
        return directories[0].resolve()
    return extracted_dir.resolve()


def load_latest_report(report_dir: Path) -> AuditReport | None:
    """Загружает последний отчёт, если он уже есть."""

    path = report_dir / "report.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return AuditReport.model_validate(payload)


def render_page(report: AuditReport | None, state: WebState, form_values: dict[str, str] | None = None) -> str:
    """Собирает полную страницу веб-интерфейса."""

    form = form_values or {}
    if form_values is not None and "input_path" in form:
        input_value = form.get("input_path", "")
    else:
        input_value = report.summary.input_path if report else str(state.default_input or "")
    body = "\n".join(
        [
            _render_topbar(),
            '<main class="shell">',
            _render_run_panel(
                report,
                input_value,
            ),
            _render_error(state.last_error),
            _render_dashboard(report, state.report_dir) if report else _render_empty_state(),
            "</main>",
            _render_script(),
        ]
    )
    return f"<!doctype html><html lang=\"ru\"><head>{_render_head()}</head><body>{body}</body></html>"


def render_login_page(error: str = "") -> str:
    """Собирает страницу статической авторизации."""

    error_html = f'<div class="login-error">{_esc(error)}</div>' if error else ""
    body = f"""
<main class="login-shell">
  <form class="login-box" method="post" action="/login">
    <div class="login-mark" aria-hidden="true">
      <img src="{AVATAR_ROUTE}" alt="">
    </div>
    <h1>Авторизация</h1>
    <label for="username">Логин</label>
    <input id="username" name="username" autocomplete="username" required>
    <label for="password">Пароль</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required>
    <button class="login-submit" type="submit">Войти</button>
    {error_html}
  </form>
</main>
"""
    return f"<!doctype html><html lang=\"ru\"><head>{_render_head('Авторизация')}</head><body class=\"login-page\">{body}</body></html>"


def credentials_match(username: str, password: str, state: WebState) -> bool:
    """Сравнивает логин и пароль с `.env` без раннего выхода по символам."""

    if not state.auth_enabled:
        return True
    username_ok = hmac.compare_digest(username.encode("utf-8"), state.auth_username.encode("utf-8"))
    password_ok = hmac.compare_digest(password.encode("utf-8"), state.auth_password.encode("utf-8"))
    return username_ok and password_ok


def _auth_cookie(token: str) -> str:
    """Формирует защищённую cookie локальной сессии."""

    return f"{AUTH_COOKIE_NAME}={token}; Path=/; Max-Age=28800; HttpOnly; SameSite=Lax"


def _clear_auth_cookie() -> str:
    """Формирует cookie для сброса локальной сессии."""

    return f"{AUTH_COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"


def _render_head(title: str = "Аудит контента · Панель отчёта") -> str:
    """Возвращает заголовок страницы и стили."""

    return """
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__PAGE_TITLE__</title>
<link rel="stylesheet" href="/static/css/s21-tokens.css?v=20260704-shared-tokens">
<style>
/* Palette mapped onto the shared design tokens (s21-tokens.css) so the auditor
 * renders from the same one palette as the generator and catalog. */
:root {
  --bg: var(--s21-bg);
  --bg-top: var(--s21-surface-2);
  --surface: var(--s21-surface);
  --surface-strong: var(--s21-surface);
  --surface-muted: var(--s21-surface-muted);
  --border: var(--s21-border);
  --border-soft: var(--s21-grid-line);
  --border-strong: var(--s21-border-strong);
  --text: var(--s21-ink);
  --muted: var(--s21-muted);
  --accent: var(--s21-accent);
  --accent-bright: var(--s21-accent);
  --accent-deep: var(--s21-accent-hover);
  --accent-soft: var(--s21-accent-soft);
  --warn: var(--s21-warn);
  --warn-soft: var(--s21-warn-bg);
  --info: var(--s21-info);
  --amber: var(--s21-warn);
  --danger: var(--s21-danger);
  --danger-soft: var(--s21-danger-bg);
  --shadow-sm: var(--s21-shadow-1);
  --shadow: var(--s21-shadow-2);
  --radius: var(--s21-radius-xl);
  --radius-md: var(--s21-radius-lg);
  --radius-sm: var(--s21-radius-md);
  --font-sans: var(--s21-font-sans);
  --font-mono: var(--s21-font-mono);
}
* { box-sizing: border-box; }
html, body { margin: 0; }
body {
  min-height: 100vh;
  color: var(--text);
  background: var(--bg);
  font-family: var(--font-sans);
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}
.eco-nav {
  display: flex;
  gap: 4px;
  align-items: center;
  flex-wrap: wrap;
  padding: 6px 32px;
  background: var(--s21-dark);
  border-bottom: 1px solid var(--s21-border-strong);
}
.eco-nav-link {
  color: rgba(255, 255, 255, .72);
  text-decoration: none;
  font-size: 13px;
  font-weight: 500;
  padding: 6px 12px;
  border-radius: var(--radius-sm);
  line-height: 1;
  transition: background .15s ease, color .15s ease;
}
.eco-nav-link:hover { color: #fff; background: rgba(255, 255, 255, .10); }
.eco-nav-link.active { color: var(--s21-ink); background: var(--s21-accent); }
.topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  backdrop-filter: blur(18px);
  background: rgba(255, 255, 255, .86);
  border-bottom: 1px solid var(--border-soft);
}
.topbar-inner, .shell { max-width: 1720px; margin: 0 auto; padding-left: 32px; padding-right: 32px; }
.topbar-inner { min-height: 62px; display: flex; align-items: center; justify-content: space-between; gap: 18px; }
.wordmark { display: flex; align-items: center; gap: 12px; min-width: 0; }
.glyph {
  width: 32px; height: 32px; border-radius: var(--radius-sm);
  display: grid; place-items: center; overflow: hidden;
  background: var(--surface-strong);
  box-shadow: 0 10px 24px rgba(14, 143, 111, .24);
}
.glyph img { width: 100%; height: 100%; object-fit: cover; display: block; }
.brand-title {
  font-size: 14px;
  font-weight: 600;
  line-height: 1.2;
  letter-spacing: 0;
}
.brand-sub {
  color: var(--muted);
  font-size: 11.5px;
  font-weight: 400;
  line-height: 1.2;
  letter-spacing: 0;
}
.top-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.shell { padding-top: 26px; padding-bottom: 72px; }
body.login-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
}
.login-box {
  width: min(380px, 100%);
  padding: 24px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  background: var(--surface);
  box-shadow: var(--shadow);
}
.login-mark {
  width: 64px;
  height: 64px;
  margin-bottom: 16px;
  border-radius: var(--radius-md);
  overflow: hidden;
  background: var(--surface-strong);
  box-shadow: var(--shadow-sm);
}
.login-mark img { width: 100%; height: 100%; object-fit: cover; display: block; }
.login-box h1 { margin: 4px 0 20px; font-size: 28px; font-weight: 800; line-height: 1.2; }
.login-box label { display: block; margin: 14px 0 6px; color: var(--muted); font-size: 12px; font-weight: 800; }
.login-box input {
  width: 100%;
  height: 42px;
  padding: 0 12px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
  background: var(--surface-strong);
  color: var(--text);
  font: 14px var(--font-sans);
}
.login-box input:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(14,143,111,.14);
}
.login-submit {
  width: 100%;
  height: 42px;
  margin-top: 20px;
  border: 0;
  border-radius: var(--radius-sm);
  background: var(--accent-bright);
  color: var(--text);
  font-weight: 800;
  cursor: pointer;
}
.login-submit:hover { background: var(--accent); color: #fff; }
.login-error {
  margin-top: 12px;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  background: var(--danger-soft);
  color: var(--danger);
  font-size: 13px;
  font-weight: 800;
}
.run-panel {
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 22px;
}
.panel-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 18px; margin-bottom: 18px; }
h1 { margin: 0; font-size: 24px; line-height: 1.15; letter-spacing: 0; }
.muted { color: var(--muted); font-size: 13px; margin: 6px 0 0; }
.form-grid { display: grid; grid-template-columns: minmax(0, 1fr) 138px; gap: 12px; align-items: end; }
label { display: block; font-size: 12px; color: var(--muted); font-weight: 800; letter-spacing: 0; margin-bottom: 7px; }
input[type="text"], select {
  width: 100%;
  height: 44px;
  border: 1px solid var(--border-strong);
  border-radius: 999px;
  padding: 0 16px;
  background: var(--surface-strong);
  color: var(--text);
  font: 14px var(--font-mono);
  outline: none;
}
input[type="text"]:focus, select:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(14,143,111,.14); }
.upload-zone {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 12px;
  padding: 13px 16px;
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius-sm);
  background: rgba(255,253,250,.72);
  cursor: pointer;
}
.upload-zone:hover,
.upload-zone.is-dragging {
  border-color: var(--accent);
  background: var(--accent-soft);
}
.upload-zone input[type="file"] {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
}
.upload-title { font-size: 13px; font-weight: 900; color: var(--text); }
.upload-name { color: var(--muted); font: 800 12px var(--font-sans); overflow-wrap: anywhere; text-align: right; }
.button {
  border: 0;
  border-radius: 999px;
  height: 44px;
  padding: 0 18px;
  font: 800 14px var(--font-sans);
  cursor: pointer;
  color: #fff;
  background: linear-gradient(135deg, var(--accent), var(--accent-bright));
  box-shadow: 0 12px 24px rgba(14, 143, 111, .22);
}
.link-button {
  display: inline-flex; align-items: center; justify-content: center;
  height: 34px; padding: 0 13px; border-radius: 999px;
  text-decoration: none; color: var(--text); background: var(--surface-strong);
  border: 1px solid var(--border-strong);
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0;
}
.options { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px; }
.check {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 8px 12px; border-radius: 999px; border: 1px solid var(--border-soft);
  background: var(--surface-strong); font-size: 13px; font-weight: 700; color: var(--text);
}
.check input { accent-color: var(--accent); }
.alert {
  margin-top: 18px; padding: 14px 16px; border-radius: var(--radius-sm);
  border: 1px solid rgba(184,92,56,.25); background: var(--warn-soft); color: #78371f;
  font-weight: 700; font-size: 13px;
}
.empty {
  margin-top: 20px; padding: 30px; border: 1px dashed var(--border);
  border-radius: var(--radius); color: var(--muted); background: rgba(255,253,250,.58);
}
.summary-strip {
  margin-top: 18px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: 16px 18px;
}
.summary-main { display: flex; align-items: baseline; gap: 12px; min-width: 0; flex-wrap: wrap; }
.summary-number { font-size: 30px; font-weight: 900; color: var(--text); line-height: 1; }
.summary-text { color: var(--muted); font-size: 14px; font-weight: 800; }
.severity-inline { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
.severity-chip {
  display: inline-flex; align-items: center; min-height: 32px;
  border: 0;
  border-radius: 999px; padding: 5px 12px;
  font: 900 13px var(--font-sans);
  white-space: nowrap;
  cursor: pointer;
}
.severity-chip:hover,
.severity-chip.is-active { box-shadow: 0 0 0 3px rgba(14,143,111,.12); }
.severity-chip-critical { color: var(--danger); background: var(--danger-soft); }
.severity-chip-major { color: #98630c; background: #f3dfac; }
.severity-chip-minor { color: var(--muted); background: #ece7dc; }
.severity-chip-info { color: var(--muted); background: var(--surface-muted); }
.section { margin-top: 26px; }
.section-head { display: flex; align-items: baseline; justify-content: space-between; gap: 14px; border-bottom: 1px solid var(--border-soft); padding-bottom: 12px; margin-bottom: 14px; }
.section h2 { margin: 0; font-size: 20px; }
.grid-three { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }
.criteria-strip { margin-top: 22px; }
.criteria-title { color: var(--muted); font-size: 13px; font-weight: 900; margin-bottom: 8px; }
.criteria-hint { color: var(--muted); font-size: 13px; margin-bottom: 10px; }
.criteria-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.criterion-filter {
  min-height: 38px;
  border: 1px solid var(--border-soft);
  border-radius: 999px;
  padding: 7px 13px;
  background: var(--surface-strong);
  color: var(--text);
  display: inline-flex;
  align-items: center;
  gap: 7px;
  cursor: pointer;
  font: 900 13px var(--font-sans);
}
.criterion-filter:hover,
.criterion-filter.is-active {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(14,143,111,.12);
}
.criterion-filter.is-active { background: var(--accent-soft); }
.criterion-filter.is-empty { color: #8b8b84; background: transparent; opacity: .7; }
.criterion-filter.is-empty:not(.is-active) { box-shadow: none; }
.criterion-count { color: inherit; font: 900 13px var(--font-mono); }
.panel {
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
  padding: 18px;
}
.bar-row { display: grid; grid-template-columns: minmax(118px, 1fr) minmax(84px, 1.1fr) 44px; gap: 12px; align-items: center; padding: 9px 0; border-bottom: 1px solid var(--border-soft); }
.bar-row:last-child { border-bottom: 0; }
.bar-label { font-size: 13px; font-weight: 800; overflow-wrap: anywhere; }
.bar-track { height: 10px; border-radius: 999px; background: var(--surface-muted); overflow: hidden; }
.bar-fill { height: 100%; border-radius: 999px; background: linear-gradient(90deg, var(--accent), var(--accent-bright)); }
.bar-count { color: var(--muted); text-align: right; font: 700 12px var(--font-mono); }
.metric-item { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 9px 0; border-bottom: 1px solid var(--border-soft); }
.metric-item:last-child { border-bottom: 0; }
.metric-name { font-size: 13px; font-weight: 800; }
.metric-value { color: var(--muted); font-size: 12px; font-weight: 900; text-align: right; }
.metric-empty { color: var(--muted); font-size: 13px; font-weight: 700; padding: 10px 0; }
.table-wrap {
  overflow-x: auto;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
}
table {
  width: 100%;
  min-width: 2440px;
  table-layout: fixed;
  border-collapse: collapse;
}
col.col-criterion { width: 150px; }
col.col-kind { width: 130px; }
col.col-verdict { width: 170px; }
col.col-severity { width: 130px; }
col.col-file { width: 190px; }
col.col-line { width: 120px; }
col.col-fragment { width: 340px; }
col.col-evidence { width: 520px; }
col.col-source { width: 320px; }
col.col-checked { width: 190px; }
col.col-support { width: 160px; }
col.col-latest { width: 160px; }
col.col-recommended { width: 190px; }
col.col-confidence { width: 110px; }
col.col-module { width: 190px; }
th, td {
  padding: 14px 13px;
  border-bottom: 1px solid var(--border-soft);
  text-align: left;
  vertical-align: top;
  font-size: 13px;
  overflow-wrap: anywhere;
  word-break: normal;
}
th {
  position: relative;
  background: var(--surface-muted);
  color: var(--muted);
  font-size: 11px;
  letter-spacing: 0;
}
.column-filter-trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  font: 900 11px var(--font-sans);
  text-align: left;
}
.column-filter-trigger:hover,
.column-filter-trigger.is-active { color: var(--accent-deep); }
.column-filter-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 17px;
  height: 17px;
  border-radius: 999px;
  background: rgba(255,255,255,.66);
  color: var(--muted);
  font-size: 11px;
  flex-shrink: 0;
}
.column-filter-trigger.is-active .column-filter-mark {
  color: var(--accent-deep);
  background: var(--accent-soft);
}
.column-filter-menu {
  position: fixed;
  z-index: 50;
  width: min(320px, calc(100vw - 24px));
  max-height: 360px;
  overflow: hidden;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow);
  background: var(--surface);
}
.column-filter-menu[hidden] { display: none; }
.column-filter-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 11px 12px;
  border-bottom: 1px solid var(--border-soft);
  color: var(--text);
  font-size: 13px;
  font-weight: 900;
}
.column-filter-clear {
  border: 0;
  background: transparent;
  color: var(--info);
  cursor: pointer;
  font: 900 12px var(--font-sans);
}
.column-filter-options {
  max-height: 292px;
  overflow-y: auto;
  padding: 7px;
}
.column-filter-option {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 7px 6px;
  border-radius: 8px;
  color: var(--text);
  font-size: 12px;
  font-weight: 800;
  line-height: 1.3;
  cursor: pointer;
}
.column-filter-option:hover { background: var(--surface-muted); }
.column-filter-option input { margin-top: 1px; accent-color: var(--accent); flex-shrink: 0; }
.column-filter-value { overflow-wrap: anywhere; }
.column-filter-empty {
  padding: 12px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 800;
}
tr:last-child td { border-bottom: 0; }
.mono { font-family: var(--font-mono); font-size: 12px; overflow-wrap: anywhere; }
.pill {
  display: inline-flex; align-items: center; white-space: nowrap;
  border-radius: 999px; padding: 4px 9px; font-size: 12px; font-weight: 900;
}
.pill-critical { color: var(--danger); background: var(--danger-soft); }
.pill-major { color: var(--warn); background: var(--warn-soft); }
.pill-minor { color: var(--info); background: #e7eefb; }
.pill-info { color: var(--muted); background: #ece7dc; }
.pill-fail { color: var(--danger); background: var(--danger-soft); }
.pill-warning, .pill-unknown { color: var(--warn); background: var(--warn-soft); }
.pill-pass { color: var(--accent-deep); background: var(--accent-soft); }
.fragment, .evidence, .source {
  color: var(--text);
  white-space: normal;
  overflow-wrap: anywhere;
}
.reason-label { color: var(--muted); font-weight: 900; }
.reason-action { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border-soft); }
.downloads { display: flex; gap: 8px; flex-wrap: wrap; }
.loading { opacity: .72; pointer-events: none; }
.run-details > summary { list-style: none; cursor: pointer; outline: none; }
.run-details > summary::-webkit-details-marker { display: none; }
.run-bar { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.run-details[open] .run-bar { margin-bottom: 18px; padding-bottom: 16px; border-bottom: 1px solid var(--border-soft); }
.run-bar-text { font-weight: 900; font-size: 16px; overflow-wrap: anywhere; }
.run-bar-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.run-bar-edit { color: var(--info); font-size: 13px; font-weight: 900; white-space: nowrap; }
.run-details:not([open]) .run-bar-edit::after { content: "Изменить"; }
.run-details[open] .run-bar-edit::after { content: "Свернуть"; }
.run-restart { cursor: pointer; }
.run-progress[hidden] { display: none; }
.run-progress {
  margin-top: 14px;
  border: 1px solid var(--border-soft);
  border-radius: var(--radius-sm);
  padding: 12px;
  background: var(--surface-strong);
}
.run-progress-head {
  display: flex; justify-content: space-between; gap: 12px; align-items: baseline;
  color: var(--muted); font-size: 12px; font-weight: 900;
}
.run-progress-stage { color: var(--text); overflow-wrap: anywhere; }
.run-progress-track {
  height: 10px; margin-top: 10px; overflow: hidden;
  border-radius: 999px; background: var(--surface-muted);
}
.run-progress-fill {
  width: 0%; height: 100%; border-radius: inherit;
  background: linear-gradient(90deg, var(--accent), var(--accent-bright));
  transition: width .45s ease;
}
.run-progress-meta {
  display: flex; justify-content: space-between; gap: 12px; margin-top: 8px;
  color: var(--muted); font-size: 12px; font-weight: 800;
}
.run-progress.is-error { border-color: rgba(196, 54, 54, .35); background: var(--danger-soft); }
.filter-note {
  display: inline-flex; align-items: center; min-height: 30px; border-radius: 999px;
  padding: 5px 10px; font-size: 12px; font-weight: 800;
}
.filter-note { color: var(--info); background: #e7eefb; }
.filter-bar {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  margin: 16px 0 12px; padding: 12px 14px;
  background: var(--surface); border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm); box-shadow: var(--shadow-sm);
}
.filter-bar .check { min-height: 30px; margin: 0; }
.filter-bar-label { color: var(--muted); font-size: 12px; font-weight: 900; letter-spacing: 0; white-space: nowrap; }
.filter-chip {
  display: inline-flex; align-items: center; min-height: 30px;
  border-radius: 999px; padding: 5px 10px;
  color: var(--accent-deep); background: var(--accent-soft);
  font-size: 12px; font-weight: 900; white-space: nowrap;
}
table.findings.hide-unknown tr[data-verdict="unknown"] { display: none; }
.diagnostics {
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
  padding: 0;
}
.diagnostics > summary {
  cursor: pointer;
  list-style: none;
  padding: 16px 18px;
  font-weight: 900;
  color: var(--muted);
}
.diagnostics > summary::-webkit-details-marker { display: none; }
.diagnostics-body { padding: 0 18px 18px; }
@media (max-width: 980px) {
  .form-grid, .grid-three { grid-template-columns: 1fr; }
  .summary-strip { align-items: flex-start; flex-direction: column; }
  .severity-inline { justify-content: flex-start; }
  .panel-head { display: block; }
  .topbar-inner, .shell { padding-left: 16px; padding-right: 16px; }
}
</style>
""".replace("__PAGE_TITLE__", _esc(title), 1)


def _render_topbar() -> str:
    """Возвращает верхнюю панель."""

    return """
<nav class="eco-nav" aria-label="Разделы платформы">
  <a class="eco-nav-link" href="/app">Главная</a>
  <a class="eco-nav-link" href="/app/generate">Генерация</a>
  <a class="eco-nav-link active" href="/app/auditor">Аудитор</a>
  <a class="eco-nav-link" href="/app/translate">Перевод</a>
  <a class="eco-nav-link" href="/app/curriculum">УП</a>
  <a class="eco-nav-link" href="/app/spravochnik">Справочник</a>
  <a class="eco-nav-link" href="/app/instruction">Инструкция</a>
</nav>
<header class="topbar">
  <div class="topbar-inner">
    <div class="wordmark">
      <span class="glyph"><img src="/assets/avatar-placeholder.jpg" alt=""></span>
      <span>
        <div class="brand-title">Аудит контента</div>
        <div class="brand-sub">проверка учебных проектов</div>
      </span>
    </div>
    <nav class="top-actions">
      <a class="link-button" href="#summary">Сводка</a>
      <a class="link-button" href="/logout">Выйти</a>
    </nav>
  </div>
</header>
"""


def _render_run_panel(
    report: AuditReport | None,
    input_value: str,
) -> str:
    """Возвращает форму запуска, свёрнутую после построения отчёта."""

    open_attr = "" if report is not None else " open"
    restart_button = '<span class="link-button run-restart" role="button" tabindex="0">Перезапустить</span>' if report is not None else ""
    return f"""
<section class="run-panel">
  <details class="run-details"{open_attr}>
    <summary class="run-bar">
      <span class="run-bar-text">{_esc(_run_bar_text(report, input_value))}</span>
      <span class="run-bar-actions">
        {restart_button}
        <span class="run-bar-edit"></span>
      </span>
    </summary>
    <div class="panel-head">
      <div>
        <h1>Проверка локального проекта</h1>
      </div>
    </div>
    <form id="run-form" method="post" action="/run" enctype="multipart/form-data">
      <div class="form-grid">
        <div>
          <label for="input_path">Путь к проекту</label>
          <input id="input_path" name="input_path" type="text" value="{_esc(input_value)}" spellcheck="false">
        </div>
        <button class="button" type="submit">Запустить</button>
      </div>
      <label class="upload-zone" for="{ARCHIVE_FIELD_NAME}">
        <input id="{ARCHIVE_FIELD_NAME}" name="{ARCHIVE_FIELD_NAME}" type="file" accept=".zip,.rar,.tar,.gz,.tgz,.bz2,.xz">
        <span class="upload-title">Архив проекта</span>
        <span class="upload-name" id="archive-file-name">ZIP / RAR / TAR</span>
      </label>
      <div class="run-progress" id="run-progress" role="status" aria-live="polite" aria-busy="false" hidden>
        <div class="run-progress-head">
          <span>Готовность отчёта</span>
          <span class="run-progress-stage" id="run-progress-stage">Подготовка запуска</span>
        </div>
        <div class="run-progress-track" aria-hidden="true">
          <div class="run-progress-fill" id="run-progress-fill"></div>
        </div>
        <div class="run-progress-meta">
          <span id="run-progress-percent">0%</span>
          <span id="run-progress-elapsed">0 с</span>
        </div>
      </div>
    </form>
  </details>
</section>
"""


def _run_bar_text(report: AuditReport | None, input_value: str) -> str:
    """Короткая строка-шапка для свёрнутого блока запуска."""

    if report is None:
        return "Проверка локального проекта"
    project = Path(report.summary.input_path).name or Path(input_value).name or input_value
    cases = len(report.findings)
    return f"{project} · {cases} случаев"


def _render_error(error: str | None) -> str:
    """Показывает ошибку запуска, если она есть."""

    if not error:
        return ""
    return f'<div class="alert">{_esc(error)}</div>'


def _render_empty_state() -> str:
    """Показывает состояние до первого запуска."""

    return """
<section class="empty">
  <strong>Отчёта пока нет.</strong>
  <div>Запустите проверку по пути к проекту, чтобы увидеть панель отчёта.</div>
</section>
"""


def _render_dashboard(report: AuditReport, report_dir: Path) -> str:
    """Собирает панель отчёта."""

    return "\n".join(
        [
            _render_summary(report),
            _render_criterion_filters(report),
            _render_filter_bar(),
            _render_findings_table(report.findings),
            _render_observability(report),
            _render_downloads(report_dir),
        ]
    )


def _render_filter_bar() -> str:
    """Фильтры таблицы: работают на клиенте, без повторного прогона."""

    return """
<section class="filter-bar" id="filter-bar">
  <span class="filter-bar-label">Фильтры таблицы</span>
  <span class="filter-chip" id="active-criterion-label">Критерий: все</span>
  <span class="filter-chip" id="active-severity-label">Критичность: все</span>
  <span class="filter-chip" id="active-column-filter-label">Колонки: нет</span>
  <label class="check"><input type="checkbox" id="flt-hide-unknown"> Скрыть «нужна проверка»</label>
  <span class="filter-note" id="filter-result-count">видно: 0</span>
</section>
"""


def _case_findings(report: AuditReport) -> list[Finding]:
    """Возвращает строки, требующие внимания аудитора."""

    return list(report.findings)


def _render_summary(report: AuditReport) -> str:
    """Показывает главные счётчики."""

    summary = report.summary
    cases = _case_findings(report)
    by_severity = Counter(finding.severity.value for finding in cases)
    return f"""
<section id="summary" class="summary-strip">
  <div class="summary-main">
    <span class="summary-number">{len(cases)}</span>
    <span class="summary-text">случаев · {summary.files_total} файлов</span>
  </div>
  <div class="severity-inline">
    {_severity_chip(Severity.CRITICAL, by_severity.get(Severity.CRITICAL.value, 0))}
    {_severity_chip(Severity.MAJOR, by_severity.get(Severity.MAJOR.value, 0))}
    {_severity_chip(Severity.MINOR, by_severity.get(Severity.MINOR.value, 0))}
    {_severity_chip(Severity.INFO, by_severity.get(Severity.INFO.value, 0))}
  </div>
</section>
"""


def _render_observability(report: AuditReport) -> str:
    """Показывает техническую сводку выполнения."""

    usage = report.summary.model_usage
    usage_rows = {
        "Свежие вызовы": usage.calls_total,
        "Ответы из кэша": usage.cache_hits,
        "Токены": usage.total_tokens,
        "Стоимость, $": round(usage.cost_usd, 6),
    }
    step_rows = {step.name: step.duration_ms for step in report.summary.steps}
    usage_markup = (
        _bars(usage_rows, {})
        if any(value for value in usage_rows.values())
        else '<div class="metric-empty">Свежих вызовов моделей нет.</div>'
    )
    return f"""
<details class="section diagnostics">
  <summary>Диагностика прогона — шаги, стоимость, покрытие ТЗ, версии запросов</summary>
  <div class="diagnostics-body">
    <div class="muted">версии запросов: {_esc(', '.join(report.summary.prompt_versions.values()) or 'нет')}</div>
    <div class="grid-three">
    <div class="panel">
      <label>Стоимость и кэш</label>
      {usage_markup}
    </div>
    <div class="panel">
      <label>Шаги, мс</label>
      {_bars(step_rows, {})}
    </div>
    <div class="panel">
      <label>Покрытие ТЗ</label>
      {_render_requirement_status(report)}
    </div>
  </div>
  </div>
</details>
"""


def _render_criterion_filters(report: AuditReport) -> str:
    """Рисует компактные чипы критериев, которые фильтруют таблицу."""

    cases = _case_findings(report)
    by_criterion = Counter(finding.criterion.value for finding in cases)
    buttons = [
        f"""
<button type="button" class="criterion-filter is-active" data-criterion-filter="all" data-criterion-label="все">
  <span>Все</span><span class="criterion-count">{len(cases)}</span>
</button>
"""
    ]
    for criterion in Criterion:
        count = by_criterion.get(criterion.value, 0)
        empty_class = " is-empty" if count == 0 else ""
        buttons.append(
            f"""
<button type="button" class="criterion-filter{empty_class}" data-criterion-filter="{criterion.value}" data-criterion-label="{_esc(CRITERION_LABELS[criterion])}" title="{_esc(CRITERION_LABELS[criterion])}">
  <span>{_esc(_criterion_short_label(criterion))}</span><span class="criterion-count">{count}</span>
</button>
"""
        )
    return f"""
<section class="criteria-strip">
  <div class="criteria-title">Критерий — фильтр таблицы</div>
  <div class="criteria-hint">Нажмите критерий, чтобы оставить в таблице только связанные с ним сообщения.</div>
  <div class="criteria-chips">{"".join(buttons)}</div>
</section>
"""


def _render_requirement_status(report: AuditReport) -> str:
    """Показывает, какие управленческие поля из ТЗ есть в текущем отчёте."""

    summary = report.summary
    usage = summary.model_usage
    cases = _case_findings(report)
    affected_units = len({finding.unit_id for finding in cases})
    affected_branches = len({finding.branch or "без ветки" for finding in cases})
    rows = [
        ("Ветка и единица", f"{affected_branches} веток · {affected_units} ед."),
        ("Критичность", "Critical / Major / Minor / Info"),
        ("Экспорт", "XLSX / CSV / JSON"),
        ("Ссылки", "сеть использовалась" if summary.network_used else "сеть выключена"),
        ("Стоимость", "учтена" if usage.calls_total or usage.cache_hits else "нет модельных вызовов"),
    ]
    return _metric_rows(rows)


def _severity_chip(severity: Severity, count: int) -> str:
    """Рисует компактный счётчик критичности в общей сводке."""

    return (
        f'<button type="button" class="severity-chip severity-chip-{severity.value}" '
        f'data-severity-filter="{severity.value}" '
        f'data-severity-label="{_esc(SEVERITY_LABELS[severity].lower())}">'
        f'{count} {_esc(SEVERITY_LABELS[severity].lower())}</button>'
    )


def _criterion_short_label(criterion: Criterion) -> str:
    """Короткие подписи нужны, чтобы фильтры помещались в одну строку."""

    labels = {
        Criterion.ACTUALITY: "Актуальность",
        Criterion.LINKS: "Ссылки",
        Criterion.TECHNOLOGY_FRESHNESS: "Технологии",
        Criterion.FACTS: "Факты",
        Criterion.MARKET_FIT: "Рынок",
        Criterion.RIGHTS: "Права",
        Criterion.CORRECTNESS: "Точность",
        Criterion.READABILITY: "Грамотность",
        Criterion.CHECKLIST_ALIGNMENT: "Чек-лист",
        Criterion.WORKLOAD: "Трудоёмкость",
        Criterion.EXAM: "Экзамен",
        Criterion.LANGUAGE: "Язык",
        Criterion.IMAGE_QUALITY: "Изображения",
    }
    return labels.get(criterion, CRITERION_LABELS[criterion])


TABLE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("criterion", "Критерий"),
    ("kind", "Тип"),
    ("verdict", "Вердикт"),
    ("severity", "Критичность"),
    ("file", "Файл"),
    ("line", "Строка"),
    ("fragment", "Фрагмент"),
    ("evidence", "Обоснование"),
    ("source", "Источник"),
    ("checked", "Дата проверки"),
    ("support", "Статус поддержки"),
    ("latest", "Последняя версия"),
    ("recommended", "Рекомендуемая версия"),
    ("confidence", "Уверенность"),
    ("module", "Модуль"),
)


def _render_table_header(column_key: str, label: str) -> str:
    """Возвращает заголовок колонки с клиентским фильтром."""

    return f"""
          <th>
            <button type="button" class="column-filter-trigger" data-column-filter="{column_key}" data-column-label="{_esc(label)}" aria-expanded="false">
              <span>{_esc(label)}</span>
              <span class="column-filter-mark" aria-hidden="true">▾</span>
            </button>
          </th>"""


def _render_findings_table(findings: list[Finding]) -> str:
    """Показывает таблицу найденных случаев."""

    headers = "\n".join(_render_table_header(key, label) for key, label in TABLE_COLUMNS)
    rows = "\n".join(_render_finding_row(finding) for finding in findings)
    if not rows:
        rows = '<tr><td colspan="15">По выбранным условиям случаев нет.</td></tr>'
    rows += '\n<tr id="no-match" class="no-match" style="display:none"><td colspan="15">Под выбранные фильтры ничего не попадает.</td></tr>'
    return f"""
<section id="findings" class="section">
  <div class="section-head">
    <h2>Таблица результата</h2>
    <span class="muted">одна строка — один найденный случай</span>
  </div>
  <div class="table-wrap">
    <table id="findings-table" class="findings">
      <colgroup>
        <col class="col-criterion">
        <col class="col-kind">
        <col class="col-verdict">
        <col class="col-severity">
        <col class="col-file">
        <col class="col-line">
        <col class="col-fragment">
        <col class="col-evidence">
        <col class="col-source">
        <col class="col-checked">
        <col class="col-support">
        <col class="col-latest">
        <col class="col-recommended">
        <col class="col-confidence">
        <col class="col-module">
      </colgroup>
      <thead>
        <tr>
{headers}
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</section>
"""


def _render_finding_row(finding: Finding) -> str:
    """Показывает строку найденного случая."""

    file_path = finding.location.file_path if finding.location else ""
    line = str(finding.location.line_start or "") if finding.location else ""
    checked_at = finding.checked_at.isoformat() if finding.checked_at else ""
    explanation = format_finding_explanation_html(finding, _esc)
    fragment = format_finding_fragment(finding)
    return f"""
<tr class="frow" data-criterion="{finding.criterion.value}" data-kind="{finding.issue_kind.value}" data-verdict="{finding.verdict.value}" data-severity="{finding.severity.value}">
  <td>{_esc(CRITERION_LABELS[finding.criterion])}</td>
  <td>{_esc(ISSUE_KIND_LABELS[finding.issue_kind])}</td>
  <td>{_pill(VERDICT_LABELS[finding.verdict], f"pill-{finding.verdict.value}")}</td>
  <td>{_pill(SEVERITY_LABELS[finding.severity], f"pill-{finding.severity.value}")}</td>
  <td class="mono">{_esc(file_path)}</td>
  <td class="mono">{_esc(line)}</td>
  <td class="fragment">{_esc(fragment)}</td>
  <td class="evidence">{explanation}</td>
  <td class="source">{_esc(finding.source or "")}</td>
  <td class="mono">{_esc(checked_at)}</td>
  <td>{_esc(finding.support_status or "")}</td>
  <td class="mono">{_esc(finding.latest_version or "")}</td>
  <td class="mono">{_esc(finding.recommended_version or "")}</td>
  <td class="mono">{finding.confidence:.2f}</td>
  <td class="mono">{_esc(finding.checker_name)}</td>
</tr>
"""


def _render_downloads(report_dir: Path) -> str:
    """Показывает ссылки на файлы отчёта."""

    links = [
        ("XLSX", "report.xlsx"),
        ("CSV", "report.csv"),
        ("JSON", "report.json"),
    ]
    items = "\n".join(f'<a class="link-button" href="/download?file={quote(name)}">{label}</a>' for label, name in links)
    return f"""
<section class="section">
  <div class="section-head">
    <h2>Файлы отчёта</h2>
  </div>
  <div class="downloads">{items}</div>
</section>
"""


def _render_script() -> str:
    """Добавляет минимальное поведение формы."""

    return """
<script>
(() => {
const form = document.getElementById("run-form");
const progressPanel = document.getElementById("run-progress");
const progressFill = document.getElementById("run-progress-fill");
const progressPercent = document.getElementById("run-progress-percent");
const progressStage = document.getElementById("run-progress-stage");
const progressElapsed = document.getElementById("run-progress-elapsed");
const archiveInput = document.getElementById("project_archive");
const archiveFileName = document.getElementById("archive-file-name");
const archiveZone = archiveInput ? archiveInput.closest(".upload-zone") : null;
const progressStages = [
  [8, "Подготовка запуска"],
  [22, "Загрузка файлов"],
  [42, "Извлечение сущностей"],
  [62, "Проверка ссылок и файлов"],
  [82, "Проверка фактов и версий"],
  [96, "Сборка отчёта"],
  [100, "Отчёт готов"]
];
let progressTimer = null;
let progressStartedAt = 0;
let progressValue = 0;

function progressLabel(value) {
  for (const item of progressStages) {
    if (value <= item[0]) return item[1];
  }
  return "Сборка отчёта";
}

function setProgress(value, label) {
  progressValue = Math.max(0, Math.min(100, Math.round(value)));
  if (progressFill) progressFill.style.width = `${progressValue}%`;
  if (progressPercent) progressPercent.textContent = `${progressValue}%`;
  if (progressStage) progressStage.textContent = label || progressLabel(progressValue);
}

function startProgress() {
  if (!progressPanel) return;
  progressPanel.hidden = false;
  progressPanel.classList.remove("is-error");
  progressPanel.setAttribute("aria-busy", "true");
  progressStartedAt = Date.now();
  setProgress(3, "Подготовка запуска");
  if (progressTimer) window.clearInterval(progressTimer);
  progressTimer = window.setInterval(() => {
    const elapsedSeconds = Math.max(0, Math.floor((Date.now() - progressStartedAt) / 1000));
    const nextValue = Math.min(94, 3 + Math.log2(elapsedSeconds + 1) * 18);
    setProgress(nextValue);
    if (progressElapsed) progressElapsed.textContent = `${elapsedSeconds} с`;
  }, 700);
}

function stopProgress(value, label) {
  if (progressTimer) window.clearInterval(progressTimer);
  progressTimer = null;
  if (progressPanel) progressPanel.setAttribute("aria-busy", "false");
  setProgress(value, label);
}

if (form) {
  form.addEventListener("submit", async (event) => {
    if (form.dataset.submitting === "1") {
      event.preventDefault();
      return;
    }
    if (!window.fetch) return;
    event.preventDefault();
    form.dataset.submitting = "1";
    form.classList.add("loading");
    const button = form.querySelector("button[type='submit']");
    if (button) {
      button.disabled = true;
      button.textContent = "Проверяю...";
    }
    startProgress();

    try {
      const payload = new FormData(form);
      const response = await fetch(form.action, {
        method: "POST",
        body: payload
      });
      const html = await response.text();
      stopProgress(100, response.ok ? "Отчёт готов" : "Проверка завершилась с ошибкой");
      window.setTimeout(() => {
        document.open();
        document.write(html);
        document.close();
      }, 250);
    } catch (error) {
      stopProgress(progressValue, "Не удалось получить ответ");
      if (progressPanel) progressPanel.classList.add("is-error");
      form.classList.remove("loading");
      delete form.dataset.submitting;
      if (button) {
        button.disabled = false;
        button.textContent = "Запустить";
      }
    }
  });
}

if (archiveInput && archiveFileName) {
  archiveInput.addEventListener("change", () => {
    const file = archiveInput.files && archiveInput.files[0];
    archiveFileName.textContent = file ? file.name : "ZIP / RAR / TAR";
  });
}

if (archiveZone) {
  archiveZone.addEventListener("dragenter", () => archiveZone.classList.add("is-dragging"));
  archiveZone.addEventListener("dragleave", () => archiveZone.classList.remove("is-dragging"));
  archiveZone.addEventListener("drop", () => archiveZone.classList.remove("is-dragging"));
}

const restart = document.querySelector(".run-restart");
if (restart && form) {
  const submitCurrentForm = (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (form.requestSubmit) form.requestSubmit();
    else form.submit();
  };
  restart.addEventListener("click", submitCurrentForm);
  restart.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") submitCurrentForm(event);
  });
}

const diagnostics = document.querySelector(".diagnostics");
if (diagnostics) diagnostics.removeAttribute("open");

const table = document.getElementById("findings-table");
const hideUnknown = document.getElementById("flt-hide-unknown");
const criterionButtons = document.querySelectorAll("[data-criterion-filter]");
const severityButtons = document.querySelectorAll("[data-severity-filter]");
const activeCriterionLabel = document.getElementById("active-criterion-label");
const activeSeverityLabel = document.getElementById("active-severity-label");
const activeColumnFilterLabel = document.getElementById("active-column-filter-label");
const resultCount = document.getElementById("filter-result-count");
const columnFilterButtons = table ? Array.from(table.querySelectorAll("[data-column-filter]")) : [];
const columnFilterState = new Map();
let activeCriterion = "all";
let activeSeverity = "all";
let activeColumnMenu = null;

function rowValue(row, columnIndex) {
  const cell = row.cells[columnIndex];
  if (!cell) return "";
  return cell.textContent.replace(/\\s+/g, " ").trim();
}

function valueLabel(value) {
  return value || "Пусто";
}

function sortedColumnValues(columnIndex) {
  if (!table) return [];
  const values = new Set();
  table.querySelectorAll("tbody tr.frow").forEach((row) => values.add(rowValue(row, columnIndex)));
  return Array.from(values).sort((left, right) => valueLabel(left).localeCompare(valueLabel(right), "ru"));
}

function closeColumnMenu() {
  if (activeColumnMenu) activeColumnMenu.remove();
  activeColumnMenu = null;
  columnFilterButtons.forEach((button) => button.setAttribute("aria-expanded", "false"));
}

function updateColumnFilterState(columnIndex, values, checkedValues) {
  if (checkedValues.length === values.length) {
    columnFilterState.delete(columnIndex);
  } else {
    columnFilterState.set(columnIndex, new Set(checkedValues));
  }
  columnFilterButtons.forEach((button, index) => {
    button.classList.toggle("is-active", columnFilterState.has(index));
  });
  if (activeColumnFilterLabel) {
    const activeCount = columnFilterState.size;
    activeColumnFilterLabel.textContent = activeCount ? `Колонки: ${activeCount}` : "Колонки: нет";
  }
  applyFilters();
}

function buildColumnMenu(button, columnIndex) {
  closeColumnMenu();
  const values = sortedColumnValues(columnIndex);
  const selected = columnFilterState.get(columnIndex);
  const menu = document.createElement("div");
  menu.className = "column-filter-menu";
  menu.setAttribute("role", "dialog");

  const head = document.createElement("div");
  head.className = "column-filter-head";
  const title = document.createElement("span");
  title.textContent = button.dataset.columnLabel || "Колонка";
  const clear = document.createElement("button");
  clear.type = "button";
  clear.className = "column-filter-clear";
  clear.textContent = "Сбросить";
  clear.addEventListener("click", (event) => {
    event.stopPropagation();
    columnFilterState.delete(columnIndex);
    closeColumnMenu();
    columnFilterButtons.forEach((item, index) => item.classList.toggle("is-active", columnFilterState.has(index)));
    if (activeColumnFilterLabel) {
      const activeCount = columnFilterState.size;
      activeColumnFilterLabel.textContent = activeCount ? `Колонки: ${activeCount}` : "Колонки: нет";
    }
    applyFilters();
  });
  head.append(title, clear);
  menu.append(head);

  const list = document.createElement("div");
  list.className = "column-filter-options";
  if (values.length === 0) {
    const empty = document.createElement("div");
    empty.className = "column-filter-empty";
    empty.textContent = "Нет значений";
    list.append(empty);
  } else {
    values.forEach((value) => {
      const option = document.createElement("label");
      option.className = "column-filter-option";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = value;
      checkbox.checked = selected ? selected.has(value) : true;
      const caption = document.createElement("span");
      caption.className = "column-filter-value";
      caption.textContent = valueLabel(value);
      checkbox.addEventListener("change", () => {
        const checkedValues = Array.from(list.querySelectorAll("input[type='checkbox']:checked")).map((item) => item.value);
        updateColumnFilterState(columnIndex, values, checkedValues);
      });
      option.append(checkbox, caption);
      list.append(option);
    });
  }
  menu.append(list);

  document.body.append(menu);
  const rect = button.getBoundingClientRect();
  const left = Math.min(Math.max(12, rect.left), window.innerWidth - menu.offsetWidth - 12);
  menu.style.left = `${left}px`;
  menu.style.top = `${rect.bottom + 8}px`;
  button.setAttribute("aria-expanded", "true");
  activeColumnMenu = menu;
}

function updateEmptyState() {
  if (!table) return;
  const rows = table.querySelectorAll("tbody tr.frow");
  let visible = 0;
  rows.forEach((row) => {
    if (getComputedStyle(row).display !== "none") visible += 1;
  });
  const note = document.getElementById("no-match");
  if (note) note.style.display = rows.length > 0 && visible === 0 ? "" : "none";
  if (resultCount) resultCount.textContent = `видно: ${visible} из ${rows.length}`;
}

function applyFilters() {
  if (!table) return;
  table.classList.toggle("hide-unknown", !!(hideUnknown && hideUnknown.checked));
  const rows = table.querySelectorAll("tbody tr.frow");
  rows.forEach((row) => {
    const byCriterion = activeCriterion === "all" || row.dataset.criterion === activeCriterion;
    const bySeverity = activeSeverity === "all" || row.dataset.severity === activeSeverity;
    const byColumns = Array.from(columnFilterState.entries()).every(([columnIndex, selected]) => selected.has(rowValue(row, columnIndex)));
    row.style.display = byCriterion && bySeverity && byColumns ? "" : "none";
  });
  updateEmptyState();
}

criterionButtons.forEach((button) => {
  button.addEventListener("click", () => {
    activeCriterion = button.dataset.criterionFilter || "all";
    criterionButtons.forEach((item) => item.classList.toggle("is-active", item === button));
    if (activeCriterionLabel) {
      const label = button.dataset.criterionLabel || "все";
      activeCriterionLabel.textContent = `Критерий: ${label}`;
    }
    applyFilters();
    const findings = document.getElementById("findings");
    if (findings) findings.scrollIntoView({ behavior: "smooth", block: "start" });
  });
});

severityButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const nextSeverity = button.dataset.severityFilter || "all";
    activeSeverity = activeSeverity === nextSeverity ? "all" : nextSeverity;
    severityButtons.forEach((item) => item.classList.toggle("is-active", activeSeverity !== "all" && item === button));
    if (activeSeverityLabel) {
      const label = activeSeverity === "all" ? "все" : button.dataset.severityLabel || nextSeverity;
      activeSeverityLabel.textContent = `Критичность: ${label}`;
    }
    applyFilters();
    const findings = document.getElementById("findings");
    if (findings) findings.scrollIntoView({ behavior: "smooth", block: "start" });
  });
});

columnFilterButtons.forEach((button, columnIndex) => {
  button.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (button.getAttribute("aria-expanded") === "true") {
      closeColumnMenu();
      return;
    }
    buildColumnMenu(button, columnIndex);
  });
});

document.addEventListener("click", (event) => {
  if (activeColumnMenu && !activeColumnMenu.contains(event.target)) closeColumnMenu();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeColumnMenu();
});

window.addEventListener("resize", closeColumnMenu);
if (hideUnknown) hideUnknown.addEventListener("change", applyFilters);
applyFilters();
})();
</script>
"""


def _metric_rows(rows: list[tuple[str, object]]) -> str:
    """Рисует компактные строки без шкалы, когда важнее статус, а не объём."""

    if not rows:
        return '<div class="metric-empty">Нет данных.</div>'
    return "\n".join(
        f"""
<div class="metric-item">
  <div class="metric-name">{_esc(name)}</div>
  <div class="metric-value">{_esc(str(value))}</div>
</div>
"""
        for name, value in rows
    )


def _bars(values: dict[str, int | float], labels: dict[str, str], sort_by_count: bool = True) -> str:
    """Рисует горизонтальные полосы распределения."""

    if not values:
        return '<div class="muted">Нет данных.</div>'
    max_value = max(values.values()) or 1
    rows = []
    items = sorted(values.items(), key=lambda item: item[1], reverse=True) if sort_by_count else values.items()
    for key, count in items:
        width = 0 if count <= 0 else max(4, round(count / max_value * 100))
        rows.append(
            f"""
<div class="bar-row">
  <div class="bar-label">{_esc(labels.get(key, key))}</div>
  <div class="bar-track"><div class="bar-fill" style="width:{width}%"></div></div>
  <div class="bar-count">{count}</div>
</div>
"""
        )
    return "\n".join(rows)


def _pill(label: str, css_class: str) -> str:
    """Рисует статусную метку."""

    return f'<span class="pill {css_class}">{_esc(label)}</span>'


def _esc(value: str) -> str:
    """Экранирует текст для HTML."""

    return html.escape(unquote(value), quote=True)
