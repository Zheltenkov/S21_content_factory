"""Branch auditor route: summary rendering, drill-down, and skip reporting — no LLM/engine."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from content_factory.api.routers import auditor
from content_factory.audit.branch import BranchProjectResult, BranchReport, BranchSkip


def _fake_branch_report() -> BranchReport:
    return BranchReport(
        projects=[
            BranchProjectResult(
                name="project-a.zip",
                status="completed",
                summary={"findings_total": 0, "by_severity": {}},
                report_dir="/out/0",
            ),
            BranchProjectResult(
                name="project-b.zip",
                status="completed",
                summary={"findings_total": 3, "by_severity": {"major": 2, "minor": 1}},
                report_dir="/out/1",
            ),
            BranchProjectResult(name="broken.zip", status="failed", error="движок упал"),
        ],
        skipped=[
            BranchSkip(name="notes.docx", reason="loose_file", message="Отдельный файл, не архив проекта — пропущено."),
            BranchSkip(name="old.zip", reason="no_readme", message="Нет README.md — не распознано как проект, пропущено."),
        ],
    )


def test_project_verdict_maps_findings_and_severity() -> None:
    assert auditor._project_verdict({"findings_total": 0, "by_severity": {}})[0] == "✓ чисто"
    verdict, findings, severity = auditor._project_verdict({"findings_total": 3, "by_severity": {"major": 2, "minor": 1}})
    assert verdict == "⚠ 3"
    assert findings == 3
    assert "major: 2" in severity
    assert auditor._project_verdict(None) == ("—", 0, "")


def test_summary_renders_rows_drilldown_links_and_skips() -> None:
    html = auditor._render_branch_summary("BRANCH1", _fake_branch_report())

    # totals
    assert "Проверено проектов: <strong>2</strong>" in html
    assert "с ошибкой: <strong>1</strong>" in html
    assert "пропущено: <strong>2</strong>" in html
    # a clean project and a project with findings
    assert "project-a.zip" in html and "✓ чисто" in html
    assert "project-b.zip" in html and "major: 2" in html
    # drill-down links point at the right indices
    assert '/app/auditor/branch/BRANCH1/project/0' in html
    assert '/app/auditor/branch/BRANCH1/project/1' in html
    # a failed project shows its error, not a drill-down link
    assert "broken.zip" in html and "движок упал" in html
    # skip section with reasons
    assert "Пропущено (2)" in html
    assert "notes.docx" in html and "не архив" in html


def test_summary_escapes_project_names() -> None:
    report = BranchReport(
        projects=[BranchProjectResult(name="<script>x</script>.zip", status="failed", error="e")],
        skipped=[],
    )
    html = auditor._render_branch_summary("B", report)
    assert "<script>x</script>.zip" not in html
    assert "&lt;script&gt;" in html


@pytest.fixture
def branch_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DISABLE_AUTH", "true")  # validate_request_user -> dev admin
    app = FastAPI()
    app.include_router(auditor.page_router)
    return TestClient(app)


def _inner_project_zip(name: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"{name}/README.md", f"# {name}")
        archive.writestr(f"{name}/main.py", "x = 1")
    return buffer.getvalue()


def _branch_zip(names: tuple[str, ...] = ("p1", "p2")) -> bytes:
    """A branch archive whose top-level entries are project archives (each with a README)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(f"{name}.zip", _inner_project_zip(name))
    return buffer.getvalue()


class _SyncThread:
    """Stand-in for threading.Thread that runs the target inline, so the background branch job
    finishes before the POST returns and the test needs no real waiting."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None) -> None:
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self) -> None:
        if self._target is not None:
            self._target(*self._args, **self._kwargs)


class _FakeSummary:
    def model_dump(self, mode: str = "json") -> dict:
        return {"findings_total": 0, "by_severity": {}}


class _FakeReport:
    summary = _FakeSummary()


@pytest.fixture
def sync_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the branch job inline with a fake single-project engine (no LLM/network)."""
    monkeypatch.setattr(auditor, "Thread", _SyncThread)
    monkeypatch.setattr(auditor, "_run_single_project_audit", lambda project_dir, out_dir, **k: _FakeReport())


def _upload_branch(client: TestClient, names: tuple[str, ...] = ("p1", "p2")) -> str:
    uploaded = client.post(
        "/app/auditor/branch/upload",
        files={"branch_archive": ("branch.zip", _branch_zip(names), "application/zip")},
    )
    assert uploaded.status_code == 200
    return uploaded.json()["upload_id"]


def _post_branch(client: TestClient, names: tuple[str, ...] = ("p1", "p2")) -> str:
    # Two-step flow: the archive is uploaded on file-select, the run starts from its upload_id.
    upload_id = _upload_branch(client, names)
    response = client.post("/app/auditor/branch", data={"upload_id": upload_id})
    assert response.status_code == 200
    progress_url = response.json()["progress_url"]  # run returns the stable progress URL, not HTML
    return progress_url.split("/app/auditor/branch/", 1)[1].split("/progress", 1)[0]


def test_post_branch_starts_a_job_and_processes_to_summary(branch_client: TestClient, sync_engine: None) -> None:
    branch_id = _post_branch(branch_client)

    status = branch_client.get(f"/app/auditor/branch/{branch_id}/status").json()
    assert status["done"] is True
    assert status["total"] == 2 and status["completed"] == 2 and status["failed"] == 0

    summary = branch_client.get(f"/app/auditor/branch/{branch_id}")
    assert summary.status_code == 200
    assert "p1.zip" in summary.text and "p2.zip" in summary.text


def test_branch_upload_requires_a_file(branch_client: TestClient) -> None:
    assert branch_client.post("/app/auditor/branch/upload").status_code == 400


def test_branch_upload_returns_an_id_the_run_consumes(branch_client: TestClient, sync_engine: None) -> None:
    upload_id = _upload_branch(branch_client)
    assert upload_id
    # the run starts from the upload_id; a second run with the same (now consumed) id fails
    assert branch_client.post("/app/auditor/branch", data={"upload_id": upload_id}).status_code == 200
    assert branch_client.post("/app/auditor/branch", data={"upload_id": upload_id}).status_code == 400


def test_run_without_a_known_upload_id_is_rejected(branch_client: TestClient) -> None:
    assert branch_client.post("/app/auditor/branch", data={"upload_id": "nope"}).status_code == 400
    assert branch_client.post("/app/auditor/branch").status_code == 400


def test_progress_page_redirects_to_summary_when_finished(branch_client: TestClient, sync_engine: None) -> None:
    branch_id = _post_branch(branch_client)  # sync engine -> job already done
    redirected = branch_client.get(f"/app/auditor/branch/{branch_id}/progress", follow_redirects=False)
    assert redirected.status_code == 303
    assert redirected.headers["location"] == f"/app/auditor/branch/{branch_id}"


def test_progress_page_shows_the_bar_while_running(branch_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # A thread that never runs leaves the job in the "running" state so /progress renders the bar.
    class _NoopThread:
        def __init__(self, *a, **k) -> None: ...
        def start(self) -> None: ...

    monkeypatch.setattr(auditor, "Thread", _NoopThread)
    branch_id = _post_branch(branch_client)
    page = branch_client.get(f"/app/auditor/branch/{branch_id}/progress")
    assert page.status_code == 200
    assert "bar-fill" in page.text and "Обработка ветки" in page.text
    # an unknown branch shows the "gone" page (HTML), not a raw 404
    gone = branch_client.get("/app/auditor/branch/nope/progress")
    assert gone.status_code == 200 and "Прогон не найден или прерван" in gone.text


def test_progress_page_for_unknown_branch_renders_html_not_raw_json(branch_client: TestClient) -> None:
    resp = branch_client.get("/app/auditor/branch/does-not-exist/progress")
    assert resp.status_code == 200  # a real page, not a bare 404 JSON body
    assert "Прогон не найден или прерван" in resp.text
    assert "Вернуться к аудитору" in resp.text


def test_reload_marks_a_cut_off_job_as_interrupted(
    monkeypatch: pytest.MonkeyPatch, branch_client: TestClient, tmp_path
) -> None:
    monkeypatch.setattr(auditor, "AUDITOR_ROOT", tmp_path)
    work_dir = tmp_path / "ui" / "u" / "branch_y"
    work_dir.mkdir(parents=True)
    (work_dir / "branch_job.json").write_text(
        json.dumps(
            {
                "branch_id": "CUT",
                "archive_name": "b.zip",
                "created_at": "2026-07-27T10:00:00",
                "job": {"state": "running", "total": 12, "completed": 3, "failed": 0, "current": "p4.zip"},
            }
        ),
        encoding="utf-8",
    )
    auditor._BRANCH_JOBS.pop("CUT", None)
    auditor._BRANCHES_LOADED = False
    try:
        status = branch_client.get("/app/auditor/branch/CUT/status").json()
        assert status["state"] == "interrupted"  # a restart-cut job is surfaced, not 404
        assert status["total"] == 12 and status["completed"] == 3
    finally:
        auditor._BRANCH_JOBS.pop("CUT", None)


def test_delete_branch_removes_the_run(branch_client: TestClient, sync_engine: None) -> None:
    branch_id = _post_branch(branch_client)
    assert branch_client.get(f"/app/auditor/branch/{branch_id}").status_code == 200

    deleted = branch_client.post(f"/app/auditor/branch/{branch_id}/delete", follow_redirects=False)
    assert deleted.status_code == 303
    assert deleted.headers["location"] == "/app/auditor"
    assert branch_client.get(f"/app/auditor/branch/{branch_id}").status_code == 404


def test_branch_status_reports_progress_fields_and_404s_for_unknown(
    branch_client: TestClient, sync_engine: None
) -> None:
    branch_id = _post_branch(branch_client, ("only",))
    body = branch_client.get(f"/app/auditor/branch/{branch_id}/status").json()
    assert set(body) >= {"state", "total", "completed", "failed", "current", "done", "summary_url"}
    assert body["summary_url"] == f"/app/auditor/branch/{branch_id}"
    assert branch_client.get("/app/auditor/branch/nope/status").status_code == 404


def test_branch_full_audit_flags_reach_the_engine(
    branch_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}

    def fake_audit(project_dir, out_dir, **kwargs):
        captured.update(kwargs)
        return _FakeReport()

    monkeypatch.setattr(auditor, "Thread", _SyncThread)
    monkeypatch.setattr(auditor, "_run_single_project_audit", fake_audit)
    # no depth toggles are sent — the route defaults to a full audit (model + network on)
    _post_branch(branch_client, ("only",))
    assert captured["use_model"] is True and captured["allow_network"] is True


def test_drilldown_renders_stored_report_and_404s_out_of_range(
    branch_client: TestClient, sync_engine: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeWebApp:
        @staticmethod
        def render_page(report_obj, state, form_values=None):
            return "<html>FULL_PROJECT_REPORT</html>"

    monkeypatch.setattr(auditor, "_load_auditor_web_app", lambda: _FakeWebApp())

    branch_id = _post_branch(branch_client)

    ok = branch_client.get(f"/app/auditor/branch/{branch_id}/project/0")
    assert ok.status_code == 200
    assert "FULL_PROJECT_REPORT" in ok.text
    assert "Вернуться к ветке" in ok.text

    assert branch_client.get(f"/app/auditor/branch/{branch_id}/project/99").status_code == 404
    assert branch_client.get("/app/auditor/branch/nope/project/0").status_code == 404


def test_drilldown_back_link_sits_in_the_run_bar_actions(
    branch_client: TestClient, sync_engine: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeWebApp:
        @staticmethod
        def render_page(report_obj, state, form_values=None):
            return (
                '<summary class="run-bar"><span class="run-bar-text">Проект</span>'
                '<span class="run-bar-actions"><button>Перезапустить</button></span></summary>'
                "<h2>Таблица результата</h2><table></table>"
            )

    monkeypatch.setattr(auditor, "_load_auditor_web_app", lambda: _FakeWebApp())

    branch_id = _post_branch(branch_client)
    text = branch_client.get(f"/app/auditor/branch/{branch_id}/project/0").text
    # the back link sits in the top action bar, before Перезапустить (not down by the table)
    assert "Вернуться к ветке" in text
    assert text.index("run-bar-actions") < text.index("Вернуться к ветке") < text.index("Перезапустить")


def test_drilldown_back_link_falls_back_to_results_heading_without_action_bar(
    branch_client: TestClient, sync_engine: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeWebApp:
        @staticmethod
        def render_page(report_obj, state, form_values=None):
            return "<body><h1>Проект</h1><h2>Таблица результата</h2><table></table></body>"

    monkeypatch.setattr(auditor, "_load_auditor_web_app", lambda: _FakeWebApp())

    branch_id = _post_branch(branch_client)
    text = branch_client.get(f"/app/auditor/branch/{branch_id}/project/0").text
    assert text.index("Таблица результата") < text.index("Вернуться к ветке") < text.index("<table>")


def test_summary_get_reloads_a_run(branch_client: TestClient, sync_engine: None) -> None:
    branch_id = _post_branch(branch_client)

    reloaded = branch_client.get(f"/app/auditor/branch/{branch_id}")
    assert reloaded.status_code == 200
    assert "p1.zip" in reloaded.text
    assert branch_client.get("/app/auditor/branch/does-not-exist").status_code == 404


def test_branch_summary_survives_a_simulated_restart(branch_client: TestClient, sync_engine: None) -> None:
    branch_id = _post_branch(branch_client)
    assert branch_client.get(f"/app/auditor/branch/{branch_id}").status_code == 200

    # Simulate a process restart: drop the in-memory index and the one-shot load flag.
    auditor._BRANCH_RUNS.pop(branch_id, None)
    auditor._BRANCH_REPORTS.pop(branch_id, None)
    auditor._BRANCH_CREATED.pop(branch_id, None)
    auditor._BRANCHES_LOADED = False

    reloaded = branch_client.get(f"/app/auditor/branch/{branch_id}")
    assert reloaded.status_code == 200  # re-indexed from branch_meta.json on disk
    assert "p1.zip" in reloaded.text


def test_load_persisted_branches_reindexes_from_disk(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(auditor, "AUDITOR_ROOT", tmp_path)
    work_dir = tmp_path / "ui" / "u" / "branch_x"
    work_dir.mkdir(parents=True)
    report = BranchReport(
        projects=[BranchProjectResult(name="p1.zip", status="completed", summary={"findings_total": 0}, report_dir=str(work_dir / "p1"))],
        skipped=[],
    )
    (work_dir / "branch_meta.json").write_text(
        json.dumps({"branch_id": "BR1", "created_at": "2026-07-27T10:00:00", "archive_name": "b.zip", "report": report.as_dict()}),
        encoding="utf-8",
    )
    auditor._BRANCH_RUNS.pop("BR1", None)
    try:
        auditor._load_persisted_branches()
        assert "BR1" in auditor._BRANCH_RUNS
        assert auditor._BRANCH_RUNS["BR1"].projects[0].name == "p1.zip"
        assert auditor._BRANCH_CREATED["BR1"]["archive_name"] == "b.zip"
    finally:
        auditor._BRANCH_RUNS.pop("BR1", None)
        auditor._BRANCH_CREATED.pop("BR1", None)


def test_render_recent_branches_lists_newest_first() -> None:
    saved_runs = dict(auditor._BRANCH_RUNS)
    saved_created = dict(auditor._BRANCH_CREATED)
    auditor._BRANCH_RUNS.clear()
    auditor._BRANCH_CREATED.clear()
    try:
        for bid, name, ts in [("a", "old.zip", "2026-01-01T00:00"), ("b", "new.zip", "2026-07-01T00:00")]:
            auditor._BRANCH_RUNS[bid] = BranchReport(
                projects=[BranchProjectResult(name="p", status="completed", summary={}, report_dir="/x")], skipped=[]
            )
            auditor._BRANCH_CREATED[bid] = {"created_at": ts, "archive_name": name}
        html = auditor._render_recent_branches()
        assert "Недавние ветки" in html
        assert html.index("new.zip") < html.index("old.zip")  # newest first
        assert "/app/auditor/branch/b" in html
        assert "/app/auditor/branch/b/delete" in html  # per-row delete control
    finally:
        auditor._BRANCH_RUNS.clear()
        auditor._BRANCH_RUNS.update(saved_runs)
        auditor._BRANCH_CREATED.clear()
        auditor._BRANCH_CREATED.update(saved_created)


def test_prune_branch_workdir_keeps_only_reports_and_meta(tmp_path) -> None:
    work_dir = tmp_path / "wd"
    work_dir.mkdir()
    (work_dir / "DS_Bootcamp.zip").write_bytes(b"x")  # raw upload
    (work_dir / "branch").mkdir()
    (work_dir / "branch" / "f").write_text("x", encoding="utf-8")
    (work_dir / "extract").mkdir()
    out_root = work_dir / "reports"
    (out_root / "p1").mkdir(parents=True)
    (out_root / "p1" / "report.json").write_text("{}", encoding="utf-8")
    (work_dir / "branch_meta.json").write_text("{}", encoding="utf-8")

    auditor._prune_branch_workdir(work_dir, out_root)

    assert not (work_dir / "DS_Bootcamp.zip").exists()  # raw archive dropped
    assert not (work_dir / "branch").exists()  # extracted branch dropped
    assert not (work_dir / "extract").exists()  # inner-archive scratch dropped
    assert (out_root / "p1" / "report.json").exists()  # reports kept (for drill-down)
    assert (work_dir / "branch_meta.json").exists()  # index kept (for reload)


def test_download_name_prefixes_the_project_for_branch_reports() -> None:
    from pathlib import Path

    # branch drill-down: report_dir is the project folder -> download named after the project
    assert (
        auditor._download_name(Path("/out/DSB6_MovieLens.ID_886515-master.zip"), "report.xlsx")
        == "DSB6_MovieLens.ID_886515-master_report.xlsx"
    )
    # single-run report dir is generic -> keep the plain filename
    assert auditor._download_name(Path("/x/reports"), "report.xlsx") == "report.xlsx"


def test_load_project_report_returns_none_without_artifact(tmp_path) -> None:
    assert auditor._load_project_report(BranchProjectResult(name="x", status="failed")) is None
    no_json = BranchProjectResult(name="x", status="completed", report_dir=str(tmp_path))
    assert auditor._load_project_report(no_json) is None
