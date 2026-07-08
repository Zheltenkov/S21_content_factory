"""Guards that catalog migrations stay reproducible: each migration pins the sha256 of the
`.sql` it was authored against, so editing an applied migration's SQL fails loud instead of
silently rewriting history. This test catches a drifted pin in CI before a deploy does."""

import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ROOT / "migrations" / "versions"

# migration file stem -> [(path_attr, sha_attr), ...]
_FROZEN = {
    "014_catalog_schema": [("_DDL_PATH", "_DDL_SHA256")],
    "015_curriculum_plan_mirror": [("_DDL_PATH", "_DDL_SHA256")],
    "016_catalog_working_tables": [
        ("_DDL_PATH", "_DDL_SHA256"),
        ("_FUNCTIONS_PATH", "_FUNCTIONS_SHA256"),
    ],
    "017_catalog_admin_runtime_tables": [("_DDL_PATH", "_DDL_SHA256")],
}


def _load(stem: str):
    spec = importlib.util.spec_from_file_location(f"mig_{stem}", VERSIONS / f"{stem}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


@pytest.mark.parametrize("stem", sorted(_FROZEN))
def test_migration_sql_matches_pinned_sha(stem: str) -> None:
    module = _load(stem)
    for path_attr, sha_attr in _FROZEN[stem]:
        path = getattr(module, path_attr)
        pinned = getattr(module, sha_attr)
        assert _sha(path) == pinned, (
            f"{stem}:{path.name} content drifted from its pinned sha. Do NOT edit an applied "
            f"migration's SQL — add a new migration (or, if this change is intentional and the "
            f"migration was never applied anywhere, update {sha_attr} deliberately)."
        )


def test_read_frozen_raises_on_drift() -> None:
    module = _load("017_catalog_admin_runtime_tables")
    with pytest.raises(RuntimeError, match="add a NEW migration"):
        module._read_frozen(module._DDL_PATH, "0" * 64)
