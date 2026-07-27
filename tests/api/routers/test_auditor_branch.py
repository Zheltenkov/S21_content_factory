"""Branch auditor route: summary rendering, drill-down, and skip reporting — no LLM/engine."""

from __future__ import annotations

import io
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


def _post_branch(client: TestClient, names: tuple[str, ...] = ("p1", "p2")) -> str:
    response = client.post(
        "/app/auditor/branch",
        files={"branch_archive": ("branch.zip", _branch_zip(names), "application/zip")},
    )
    assert response.status_code == 200
    assert "Обработка ветки" in response.text  # processing page, not the summary
    return response.text.split("/app/auditor/branch/", 1)[1].split("/status", 1)[0]


def test_post_branch_starts_a_job_and_processes_to_summary(branch_client: TestClient, sync_engine: None) -> None:
    branch_id = _post_branch(branch_client)

    status = branch_client.get(f"/app/auditor/branch/{branch_id}/status").json()
    assert status["done"] is True
    assert status["total"] == 2 and status["completed"] == 2 and status["failed"] == 0

    summary = branch_client.get(f"/app/auditor/branch/{branch_id}")
    assert summary.status_code == 200
    assert "p1.zip" in summary.text and "p2.zip" in summary.text


def test_post_branch_requires_a_file(branch_client: TestClient) -> None:
    assert branch_client.post("/app/auditor/branch").status_code == 400


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
    assert "← к ветке" in ok.text

    assert branch_client.get(f"/app/auditor/branch/{branch_id}/project/99").status_code == 404
    assert branch_client.get("/app/auditor/branch/nope/project/0").status_code == 404


def test_drilldown_back_link_sits_under_the_results_heading(
    branch_client: TestClient, sync_engine: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeWebApp:
        @staticmethod
        def render_page(report_obj, state, form_values=None):
            return "<body><h1>Проект</h1><h2>Таблица результата</h2><table></table></body>"

    monkeypatch.setattr(auditor, "_load_auditor_web_app", lambda: _FakeWebApp())

    branch_id = _post_branch(branch_client)
    text = branch_client.get(f"/app/auditor/branch/{branch_id}/project/0").text
    # the back link is injected right after the results heading, not floated above the page
    assert text.index("Таблица результата") < text.index("← к ветке")
    assert text.index("← к ветке") < text.index("<table>")


def test_summary_get_reloads_a_run(branch_client: TestClient, sync_engine: None) -> None:
    branch_id = _post_branch(branch_client)

    reloaded = branch_client.get(f"/app/auditor/branch/{branch_id}")
    assert reloaded.status_code == 200
    assert "p1.zip" in reloaded.text
    assert branch_client.get("/app/auditor/branch/does-not-exist").status_code == 404
