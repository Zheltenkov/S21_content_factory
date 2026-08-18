"""Single-project run persistence: recent-runs list, disk reload, eviction — no LLM/engine."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from content_factory.api.routers import auditor


class _FakeSummary:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def model_dump(self, mode: str = "json") -> dict:
        del mode
        return dict(self._payload)


class _FakeReport:
    def __init__(self, payload: dict) -> None:
        self.summary = _FakeSummary(payload)


@pytest.fixture(autouse=True)
def _isolate_runs(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Point the auditor root at a temp dir and start each test with an empty per-user store."""
    monkeypatch.setattr(auditor, "AUDITOR_ROOT", tmp_path)
    saved = dict(auditor._RECENT_RUNS)
    auditor._RECENT_RUNS.clear()
    try:
        yield
    finally:
        auditor._RECENT_RUNS.clear()
        auditor._RECENT_RUNS.update(saved)


def _persist(state_key: str, run_id: str, payload: dict) -> Path:
    run_dir = auditor._single_run_dir(state_key, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.json").write_text("{}", encoding="utf-8")
    auditor._persist_single_run(state_key, run_id, run_dir, _FakeReport(payload))
    return run_dir


def test_persist_writes_meta_and_registers_the_run() -> None:
    run_dir = _persist("u", "R1", {"input_path": "Архив: DS.zip", "findings_total": 0, "by_severity": {}})

    meta = json.loads((run_dir / auditor._RUN_META_FILE).read_text(encoding="utf-8"))
    assert meta["run_id"] == "R1" and meta["state_key"] == "u"
    assert meta["name"] == "Архив: DS.zip"
    assert auditor._RECENT_RUNS["u"][0]["run_id"] == "R1"


def test_render_recent_runs_lists_newest_first_with_links_and_delete() -> None:
    _persist("u", "OLD", {"input_path": "old", "findings_total": 0, "by_severity": {}})
    _persist("u", "NEW", {"input_path": "new", "findings_total": 3, "by_severity": {"major": 2, "minor": 1}})
    # persist inserts newest at the front, so NEW is already ahead of OLD

    html = auditor._render_recent_runs()

    assert "Недавние проверки" in html
    assert html.index("/app/auditor/run/NEW") < html.index("/app/auditor/run/OLD")  # newest first
    assert "/app/auditor/run/NEW" in html
    assert "/app/auditor/run/NEW/delete" in html  # per-row delete control
    assert "⚠ 3" in html and "major: 2" in html  # verdict + severity for the run with findings
    assert "✓ чисто" in html  # the clean run


def test_render_recent_runs_is_global_across_users() -> None:
    # runs are shared like branches: the panel merges every user's recent runs, newest first
    _persist("u1", "ALPHA", {"input_path": "alpha", "created_at": "a", "findings_total": 0, "by_severity": {}})
    _persist("u2", "BETA", {"input_path": "beta", "findings_total": 0, "by_severity": {}})

    html = auditor._render_recent_runs()

    assert "alpha" in html and "beta" in html
    assert "/app/auditor/run/ALPHA" in html and "/app/auditor/run/BETA" in html


def test_render_recent_runs_empty_when_no_runs() -> None:
    assert auditor._render_recent_runs() == ""


def test_render_recent_runs_escapes_names() -> None:
    _persist("u", "R", {"input_path": "<script>x</script>", "findings_total": 0, "by_severity": {}})
    html = auditor._render_recent_runs()
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html


def test_recent_runs_panel_has_search_and_sort_controls() -> None:
    _persist("u", "R1", {"input_path": "alpha", "findings_total": 3, "by_severity": {"major": 1, "minor": 2}})

    html = auditor._render_recent_runs()

    assert 'class="filter-panel recent-panel"' in html and 'data-recent="runs"' in html
    assert "recent-search" in html  # filter by name
    assert 'data-sort="name"' in html and 'data-sort="date"' in html and 'data-sort="errors"' in html
    # each row carries the sortable/filterable keys
    assert 'class="recent-row"' in html
    assert 'data-name="alpha"' in html  # lowercased for search
    assert 'data-errors="3"' in html  # findings count drives the "errors" sort
    assert "data-ts=" in html


def test_find_single_run_is_global() -> None:
    _persist("owner", "Gid", {"input_path": "x", "findings_total": 0, "by_severity": {}})
    found = auditor._find_single_run("Gid")
    assert found is not None and found["run_id"] == "Gid"
    assert auditor._find_single_run("missing") is None


def test_evict_old_single_runs_caps_and_reclaims_dirs() -> None:
    dirs = []
    for index in range(auditor._MAX_SINGLE_RUNS + 3):
        dirs.append(_persist("u", f"R{index}", {"input_path": str(index), "findings_total": 0, "by_severity": {}}))

    runs = auditor._RECENT_RUNS["u"]
    assert len(runs) == auditor._MAX_SINGLE_RUNS  # capped
    # the three oldest (first persisted) were evicted and their dirs reclaimed
    assert not dirs[0].exists() and not dirs[1].exists() and not dirs[2].exists()
    assert dirs[-1].exists()  # newest kept


def test_load_persisted_runs_reindexes_from_disk() -> None:
    run_dir = auditor._single_run_dir("u", "DISK1")
    run_dir.mkdir(parents=True)
    (run_dir / "report.json").write_text("{}", encoding="utf-8")
    (run_dir / auditor._RUN_META_FILE).write_text(
        json.dumps(
            {
                "run_id": "DISK1",
                "state_key": "u",
                "created_at": "2026-07-27T10:00:00",
                "name": "Архив: b.zip",
                "report_dir": "/stale/path",  # trumped by the on-disk location
                "summary": {"findings_total": 0, "by_severity": {}},
            }
        ),
        encoding="utf-8",
    )

    auditor._RECENT_RUNS.pop("u", None)
    auditor._load_persisted_runs()

    runs = auditor._RECENT_RUNS["u"]
    assert runs[0]["run_id"] == "DISK1"
    assert runs[0]["report_dir"] == str(run_dir)  # trusts disk location, not the stale stored path


def test_load_persisted_runs_keeps_newest_per_user() -> None:
    for index in range(auditor._MAX_SINGLE_RUNS + 4):
        run_dir = auditor._single_run_dir("u", f"D{index:02d}")
        run_dir.mkdir(parents=True)
        (run_dir / auditor._RUN_META_FILE).write_text(
            json.dumps(
                {
                    "run_id": f"D{index:02d}",
                    "state_key": "u",
                    "created_at": f"2026-07-{index + 1:02d}T00:00:00",
                    "name": str(index),
                    "summary": {},
                }
            ),
            encoding="utf-8",
        )

    auditor._RECENT_RUNS.pop("u", None)
    auditor._load_persisted_runs()

    runs = auditor._RECENT_RUNS["u"]
    assert len(runs) == auditor._MAX_SINGLE_RUNS
    assert runs[0]["run_id"] == "D15"  # newest created_at first (index 15 -> 2026-07-16)


def test_load_single_report_returns_none_without_artifact(tmp_path) -> None:
    assert auditor._load_single_report(tmp_path) is None  # no report.json


def _project_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("proj/README.md", "# proj")
        archive.writestr("proj/main.py", "x = 1")
    return buffer.getvalue()


@pytest.fixture
def run_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DISABLE_AUTH", "true")  # validate_request_user -> dev admin
    app = FastAPI()
    app.include_router(auditor.page_router)
    return TestClient(app)


def _write_real_report(run_dir: Path) -> None:
    from datetime import UTC, datetime

    from content_factory.audit.domain import AuditReport, RunSummary

    report = AuditReport(
        summary=RunSummary(started_at=datetime.now(UTC), input_path="proj", findings_total=0),
        units=[], entities=[], findings=[],
    )
    (run_dir / "report.json").write_text(report.model_dump_json(), encoding="utf-8")


def test_open_run_is_global_across_users(run_client: TestClient) -> None:
    # a run created under a DIFFERENT user is openable by anyone (shared like branches)
    run_dir = _persist("other_user", "X1", {"input_path": "proj", "findings_total": 0, "by_severity": {}})
    _write_real_report(run_dir)

    resp = run_client.get("/app/auditor/run/X1")
    assert resp.status_code == 200  # not 404 — the run belongs to another user but is still visible
    assert run_dir.exists()


def test_delete_run_is_global_across_users(run_client: TestClient) -> None:
    run_dir = _persist("other_user", "D1", {"input_path": "proj", "findings_total": 0, "by_severity": {}})

    resp = run_client.post("/app/auditor/run/D1/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert auditor._find_single_run("D1") is None  # removed from another user's bucket
    assert not run_dir.exists()  # its report dir reclaimed


def test_run_upload_returns_an_id_and_stashes_the_archive(run_client: TestClient) -> None:
    before = set(auditor._RUN_UPLOADS)
    resp = run_client.post(
        "/app/auditor/run/upload",
        files={"project_archive": ("proj.zip", _project_zip(), "application/zip")},
    )
    assert resp.status_code == 200
    upload_id = resp.json()["upload_id"]
    assert upload_id and upload_id in auditor._RUN_UPLOADS
    assert upload_id not in before
    # the stashed archive is on disk, ready for the run to consume by id
    assert Path(auditor._RUN_UPLOADS[upload_id]["archive_path"]).exists()
    auditor._RUN_UPLOADS.pop(upload_id, None)


def test_run_upload_requires_a_file(run_client: TestClient) -> None:
    assert run_client.post("/app/auditor/run/upload").status_code == 400


def test_run_consumes_the_upload_id(run_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class _FakeWebApp:
        INTERNAL_ARCHIVE_PATH_FIELD = "__ap"
        INTERNAL_ARCHIVE_NAME_FIELD = "__an"
        INTERNAL_UPLOAD_DIR_FIELD = "__ud"

        @staticmethod
        def run_from_form(form, state):
            captured.update(form)
            (state.report_dir).mkdir(parents=True, exist_ok=True)
            (state.report_dir / "report.json").write_text("{}", encoding="utf-8")
            return _FakeReport({"input_path": "Архив: proj.zip", "findings_total": 0, "by_severity": {}})

        @staticmethod
        def render_page(report, state, form_values=None):
            return "<html><h2>Таблица результата</h2>OK</html>"

    upload_id = run_client.post(
        "/app/auditor/run/upload",
        files={"project_archive": ("proj.zip", _project_zip(), "application/zip")},
    ).json()["upload_id"]

    # patch only the run's engine/render; _auditor_page_state already built real state on upload
    monkeypatch.setattr(auditor, "_load_auditor_web_app", lambda: _FakeWebApp())

    resp = run_client.post("/app/auditor/run", data={"upload_id": upload_id})
    assert resp.status_code == 200
    assert "Таблица результата" in resp.text
    # the pre-uploaded archive fed the run, and the id was consumed (single-use)
    assert captured["__an"] == "proj.zip"
    assert upload_id not in auditor._RUN_UPLOADS
