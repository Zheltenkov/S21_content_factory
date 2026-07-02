from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

# The audit code reads these via os.getenv (env overrides passed env_values).
# In the unified repo, api.db.session calls load_dotenv() at import, so the root
# .env leaks OPENROUTER/AUTH keys into os.environ. Isolate the audit suite so its
# env-based tests stay deterministic regardless of process environment.
_AUDIT_ENV_KEYS = (
    "AUTH_USERNAME",
    "AUTH_PASSWORD",
    "OPENROUTER_API_KEY",
    "OPEN_ROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "OPEN_ROUTER_MODEL",
    "OPENROUTER_FACT_MODEL",
    "OPEN_ROUTER_FACT_MODEL",
    "OPENROUTER_TECH_MODEL",
    "OPEN_ROUTER_TECH_MODEL",
)


@pytest.fixture(autouse=True)
def _isolate_audit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove ambient OpenRouter/auth env vars so audit tests are hermetic."""

    for key in _AUDIT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def workspace_tmp_path() -> Path:
    """Создаём временную папку внутри рабочей области, а не в системном Temp."""

    root = Path(__file__).resolve().parents[1] / "test_tmp"
    root.mkdir(exist_ok=True)
    path = root / f"case_{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
