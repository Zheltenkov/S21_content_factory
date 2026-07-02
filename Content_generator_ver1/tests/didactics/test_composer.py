import shutil
from pathlib import Path

from content_gen.didactics.composer import compose_didactics_context
from content_gen.didactics.loader import load_didactics_manifest


def test_build_context_from_skill_specs():
    fixture_root = Path(".tmp/test-fixtures/didactics-composer").resolve()
    shutil.rmtree(fixture_root, ignore_errors=True)
    fixture_root.mkdir(parents=True)

    skill_file = fixture_root / "voice.md"
    skill_file.write_text("voice rules", encoding="utf-8")
    manifest_file = fixture_root / "manifest.yaml"
    manifest_file.write_text(
        "\n".join(
            [
                "schema_version: 1",
                'bundle_id: "bundle"',
                'version: "1.0.0"',
                'mode: "strict"',
                "skill_specs:",
                '  - id: "voice.default"',
                '    file: "voice.md"',
                "agent_bindings:",
                '  test_agent: ["voice.default"]',
            ]
        ),
        encoding="utf-8",
    )

    manifest = load_didactics_manifest(manifest_file)
    from content_gen.didactics.loader import build_didactics_context

    context = build_didactics_context(manifest, fixture_root)
    assert "didactics_manifest_version=1.0.0" in context
    assert "voice rules" in context


def test_build_context_fails_on_missing_file_in_strict_manifest():
    fixture_root = Path(".tmp/test-fixtures/didactics-missing").resolve()
    shutil.rmtree(fixture_root, ignore_errors=True)
    fixture_root.mkdir(parents=True)

    manifest_file = fixture_root / "manifest.yaml"
    manifest_file.write_text(
        "\n".join(
            [
                "schema_version: 1",
                'bundle_id: "bundle"',
                'version: "1.0.0"',
                'mode: "strict"',
                "skill_specs:",
                '  - id: "source.rules"',
                '    file: "missing.md"',
            ]
        ),
        encoding="utf-8",
    )

    manifest = load_didactics_manifest(manifest_file)
    from content_gen.didactics.loader import build_didactics_context

    try:
        build_didactics_context(manifest, fixture_root)
    except FileNotFoundError as exc:
        assert "missing.md" in str(exc)
    else:
        raise AssertionError("strict didactics context must fail when a bound file is absent")


def test_compose_didactics_context_returns_trace():
    context, trace = compose_didactics_context("practice")
    assert isinstance(context, str)
    assert "didactics_bundle_version" in trace
    assert "didactics_mode" in trace
    assert trace["didactics_agent"] == "practice"
    assert "Критерии оценивания проекта" in context
    assert "Шаблон практического задания" in context


def test_default_manifest_covers_core_agents_and_rewrite_style():
    manifest = load_didactics_manifest()
    bindings = manifest.agent_bindings

    for agent in ["title_annotation", "intro_rules", "theory", "practice", "regeneration", "rewrite_style"]:
        assert bindings.get(agent), f"{agent} has no didactics binding"

    assert "quality.checkup" in bindings["practice"]
    assert "source.stage_rules" in bindings["practice"]
    assert "structure.schema" in bindings["theory"]


def test_compose_didactics_context_for_unknown_agent_is_empty():
    context, trace = compose_didactics_context("unknown_agent")
    assert context == ""
    assert trace["didactics_skills_used"] == []
