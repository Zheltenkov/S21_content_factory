"""Didactic dimensions load from config, with env override and safe fallback."""

from __future__ import annotations

from pathlib import Path

from content_factory.generation.evaluation.didactic import dimensions as dims_module
from content_factory.generation.evaluation.didactic.dimensions import (
    _DEFAULT_DIMENSIONS,
    DIMENSIONS,
    load_dimensions,
)


def test_packaged_config_matches_default_set() -> None:
    """The shipped YAML must reproduce the packaged default hypothesis set."""
    assert load_dimensions() == _DEFAULT_DIMENSIONS
    # Module-level DIMENSIONS is loaded from that config.
    assert DIMENSIONS == _DEFAULT_DIMENSIONS
    assert {d.id for d in DIMENSIONS} == {
        "coherence",
        "scaffolding",
        "example_quality",
        "cognitive_load",
        "school_tone",
        "naturalness",
    }


def test_env_override_replaces_dimensions(tmp_path: Path, monkeypatch) -> None:
    override = tmp_path / "dims.yaml"
    override.write_text(
        "dimensions:\n"
        "  - id: only\n"
        "    title: Единственный\n"
        "    question: Работает ли подмена?\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DIDACTIC_DIMENSIONS_PATH", str(override))
    loaded = load_dimensions()
    assert [d.id for d in loaded] == ["only"]
    assert loaded[0].title == "Единственный"


def test_missing_override_falls_back_to_default(tmp_path: Path) -> None:
    loaded = load_dimensions(path=tmp_path / "nope.yaml")
    assert loaded == _DEFAULT_DIMENSIONS


def test_malformed_config_falls_back_to_default(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("dimensions: not-a-list\n", encoding="utf-8")
    assert load_dimensions(path=bad) == _DEFAULT_DIMENSIONS


def test_dimensions_source_is_configurable_not_hardcoded() -> None:
    """Regression guard: the module exposes a loader, not only a frozen constant."""
    assert callable(dims_module.load_dimensions)
