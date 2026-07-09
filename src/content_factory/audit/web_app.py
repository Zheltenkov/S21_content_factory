"""Локальный веб-интерфейс для запуска аудита и просмотра отчёта."""

from __future__ import annotations

import json
import re
import secrets
import shutil
import subprocess
import tarfile
import zipfile
from collections.abc import Iterable
from email import policy
from email.parser import BytesParser
from pathlib import Path

from content_factory.audit import web_rendering as _web_rendering
from content_factory.audit.domain import AuditReport, AuditSettings
from content_factory.audit.env import get_env_value
from content_factory.audit.exporters import write_report
from content_factory.audit.orchestrator import AuditRunner

DEFAULT_REPORT_DIR = Path("reports") / "ui_latest"
DEFAULT_MODEL = "openai/gpt-5.4-mini"
DEFAULT_FACT_MODEL = "perplexity/sonar"
DEFAULT_TECH_MODEL = "qwen/qwen3-coder"
AUTH_COOKIE_NAME = _web_rendering.AUTH_COOKIE_NAME
AVATAR_PATH = Path(__file__).resolve().parent / "avatar-placeholder.jpg"
AVATAR_ROUTE = _web_rendering.AVATAR_ROUTE
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
        if not isinstance(name, str):
            continue
        raw_payload = part.get_payload(decode=True)
        payload = raw_payload if isinstance(raw_payload, bytes) else b""
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
    """Delegates full page rendering to the UI module."""

    return _web_rendering.render_page(report, state, form_values)


def render_login_page(error: str = "") -> str:
    """Delegates login page rendering to the UI module."""

    return _web_rendering.render_login_page(error)


def credentials_match(username: str, password: str, state: WebState) -> bool:
    """Compares login and password with configured UI credentials."""

    return _web_rendering.credentials_match(username, password, state)


def _auth_cookie(token: str) -> str:
    """Formats a local audit UI session cookie."""

    return _web_rendering._auth_cookie(token)


def _clear_auth_cookie() -> str:
    """Formats a local audit UI session reset cookie."""

    return _web_rendering._clear_auth_cookie()


def _render_topbar() -> str:
    """Keeps the legacy private import used by static UI contract tests."""

    return _web_rendering._render_topbar()
