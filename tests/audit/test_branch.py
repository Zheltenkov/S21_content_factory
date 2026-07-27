"""Branch enumeration: which entries are auditable projects, which are skipped and why."""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

from content_factory.audit.branch import (
    SKIP_EMPTY,
    SKIP_LOOSE_FILE,
    SKIP_NO_README,
    SKIP_UNREADABLE_ARCHIVE,
    BranchEntry,
    enumerate_branch,
)


def _zip(path: Path, files: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def _by_name(entries: list[BranchEntry]) -> dict[str, BranchEntry]:
    return {entry.name: entry for entry in entries}


def test_inner_archive_with_readme_is_a_project(tmp_path: Path) -> None:
    branch = tmp_path / "branch"
    branch.mkdir()
    _zip(branch / "project-a.zip", {"project-a/README.md": "# A", "project-a/main.py": "x=1"})

    entries = enumerate_branch(branch, tmp_path / "work")
    entry = _by_name(entries)["project-a.zip"]

    assert entry.is_project is True
    assert entry.project_dir is not None
    # Auditor gets the folder that holds the README, not the extract root.
    assert (entry.project_dir / "README.md").exists()


def test_archive_without_readme_is_skipped_with_reason(tmp_path: Path) -> None:
    branch = tmp_path / "branch"
    branch.mkdir()
    _zip(branch / "no-readme.zip", {"no-readme/main.py": "x=1", "no-readme/data.csv": "a,b"})

    entry = _by_name(enumerate_branch(branch, tmp_path / "work"))["no-readme.zip"]

    assert entry.is_project is False
    assert entry.reason == SKIP_NO_README
    assert "README" in entry.reason_message


def test_loose_document_is_skipped_not_audited(tmp_path: Path) -> None:
    branch = tmp_path / "branch"
    branch.mkdir()
    (branch / "notes.docx").write_bytes(b"binary-doc")

    entry = _by_name(enumerate_branch(branch, tmp_path / "work"))["notes.docx"]

    assert entry.is_project is False
    assert entry.reason == SKIP_LOOSE_FILE


def test_plain_folder_with_readme_counts_as_a_project(tmp_path: Path) -> None:
    branch = tmp_path / "branch"
    (branch / "folder-proj").mkdir(parents=True)
    (branch / "folder-proj" / "README.md").write_text("# proj", encoding="utf-8")
    (branch / "folder-proj" / "app.py").write_text("x=1", encoding="utf-8")

    entry = _by_name(enumerate_branch(branch, tmp_path / "work"))["folder-proj"]

    assert entry.is_project is True
    assert (entry.project_dir / "README.md").exists()


def test_folder_without_readme_is_skipped(tmp_path: Path) -> None:
    branch = tmp_path / "branch"
    (branch / "not-a-project").mkdir(parents=True)
    (branch / "not-a-project" / "image.png").write_bytes(b"png")

    entry = _by_name(enumerate_branch(branch, tmp_path / "work"))["not-a-project"]

    assert entry.is_project is False
    assert entry.reason == SKIP_NO_README


def test_empty_folder_is_skipped_as_empty(tmp_path: Path) -> None:
    branch = tmp_path / "branch"
    (branch / "empty").mkdir(parents=True)

    entry = _by_name(enumerate_branch(branch, tmp_path / "work"))["empty"]

    assert entry.reason == SKIP_EMPTY


def test_readme_variants_and_tar_are_recognised(tmp_path: Path) -> None:
    branch = tmp_path / "branch"
    branch.mkdir()
    tar_path = branch / "proj-rus.tar.gz"
    with tarfile.open(tar_path, "w:gz") as archive:
        for name, body in {"proj/README_rus.md": "# рус", "proj/main.py": "x=1"}.items():
            data = body.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))

    entry = _by_name(enumerate_branch(branch, tmp_path / "work"))["proj-rus.tar.gz"]

    assert entry.is_project is True


def test_corrupt_archive_is_skipped_not_fatal(tmp_path: Path) -> None:
    branch = tmp_path / "branch"
    branch.mkdir()
    (branch / "broken.zip").write_bytes(b"not really a zip")

    entry = _by_name(enumerate_branch(branch, tmp_path / "work"))["broken.zip"]

    assert entry.is_project is False
    assert entry.reason == SKIP_UNREADABLE_ARCHIVE


def test_single_wrapper_folder_is_unwrapped_to_reach_projects(tmp_path: Path) -> None:
    # DS_Bootcamp.zip shape: one top folder wraps the project archives; README lives INSIDE
    # each inner zip, so the wrapper itself has no README and must be descended into.
    branch = tmp_path / "branch"
    (branch / "DS_Bootcamp").mkdir(parents=True)
    _zip(branch / "DS_Bootcamp" / "p1.zip", {"p1/README.md": "# 1", "p1/main.py": "x=1"})
    _zip(branch / "DS_Bootcamp" / "p2.zip", {"p2/README.md": "# 2", "p2/main.py": "y=1"})

    entries = enumerate_branch(branch, tmp_path / "work")

    assert {entry.name for entry in entries} == {"p1.zip", "p2.zip"}
    assert all(entry.is_project for entry in entries)


def test_single_top_folder_that_is_a_project_is_not_unwrapped(tmp_path: Path) -> None:
    # A lone top folder that IS a project (README at its root) is a 1-project branch — keep it.
    branch = tmp_path / "branch"
    (branch / "solo").mkdir(parents=True)
    (branch / "solo" / "README.md").write_text("# solo", encoding="utf-8")
    (branch / "solo" / "app.py").write_text("x=1", encoding="utf-8")

    entries = enumerate_branch(branch, tmp_path / "work")

    assert [entry.name for entry in entries] == ["solo"]
    assert entries[0].is_project is True


def test_mixed_branch_reports_projects_and_skips_in_stable_order(tmp_path: Path) -> None:
    branch = tmp_path / "branch"
    branch.mkdir()
    _zip(branch / "a-good.zip", {"a/README.md": "# a", "a/x.py": "1"})
    _zip(branch / "b-no-readme.zip", {"b/x.py": "1"})
    (branch / "c-loose.txt").write_text("loose", encoding="utf-8")

    entries = enumerate_branch(branch, tmp_path / "work")

    assert [entry.name for entry in entries] == ["a-good.zip", "b-no-readme.zip", "c-loose.txt"]
    assert [entry.is_project for entry in entries] == [True, False, False]
    projects = [entry for entry in entries if entry.is_project]
    skipped = [entry for entry in entries if not entry.is_project]
    assert len(projects) == 1
    assert len(skipped) == 2


def test_run_branch_audit_collects_results_and_isolates_failures(tmp_path: Path) -> None:
    from content_factory.audit.branch import BranchEntry, run_branch_audit

    proj_a = tmp_path / "a"
    proj_a.mkdir()
    proj_b = tmp_path / "b"
    proj_b.mkdir()
    entries = [
        BranchEntry(name="a.zip", is_project=True, project_dir=proj_a),
        BranchEntry(name="b.zip", is_project=True, project_dir=proj_b),
        BranchEntry(name="loose.txt", is_project=False, reason=SKIP_LOOSE_FILE),
    ]

    def fake_auditor(project_dir: Path, out_dir: Path) -> dict:
        if project_dir.name == "b":
            raise RuntimeError("движок упал на b")
        return {"verdict": "ok", "findings": 0}

    report = run_branch_audit(entries, tmp_path / "out", fake_auditor)

    assert report.project_count == 2
    assert report.completed_count == 1
    assert report.failed_count == 1
    assert len(report.skipped) == 1
    a = next(project for project in report.projects if project.name == "a.zip")
    b = next(project for project in report.projects if project.name == "b.zip")
    assert a.status == "completed" and a.summary == {"verdict": "ok", "findings": 0}
    assert b.status == "failed" and b.error is not None and "b" in b.error
    assert report.skipped[0].reason == SKIP_LOOSE_FILE
    assert report.as_dict()["skipped_count"] == 1


def test_run_branch_audit_reports_progress_after_each_project(tmp_path: Path) -> None:
    from content_factory.audit.branch import BranchEntry, run_branch_audit

    proj_a = tmp_path / "a"
    proj_a.mkdir()
    proj_b = tmp_path / "b"
    proj_b.mkdir()
    entries = [
        BranchEntry(name="a.zip", is_project=True, project_dir=proj_a),
        BranchEntry(name="loose.txt", is_project=False, reason=SKIP_LOOSE_FILE),
        BranchEntry(name="b.zip", is_project=True, project_dir=proj_b),
    ]
    events: list[tuple[int, int, str, str]] = []

    def on_progress(done: int, total: int, name: str, status: str) -> None:
        events.append((done, total, name, status))

    run_branch_audit(entries, tmp_path / "out", lambda pd, od: {"ok": 1}, on_progress=on_progress)

    # total counts only project entries (2); progress fires once per project, done increasing
    assert [event[0] for event in events] == [1, 2]
    assert all(event[1] == 2 for event in events)
    assert events[0][2] == "a.zip" and events[0][3] == "completed"
    assert events[1][2] == "b.zip"


def test_branch_report_round_trips_through_dict() -> None:
    from content_factory.audit.branch import BranchProjectResult, BranchReport, BranchSkip

    report = BranchReport(
        projects=[
            BranchProjectResult(name="a.zip", status="completed", summary={"findings_total": 2}, report_dir="/out/a"),
            BranchProjectResult(name="b.zip", status="failed", error="boom"),
        ],
        skipped=[BranchSkip(name="c.txt", reason="loose_file", message="skip")],
    )

    restored = BranchReport.from_dict(report.as_dict())

    assert restored.as_dict() == report.as_dict()
    assert restored.completed_count == 1 and restored.failed_count == 1
    assert restored.projects[0].report_dir == "/out/a"
    assert restored.skipped[0].reason == "loose_file"


def test_run_branch_audit_with_no_projects_is_empty_not_error(tmp_path: Path) -> None:
    from content_factory.audit.branch import BranchEntry, run_branch_audit

    entries = [BranchEntry(name="notes.txt", is_project=False, reason=SKIP_LOOSE_FILE)]
    report = run_branch_audit(entries, tmp_path / "out", lambda project_dir, out_dir: {})

    assert report.project_count == 0
    assert len(report.skipped) == 1
