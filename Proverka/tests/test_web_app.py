from pathlib import Path
import zipfile

import pytest

from content_audit import web_app as web_app_module
from content_audit.domain import AuditReport, Evidence, Finding, RunSummary, Severity, TextLocation, Verdict, Criterion
from content_audit.web_app import (
    INTERNAL_ARCHIVE_NAME_FIELD,
    INTERNAL_ARCHIVE_PATH_FIELD,
    INTERNAL_UPLOAD_DIR_FIELD,
    WebState,
    _extract_archive,
    _extract_rar_archive,
    credentials_match,
    load_latest_report,
    render_login_page,
    render_page,
    run_from_form,
)


def test_render_page_contains_project_input(workspace_tmp_path: Path) -> None:
    state = WebState(default_input=workspace_tmp_path, report_dir=workspace_tmp_path / "reports", env_values={})

    html = render_page(None, state)

    assert "Проверка локального проекта" in html
    assert "Путь к проекту" in html
    assert str(workspace_tmp_path) in html
    assert html.count("<input") == 2
    assert 'name="input_path"' in html
    assert 'name="project_archive"' in html
    assert 'accept=".zip,.rar,.tar,.gz,.tgz,.bz2,.xz"' in html
    assert "Архив проекта" in html
    assert 'id="run-progress"' in html
    assert "Готовность отчёта" in html
    assert "Подготовка запуска" in html
    assert "ключ OpenRouter" not in html
    assert "Укажите папку проекта" not in html


def test_render_login_page_uses_static_avatar_and_fields() -> None:
    html = render_login_page("Неверный логин или пароль")

    assert "Авторизация" in html
    assert 'action="/login"' in html
    assert 'name="username"' in html
    assert 'name="password"' in html
    assert "/assets/avatar-placeholder.jpg" in html
    assert "Неверный логин или пароль" in html


def test_credentials_match_uses_env_values_without_rendering_secret(workspace_tmp_path: Path) -> None:
    state = WebState(
        default_input=workspace_tmp_path,
        report_dir=workspace_tmp_path / "reports",
        env_values={"AUTH_USERNAME": "auditor", "AUTH_PASSWORD": "secret-password"},
    )

    html = render_login_page()

    assert state.auth_enabled
    assert credentials_match("auditor", "secret-password", state)
    assert not credentials_match("auditor", "wrong", state)
    assert "secret-password" not in html


def test_credentials_match_accepts_unicode_values(workspace_tmp_path: Path) -> None:
    state = WebState(
        default_input=workspace_tmp_path,
        report_dir=workspace_tmp_path / "reports",
        env_values={"AUTH_USERNAME": "аудитор", "AUTH_PASSWORD": "пароль"},
    )

    assert credentials_match("аудитор", "пароль", state)
    assert not credentials_match("аудитор", "неверно", state)


def test_render_page_has_no_demo_project_by_default(workspace_tmp_path: Path) -> None:
    state = WebState(default_input=None, report_dir=workspace_tmp_path / "reports", env_values={})

    html = render_page(None, state)

    assert "proj_example" not in html
    assert 'id="input_path" name="input_path" type="text" value=""' in html


def test_render_page_contains_extended_report_columns(workspace_tmp_path: Path) -> None:
    state = WebState(default_input=workspace_tmp_path, report_dir=workspace_tmp_path / "reports", env_values={})
    report = AuditReport(
        summary=RunSummary(
            started_at="2026-06-08T00:00:00+00:00",
            input_path=str(workspace_tmp_path),
            units_total=0,
            files_total=0,
            findings_total=1,
        ),
        units=[],
        entities=[],
        findings=[
            Finding(
                finding_id="fnd_test",
                unit_id="unit",
                branch=None,
                criterion=Criterion.ACTUALITY,
                severity=Severity.INFO,
                verdict=Verdict.UNKNOWN,
                confidence=0.5,
                quote="Use Java 21.",
                location=TextLocation(file_path="README.md", line_start=3),
                evidence=[Evidence(title="Проверка", detail="Технологию нужно сверить с актуальными источниками")],
                recommendation="Проверить вручную.",
                needs_human_review=True,
                checker_name="tech_freshness_checker",
            )
        ],
    )

    html = render_page(report, state)

    assert "Источник" in html
    assert "Статус поддержки" in html
    assert "Фрагмент" in html
    assert "Цитата" not in html
    assert 'data-column-filter="recommendation"' not in html
    assert "Что найдено:" in html
    assert "Что сделать:" in html
    assert "Проверить вручную." in html
    assert "Info" in html
    assert "Critical / Major" in html
    assert "Критерий — фильтр таблицы" in html
    assert "Диагностика прогона" in html
    assert "Покрытие ТЗ" in html
    assert "Свежих вызовов моделей нет" in html
    assert "случаев" in html
    assert 'data-criterion-filter="all"' in html
    assert 'data-criterion-filter="actuality"' in html
    assert 'data-criterion="actuality"' in html
    assert 'data-severity-filter="critical"' in html
    assert 'data-severity-filter="major"' in html
    assert 'data-severity-filter="minor"' in html
    assert 'data-severity-filter="info"' in html
    assert 'id="active-criterion-label"' in html
    assert 'id="active-severity-label"' in html
    assert 'id="active-column-filter-label"' in html
    assert 'id="filter-result-count"' in html
    assert 'data-column-filter="criterion"' in html
    assert 'data-column-filter="severity"' in html
    assert "columnFilterState" in html
    assert "activeSeverity" in html
    assert "мгновенно, без перезапуска" not in html
    assert "/download?file=report.xlsx" in html
    assert "/download?file=report.csv" in html
    assert "/download?file=report.json" in html
    assert "/download?file=run_summary.json" not in html
    topbar = html.split('<header class="topbar">', 1)[1].split("</header>", 1)[0]
    assert 'href="#findings"' not in topbar
    assert "Таблица" not in topbar
    summary_block = html.split('class="summary-strip"', 1)[1]
    assert summary_block.index("critical") < summary_block.index("major")
    assert summary_block.index("major") < summary_block.index("minor")
    assert summary_block.index("minor") < summary_block.index("info")
    assert 'id="flt-hide-unknown"' in html
    assert 'name="hide_unknown"' not in html
    assert 'data-verdict="unknown"' in html
    assert 'class="findings"' in html
    assert "Показывать успешные" not in html
    assert "успешных" not in html
    assert "Перезапустить" in html
    assert "fetch(form.action" in html
    assert "Отчёт готов" in html


def test_run_from_form_excludes_pass_findings_from_report(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("## Part 1. Работа с cat\n", encoding="utf-8")
    (project / "README_RUS.md").write_text("# Проект\n", encoding="utf-8")
    (project / "check-list.yml").write_text(
        "sections:\n"
        "  - questions:\n"
        "      - name: Part_1.CAT\n",
        encoding="utf-8",
    )
    report_dir = workspace_tmp_path / "reports"
    state = WebState(default_input=project, report_dir=report_dir, env_values={})

    report = run_from_form({"input_path": str(project)}, state)
    persisted = load_latest_report(report_dir)
    csv_text = (report_dir / "report.csv").read_text(encoding="utf-8-sig")

    assert all(finding.verdict != Verdict.PASS for finding in report.findings)
    assert persisted is not None
    assert all(finding.verdict != Verdict.PASS for finding in persisted.findings)
    assert "Проверено" not in csv_text


def test_run_from_form_always_enables_models_and_network(workspace_tmp_path: Path, monkeypatch) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    captured = {}

    class _FakeRunner:
        def __init__(self, settings):
            captured["settings"] = settings

        def run(self):
            return AuditReport(
                summary=RunSummary(started_at="2026-06-08T00:00:00+00:00", input_path=str(project)),
                units=[],
                entities=[],
                findings=[],
            )

    monkeypatch.setattr("content_audit.web_app.AuditRunner", _FakeRunner)
    state = WebState(
        default_input=None,
        report_dir=workspace_tmp_path / "reports",
        env_values={
            "OPENROUTER_API_KEY": "key",
            "OPENROUTER_MODEL": "openai/general",
            "OPENROUTER_TECH_MODEL": "qwen/tech",
            "OPENROUTER_FACT_MODEL": "perplexity/facts",
        },
    )

    run_from_form(
        {
            "input_path": str(project),
            "model_name": "perplexity/sonar",
        },
        state,
    )

    assert captured["settings"].use_model is True
    assert captured["settings"].allow_network is True
    assert captured["settings"].openrouter_model == "openai/general"
    assert captured["settings"].openrouter_tech_model == "qwen/tech"
    assert captured["settings"].openrouter_fact_model == "perplexity/facts"


def test_run_from_form_extracts_archive_and_removes_temporary_files(workspace_tmp_path: Path, monkeypatch) -> None:
    upload_dir = workspace_tmp_path / "upload"
    upload_dir.mkdir()
    archive_path = upload_dir / "project.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("project/README.md", "# Проект\n")

    captured = {}

    class _FakeRunner:
        def __init__(self, settings):
            captured["settings"] = settings
            captured["input_path"] = settings.input_path

        def run(self):
            assert (captured["input_path"] / "README.md").exists()
            return AuditReport(
                summary=RunSummary(started_at="2026-06-08T00:00:00+00:00", input_path=str(captured["input_path"])),
                units=[],
                entities=[],
                findings=[],
            )

    monkeypatch.setattr("content_audit.web_app.AuditRunner", _FakeRunner)
    state = WebState(default_input=None, report_dir=workspace_tmp_path / "reports", env_values={"OPENROUTER_API_KEY": "key"})

    report = run_from_form(
        {
            INTERNAL_ARCHIVE_PATH_FIELD: str(archive_path),
            INTERNAL_ARCHIVE_NAME_FIELD: archive_path.name,
            INTERNAL_UPLOAD_DIR_FIELD: str(upload_dir),
        },
        state,
    )

    assert report.summary.input_path == "Архив: project.zip"
    assert captured["settings"].input_path.name == "project"
    assert not upload_dir.exists()
    assert not captured["input_path"].exists()


def test_extract_archive_accepts_rar_extension(workspace_tmp_path: Path, monkeypatch) -> None:
    archive_path = workspace_tmp_path / "project.rar"
    archive_path.write_bytes(b"rar")
    target_dir = workspace_tmp_path / "extracted"

    def fake_extract_rar(path: Path, target: Path) -> None:
        assert path == archive_path
        (target / "project").mkdir(parents=True)
        (target / "project" / "README.md").write_text("# Проект\n", encoding="utf-8")

    monkeypatch.setattr(web_app_module, "_extract_rar_archive", fake_extract_rar)

    _extract_archive(archive_path, target_dir)

    assert (target_dir / "project" / "README.md").exists()


def test_extract_rar_requires_external_tool(workspace_tmp_path: Path, monkeypatch) -> None:
    archive_path = workspace_tmp_path / "project.rar"
    archive_path.write_bytes(b"rar")
    monkeypatch.setattr(web_app_module, "_find_rar_tools", lambda: [])

    with pytest.raises(ValueError, match="Для RAR нужен"):
        _extract_rar_archive(archive_path, workspace_tmp_path / "extracted")


def test_extract_rar_uses_7z_listing_and_safe_paths(workspace_tmp_path: Path, monkeypatch) -> None:
    archive_path = workspace_tmp_path / "project.rar"
    archive_path.write_bytes(b"rar")
    target_dir = workspace_tmp_path / "extracted"
    commands: list[list[str]] = []

    class _Result:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command, capture_output, text, timeout):
        del capture_output, text, timeout
        commands.append(command)
        if command[:3] == ["7z", "l", "-slt"]:
            return _Result(0, "----------\nPath = project/README.md\nSize = 10\n")
        (target_dir / "project").mkdir(parents=True)
        (target_dir / "project" / "README.md").write_text("# Проект\n", encoding="utf-8")
        return _Result(0)

    monkeypatch.setattr(web_app_module, "_find_rar_tools", lambda: ["7z"])
    monkeypatch.setattr(web_app_module.subprocess, "run", fake_run)

    _extract_rar_archive(archive_path, target_dir)

    assert commands[0][:3] == ["7z", "l", "-slt"]
    assert commands[1][0:3] == ["7z", "x", "-y"]
    assert (target_dir / "project" / "README.md").exists()


def test_extract_rar_falls_back_after_unsupported_7z_method(workspace_tmp_path: Path, monkeypatch) -> None:
    archive_path = workspace_tmp_path / "project.rar"
    archive_path.write_bytes(b"rar")
    target_dir = workspace_tmp_path / "extracted"
    commands: list[list[str]] = []

    class _Result:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command, capture_output, text, timeout):
        del capture_output, text, timeout
        commands.append(command)
        if command[:3] == ["7z", "l", "-slt"]:
            return _Result(0, "----------\nPath = project/README.md\nSize = 10\n")
        if command[0:3] == ["7z", "x", "-y"]:
            (target_dir / "partial").mkdir(parents=True)
            return _Result(2, stderr="ERROR: Unsupported Method : project/README.md")
        if command[:2] == ["unrar", "lb"]:
            return _Result(0, "project/README.md\n")
        (target_dir / "project").mkdir(parents=True)
        (target_dir / "project" / "README.md").write_text("# Проект\n", encoding="utf-8")
        return _Result(0)

    monkeypatch.setattr(web_app_module, "_find_rar_tools", lambda: ["7z", "unrar"])
    monkeypatch.setattr(web_app_module.subprocess, "run", fake_run)

    _extract_rar_archive(archive_path, target_dir)

    assert ["unrar", "lb", str(archive_path)] in commands
    assert not (target_dir / "partial").exists()
    assert (target_dir / "project" / "README.md").exists()


def test_extract_rar_supports_unar_lsar_json(workspace_tmp_path: Path, monkeypatch) -> None:
    archive_path = workspace_tmp_path / "project.rar"
    archive_path.write_bytes(b"rar")
    target_dir = workspace_tmp_path / "extracted"
    commands: list[list[str]] = []

    class _Result:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command, capture_output, text, timeout):
        del capture_output, text, timeout
        commands.append(command)
        if command[:2] == ["lsar", "-json"]:
            return _Result(0, '{"lsarContents":[{"XADFileName":"project/README.md"}]}')
        (target_dir / "project").mkdir(parents=True)
        (target_dir / "project" / "README.md").write_text("# Проект\n", encoding="utf-8")
        return _Result(0)

    monkeypatch.setattr(web_app_module, "_find_rar_tools", lambda: ["unar"])
    monkeypatch.setattr(web_app_module.subprocess, "run", fake_run)

    _extract_rar_archive(archive_path, target_dir)

    assert commands[0] == ["lsar", "-json", str(archive_path)]
    assert commands[1][:4] == ["unar", "-f", "-D", "-o"]
    assert (target_dir / "project" / "README.md").exists()
