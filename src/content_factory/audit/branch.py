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
import threading
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Projects audited at once inside one branch. Audits are I/O-bound (LLM + network), so a small
#: pool cuts total time ~N× without exhausting memory or the model provider's rate limit.
DEFAULT_BRANCH_CONCURRENCY = 3

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


def _has_direct_readme(directory: Path) -> bool:
    """README sitting directly in the folder — marks the folder itself as a project root."""
    return any(item.is_file() and _README_RE.match(item.name) for item in directory.iterdir())


def _holds_project_shaped_children(directory: Path) -> bool:
    """The folder contains archives or subfolders (candidate projects), not just loose files."""
    return any(item.is_dir() or (item.is_file() and _is_archive(item)) for item in directory.iterdir())


def _effective_branch_dir(branch_dir: Path) -> Path:
    """Descend through a single wrapping folder that merely holds the projects.

    Branch archives are usually zipped with everything under one top folder
    (``DS_Bootcamp/DSB1.zip``, ``DS_Bootcamp/DSB2.zip``, …). When the branch dir holds exactly
    one entry — a directory that is NOT itself a project (no README directly inside it) but DOES
    hold project-shaped children (inner archives or subfolders) — treat that folder as the branch
    and look inside it. A lone folder that is a project, or that holds only loose files / nothing,
    is left in place so it is reported as a project or skipped with a reason. Bounded loop guards
    against pathological deep nesting.
    """
    current = branch_dir
    for _ in range(8):
        children = list(current.iterdir())
        if len(children) != 1 or not children[0].is_dir():
            return current
        only = children[0]
        if _has_direct_readme(only) or not _holds_project_shaped_children(only):
            return current
        current = only
    return current


def enumerate_branch(branch_dir: Path, extract_root: Path) -> list[BranchEntry]:
    """Classify each top-level entry of an already-extracted branch directory.

    ``branch_dir`` is the extracted outer archive; ``extract_root`` is a scratch dir where inner
    archives are unpacked. A single wrapping top folder is unwrapped first (see
    ``_effective_branch_dir``). Projects (archive OR folder containing a README) come back with a
    ``project_dir`` to audit; everything else is skipped with a reason. Order is stable (sorted).
    """
    extract_root.mkdir(parents=True, exist_ok=True)
    branch_dir = _effective_branch_dir(branch_dir)
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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BranchReport:
        """Rebuild a report from ``as_dict`` output (used to reload persisted branch runs)."""
        projects = [
            BranchProjectResult(
                name=str(item.get("name", "")),
                status=str(item.get("status", "")),
                summary=item.get("summary"),
                error=item.get("error"),
                report_dir=item.get("report_dir"),
            )
            for item in data.get("projects", [])
        ]
        skipped = [
            BranchSkip(
                name=str(item.get("name", "")),
                reason=str(item.get("reason", "")),
                message=str(item.get("message", "")),
            )
            for item in data.get("skipped", [])
        ]
        return cls(projects=projects, skipped=skipped)

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
# on_progress(done, total, name, status) — fired after each project so a caller can report "X of N".
ProgressCallback = Callable[[int, int, str, str], None]


def run_branch_audit(
    entries: list[BranchEntry],
    output_root: Path,
    run_project: ProjectAuditor,
    on_progress: ProgressCallback | None = None,
    max_workers: int = DEFAULT_BRANCH_CONCURRENCY,
) -> BranchReport:
    """Audit every project entry with the injected single-project auditor; collect a branch report.

    Projects are audited concurrently (a small pool — see ``max_workers``), each into its own output
    dir, so one branch finishes in roughly ``total / max_workers`` project-times instead of the full
    sum. Each project is isolated: a failure is captured as a failed result (with the message) and
    never aborts the branch. Skips carry their reason straight through from enumeration.
    ``on_progress`` (if given) is called as each project *completes* with ``(done, total, name,
    status)`` where ``total`` counts only project entries and ``done`` increases monotonically; the
    final ``projects`` list keeps the original entry order regardless of completion order.
    """
    skipped: list[BranchSkip] = []
    work: list[tuple[int, BranchEntry, Path]] = []
    for index, entry in enumerate(entries):
        if not entry.is_project or entry.project_dir is None:
            skipped.append(
                BranchSkip(name=entry.name, reason=entry.reason or "skipped", message=entry.reason_message)
            )
            continue
        project_out = output_root / _safe_dirname(entry.name)
        project_out.mkdir(parents=True, exist_ok=True)
        work.append((index, entry, project_out))

    total = len(work)
    results: dict[int, BranchProjectResult] = {}
    progress_lock = threading.Lock()
    done = 0

    def audit_one(item: tuple[int, BranchEntry, Path]) -> tuple[int, BranchProjectResult]:
        nonlocal done
        index, entry, project_out = item
        assert entry.project_dir is not None
        try:
            summary = run_project(entry.project_dir, project_out)
            result = BranchProjectResult(
                name=entry.name, status="completed", summary=summary, report_dir=str(project_out)
            )
            status = "completed"
        except Exception as exc:  # noqa: BLE001 - one project's failure must not sink the branch.
            result = BranchProjectResult(name=entry.name, status="failed", error=str(exc))
            status = "failed"
        with progress_lock:
            done += 1
            done_now = done
        if on_progress is not None:
            on_progress(done_now, total, entry.name, status)
        return index, result

    if total:
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, total))) as pool:
            for future in as_completed([pool.submit(audit_one, item) for item in work]):
                index, result = future.result()
                results[index] = result

    projects = [results[index] for index, _entry, _out in work]
    return BranchReport(projects=projects, skipped=skipped)


def _safe_dirname(name: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "-", name, flags=re.UNICODE).strip("-.")
    return (cleaned[:80] or "project")
