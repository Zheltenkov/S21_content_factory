"""Archive extraction tolerance: a member whose filename exceeds the filesystem's per-component
byte limit (ENAMETOOLONG) must not sink the whole branch — it is extracted under a truncated name.

Real branches carry supplementary materials (docx/pdf) with long Cyrillic names > 255 bytes; a
plain ``extractall`` aborts on them, so those branches never audited. Extraction now truncates the
offending component (keeping the extension) instead of raising.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException

from content_factory.api.routers.auditor import _extract_archive


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _write_zip(tmp_path: Path, members: dict[str, bytes]) -> Path:
    path = tmp_path / "in.zip"
    path.write_bytes(_zip(members))
    return path


def test_extract_truncates_overlong_component_instead_of_failing(tmp_path: Path) -> None:
    long_stem = "и" * 200  # 400 bytes in UTF-8 -> exceeds the 255-byte component limit
    member = f"proj/materials/{long_stem}.docx"
    archive = _write_zip(tmp_path, {member: b"payload", "proj/README.md": b"# hi"})
    out = tmp_path / "out"

    _extract_archive(archive, out)  # must not raise

    assert (out / "proj" / "README.md").read_bytes() == b"# hi"  # the rest extracts intact
    materials = list((out / "proj" / "materials").iterdir())
    assert len(materials) == 1
    kept = materials[0]
    assert len(kept.name.encode("utf-8")) <= 255  # component now within the FS limit
    assert kept.suffix == ".docx"  # extension preserved
    assert kept.read_bytes() == b"payload"  # content preserved


def test_extract_keeps_normal_members_unchanged(tmp_path: Path) -> None:
    archive = _write_zip(tmp_path, {"proj/README.md": b"# hi", "proj/src/main.py": b"x = 1"})
    out = tmp_path / "out"

    _extract_archive(archive, out)

    assert (out / "proj" / "README.md").read_bytes() == b"# hi"
    assert (out / "proj" / "src" / "main.py").read_bytes() == b"x = 1"


def test_extract_still_rejects_path_traversal(tmp_path: Path) -> None:
    archive = _write_zip(tmp_path, {"../evil.txt": b"x"})
    out = tmp_path / "out"

    with pytest.raises(HTTPException):
        _extract_archive(archive, out)
