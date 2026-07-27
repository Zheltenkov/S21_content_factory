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


def _tiny_branch_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("placeholder.txt", "x")  # extraction target; engine is monkeypatched
    return buffer.getvalue()


def test_post_branch_renders_summary(branch_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel_report = object()
    report = _fake_branch_report()

    def fake_run(branch_dir, extract_root, out_root, **kwargs):
        return report, {"/out/0": sentinel_report, "/out/1": sentinel_report}

    monkeypatch.setattr(auditor, "_run_branch_from_dir", fake_run)

    response = branch_client.post(
        "/app/auditor/branch",
        files={"branch_archive": ("branch.zip", _tiny_branch_zip(), "application/zip")},
    )

    assert response.status_code == 200
    assert "project-a.zip" in response.text
    assert "Пропущено (2)" in response.text


def test_post_branch_requires_a_file(branch_client: TestClient) -> None:
    assert branch_client.post("/app/auditor/branch").status_code == 400


def test_branch_always_runs_full_audit_with_model_and_network(
    branch_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}

    def fake_run(branch_dir, extract_root, out_root, **kwargs):
        captured.update(kwargs)
        return _fake_branch_report(), {}

    monkeypatch.setattr(auditor, "_run_branch_from_dir", fake_run)
    # no depth toggles are sent — the route defaults to a full audit (model + network on)
    branch_client.post(
        "/app/auditor/branch",
        files={"branch_archive": ("branch.zip", _tiny_branch_zip(), "application/zip")},
    )
    assert captured["use_model"] is True and captured["allow_network"] is True


def test_drilldown_renders_stored_report_and_404s_out_of_range(
    branch_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _fake_branch_report()
    sentinel_report = object()
    monkeypatch.setattr(
        auditor, "_run_branch_from_dir", lambda *a, **k: (report, {"/out/0": sentinel_report, "/out/1": sentinel_report})
    )

    class _FakeWebApp:
        @staticmethod
        def render_page(report_obj, state, form_values=None):
            assert report_obj is sentinel_report
            return "<html>FULL_PROJECT_REPORT</html>"

    monkeypatch.setattr(auditor, "_load_auditor_web_app", lambda: _FakeWebApp())

    created = branch_client.post(
        "/app/auditor/branch",
        files={"branch_archive": ("branch.zip", _tiny_branch_zip(), "application/zip")},
    )
    branch_id = created.text.split("/app/auditor/branch/", 1)[1].split("/project/", 1)[0]

    ok = branch_client.get(f"/app/auditor/branch/{branch_id}/project/0")
    assert ok.status_code == 200
    assert "FULL_PROJECT_REPORT" in ok.text
    assert "← к ветке" in ok.text

    assert branch_client.get(f"/app/auditor/branch/{branch_id}/project/99").status_code == 404
    assert branch_client.get("/app/auditor/branch/nope/project/0").status_code == 404


def test_drilldown_back_link_sits_under_the_results_heading(
    branch_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _fake_branch_report()
    monkeypatch.setattr(
        auditor, "_run_branch_from_dir", lambda *a, **k: (report, {"/out/0": object(), "/out/1": object()})
    )

    class _FakeWebApp:
        @staticmethod
        def render_page(report_obj, state, form_values=None):
            return "<body><h1>Проект</h1><h2>Таблица результата</h2><table></table></body>"

    monkeypatch.setattr(auditor, "_load_auditor_web_app", lambda: _FakeWebApp())

    created = branch_client.post(
        "/app/auditor/branch",
        files={"branch_archive": ("branch.zip", _tiny_branch_zip(), "application/zip")},
    )
    branch_id = created.text.split("/app/auditor/branch/", 1)[1].split("/project/", 1)[0]

    text = branch_client.get(f"/app/auditor/branch/{branch_id}/project/0").text
    # the back link is injected right after the results heading, not floated above the page
    assert text.index("Таблица результата") < text.index("← к ветке")
    assert text.index("← к ветке") < text.index("<table>")


def test_summary_get_reloads_a_run(branch_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    report = _fake_branch_report()
    monkeypatch.setattr(auditor, "_run_branch_from_dir", lambda *a, **k: (report, {"/out/0": object(), "/out/1": object()}))
    created = branch_client.post(
        "/app/auditor/branch",
        files={"branch_archive": ("branch.zip", _tiny_branch_zip(), "application/zip")},
    )
    branch_id = created.text.split("/app/auditor/branch/", 1)[1].split("/project/", 1)[0]

    reloaded = branch_client.get(f"/app/auditor/branch/{branch_id}")
    assert reloaded.status_code == 200
    assert "project-a.zip" in reloaded.text
    assert branch_client.get("/app/auditor/branch/does-not-exist").status_code == 404
