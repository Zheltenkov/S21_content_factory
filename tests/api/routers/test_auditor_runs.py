"""Single-project run persistence: recent-runs list, disk reload, eviction — no LLM/engine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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

    html = auditor._render_recent_runs("u")

    assert "Недавние проверки" in html
    assert html.index("new") < html.index("old")  # newest first
    assert "/app/auditor/run/NEW" in html
    assert "/app/auditor/run/NEW/delete" in html  # per-row delete control
    assert "⚠ 3" in html and "major: 2" in html  # verdict + severity for the run with findings
    assert "✓ чисто" in html  # the clean run


def test_render_recent_runs_empty_for_unknown_user() -> None:
    assert auditor._render_recent_runs("nobody") == ""


def test_render_recent_runs_escapes_names() -> None:
    _persist("u", "R", {"input_path": "<script>x</script>", "findings_total": 0, "by_severity": {}})
    html = auditor._render_recent_runs("u")
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html


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
