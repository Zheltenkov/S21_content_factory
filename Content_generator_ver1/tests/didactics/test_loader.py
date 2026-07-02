from __future__ import annotations

import shutil
from pathlib import Path

from content_gen.didactics.composer import compose_didactics_context
from content_gen.didactics.loader import load_didactics_manifest


def test_load_didactics_manifest_accepts_nested_threshold_refs():
    fixture_root = Path(".tmp/test-fixtures/didactics-loader").resolve()
    shutil.rmtree(fixture_root, ignore_errors=True)
    fixture_root.mkdir(parents=True)

    manifest_path = fixture_root / "manifest.yaml"
    manifest_path.write_text(
        """
schema_version: 1
bundle_id: "test"
version: "1.0.0"
mode: "strict"
default_language: "ru"
skill_specs: []
agent_bindings: {}
threshold_refs:
  annotation:
    chars: "annotation_chars"
  intro:
    words: "intro_words"
  practice: "practice_tasks_range"
""".strip(),
        encoding="utf-8",
    )

    manifest = load_didactics_manifest(manifest_path)

    assert manifest.threshold_refs["annotation"] == {"chars": "annotation_chars"}
    assert manifest.threshold_refs["intro"] == {"words": "intro_words"}
    assert manifest.threshold_refs["practice"] == "practice_tasks_range"


def test_compose_didactics_context_uses_default_manifest():
    context, trace = compose_didactics_context("title_annotation")

    assert context
    assert "didactics_manifest_version=" in context
    assert "readme" in context.lower() or "школа 21" in context.lower()
    assert trace["didactics_agent"] == "title_annotation"
    assert trace["didactics_skills_used"]
