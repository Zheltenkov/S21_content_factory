"""What every test of the module is given."""

import pytest
from project_generator_lab.core import usage


@pytest.fixture(autouse=True)
def usage_kept_apart(tmp_path, monkeypatch):
    """The account of the calls is a folder of the working module (data/usage): a test that goes through a route
    with a project's id in the address would write into it. Each test has its own."""
    monkeypatch.setattr(usage, "USAGE_DIR", tmp_path / "usage")
