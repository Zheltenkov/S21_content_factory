from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest


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
