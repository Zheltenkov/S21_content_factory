"""Branch intake for the auditor: an archive whose entries are themselves projects.

A "branch" is a zip/tar whose top-level entries are individual project archives (or project
folders). This module turns that outer archive into a list of auditable project directories plus
an explicit skip list — a loose document, a folder that is not a project, or an archive without a
README is skipped WITH A REASON rather than silently dropped or failing the whole branch.

Pure filesystem logic (no audit engine, no FastAPI): it decides WHAT to audit; the caller runs
the existing single-project auditor over each returned project and renders the summary.
"""

from __future__ import annotations

import re
import tarfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The auditor's own notion of a README (extra_checkers.README_RE): readme.md, readme_rus.md, ...
_README_RE = re.compile(r"(?i)^readme(?:_[a-z]+)?\.md$")
_ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz")

# Reason codes for a skipped entry (kept stable for the UI; message is the human label).
SKIP_LOOSE_FILE = "loose_file"
SKIP_NO_README = "no_readme"
SKIP_EMPTY = "empty"
SKIP_UNREADABLE_ARCHIVE = "unreadable_archive"

_SKIP_MESSAGES = {
    SKIP_LOOSE_FILE: "Отдельный файл, не архив проекта — пропущено.",
    SKIP_NO_README: "Нет README.md — не распознано как проект, пропущено.",
    SKIP_EMPTY: "Пустая папка или архив — пропущено.",
    SKIP_UNREADABLE_ARCHIVE: "Архив повреждён или не читается — пропущено.",
}


@dataclass(frozen=True)
class BranchEntry:
    """One top-level element of the branch: an auditable project or a skipped item."""

    name: str
    is_project: bool
    project_dir: Path | None = None  # set when is_project: the dir to hand the auditor
    reason: str | None = None  # set when skipped: one of the SKIP_* codes

    @property
    def reason_message(self) -> str:
        return _SKIP_MESSAGES.get(self.reason or "", "Пропущено.")


def skip_reason_message(reason: str) -> str:
    return _SKIP_MESSAGES.get(reason, "Пропущено.")


def _is_archive(path: Path) -> bool:
    name = path.name.lower()
    return any(name.endswith(suffix) for suffix in _ARCHIVE_SUFFIXES)


def _find_project_root(root: Path) -> Path | None:
    """Return the shallowest directory that directly contains a README.md, else None.

    A project archive often wraps everything in a single top folder (proj/README.md); we point
    the auditor at the folder that holds the README so it sees a normal project root.
    """
    best: tuple[int, Path] | None = None
    for path in root.rglob("*"):
        if path.is_file() and _README_RE.match(path.name):
            depth = len(path.relative_to(root).parts)
            if best is None or depth < best[0]:
                best = (depth, path.parent)
    return best[1] if best else None


def _ensure_safe_member(target_dir: Path, member_name: str) -> None:
    resolved = (target_dir / member_name).resolve()
    if target_dir.resolve() not in resolved.parents and resolved != target_dir.resolve():
        raise ValueError(f"Небезопасный путь в архиве: {member_name}")


def safe_extract(archive_path: Path, target_dir: Path) -> None:
    """Extract a zip/tar into target_dir, rejecting path traversal and link members."""
    target_dir.mkdir(parents=True, exist_ok=True)
    name = archive_path.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.namelist():
                _ensure_safe_member(target_dir, member)
            archive.extractall(target_dir)
        return
    if name.endswith((".tar", ".tar.gz", ".tgz")):
        with tarfile.open(archive_path) as archive:
            for tar_member in archive.getmembers():
                if tar_member.issym() or tar_member.islnk():
                    raise ValueError("Архив содержит недопустимые ссылки")
                _ensure_safe_member(target_dir, tar_member.name)
            archive.extractall(target_dir)
        return
    raise ValueError("Поддерживаются ZIP, TAR и TAR.GZ архивы")


def _classify_archive_entry(entry: Path, extract_root: Path) -> BranchEntry:
    dest = extract_root / (entry.stem + "_x")
    try:
        safe_extract(entry, dest)
    except (zipfile.BadZipFile, tarfile.TarError, ValueError, OSError):
        return BranchEntry(name=entry.name, is_project=False, reason=SKIP_UNREADABLE_ARCHIVE)
    if not any(dest.rglob("*")):
        return BranchEntry(name=entry.name, is_project=False, reason=SKIP_EMPTY)
    project_root = _find_project_root(dest)
    if project_root is None:
        return BranchEntry(name=entry.name, is_project=False, reason=SKIP_NO_README)
    return BranchEntry(name=entry.name, is_project=True, project_dir=project_root)


def _classify_folder_entry(entry: Path) -> BranchEntry:
    if not any(entry.iterdir()):
        return BranchEntry(name=entry.name, is_project=False, reason=SKIP_EMPTY)
    project_root = _find_project_root(entry)
    if project_root is None:
        return BranchEntry(name=entry.name, is_project=False, reason=SKIP_NO_README)
    return BranchEntry(name=entry.name, is_project=True, project_dir=project_root)


def enumerate_branch(branch_dir: Path, extract_root: Path) -> list[BranchEntry]:
    """Classify each top-level entry of an already-extracted branch directory.

    ``branch_dir`` is the extracted outer archive; ``extract_root`` is a scratch dir where inner
    archives are unpacked. Projects (archive OR folder containing a README) come back with a
    ``project_dir`` to audit; everything else is skipped with a reason. Order is stable (sorted).
    """
    extract_root.mkdir(parents=True, exist_ok=True)
    entries: list[BranchEntry] = []
    for entry in sorted(branch_dir.iterdir(), key=lambda item: item.name.lower()):
        if entry.is_dir():
            entries.append(_classify_folder_entry(entry))
        elif _is_archive(entry):
            entries.append(_classify_archive_entry(entry, extract_root))
        else:
            entries.append(BranchEntry(name=entry.name, is_project=False, reason=SKIP_LOOSE_FILE))
    return entries


@dataclass(frozen=True)
class BranchProjectResult:
    """Outcome of auditing one project inside the branch."""

    name: str
    status: str  # "completed" | "failed"
    summary: dict[str, Any] | None = None  # the single-project report summary
    error: str | None = None
    report_dir: str | None = None  # where this project's report artifacts were written


@dataclass(frozen=True)
class BranchSkip:
    name: str
    reason: str
    message: str


@dataclass(frozen=True)
class BranchReport:
    """Everything the branch view needs: per-project results + the skip list + totals."""

    projects: list[BranchProjectResult] = field(default_factory=list)
    skipped: list[BranchSkip] = field(default_factory=list)

    @property
    def project_count(self) -> int:
        return len(self.projects)

    @property
    def completed_count(self) -> int:
        return sum(1 for project in self.projects if project.status == "completed")

    @property
    def failed_count(self) -> int:
        return sum(1 for project in self.projects if project.status == "failed")

    def as_dict(self) -> dict[str, Any]:
        return {
            "project_count": self.project_count,
            "completed_count": self.completed_count,
            "failed_count": self.failed_count,
            "skipped_count": len(self.skipped),
            "projects": [
                {
                    "name": project.name,
                    "status": project.status,
                    "summary": project.summary,
                    "error": project.error,
                    "report_dir": project.report_dir,
                }
                for project in self.projects
            ],
            "skipped": [{"name": skip.name, "reason": skip.reason, "message": skip.message} for skip in self.skipped],
        }


# run_project(project_dir, output_dir) -> summary dict; raises on failure.
ProjectAuditor = Callable[[Path, Path], dict[str, Any]]


def run_branch_audit(entries: list[BranchEntry], output_root: Path, run_project: ProjectAuditor) -> BranchReport:
    """Audit every project entry with the injected single-project auditor; collect a branch report.

    Each project is isolated: a failure is captured as a failed result (with the message) and never
    aborts the branch, so one broken project does not lose the reports for the rest. Skips carry
    their reason straight through from enumeration.
    """
    projects: list[BranchProjectResult] = []
    skipped: list[BranchSkip] = []
    for entry in entries:
        if not entry.is_project or entry.project_dir is None:
            skipped.append(
                BranchSkip(name=entry.name, reason=entry.reason or "skipped", message=entry.reason_message)
            )
            continue
        project_out = output_root / _safe_dirname(entry.name)
        project_out.mkdir(parents=True, exist_ok=True)
        try:
            summary = run_project(entry.project_dir, project_out)
            projects.append(
                BranchProjectResult(
                    name=entry.name, status="completed", summary=summary, report_dir=str(project_out)
                )
            )
        except Exception as exc:  # noqa: BLE001 - one project's failure must not sink the branch.
            projects.append(BranchProjectResult(name=entry.name, status="failed", error=str(exc)))
    return BranchReport(projects=projects, skipped=skipped)


def _safe_dirname(name: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "-", name, flags=re.UNICODE).strip("-.")
    return (cleaned[:80] or "project")
