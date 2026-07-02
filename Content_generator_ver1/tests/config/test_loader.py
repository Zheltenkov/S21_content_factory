import json
import shutil
from pathlib import Path

from content_gen.config import loader


def _reset_loader_caches(monkeypatch):
    monkeypatch.setattr(loader, "_prompt_cache", {})
    monkeypatch.setattr(loader, "_agent_config_cache", {})
    monkeypatch.setattr(loader, "_agent_versions", {})


def test_get_agent_config_reads_yaml_and_caches(monkeypatch):
    repo_root = Path(".tmp/test-fixtures/config-loader/repo").resolve()
    shutil.rmtree(repo_root, ignore_errors=True)
    config_root = repo_root / "content_gen" / "config"
    agents_dir = config_root / "agents"
    prompts_dir = repo_root / "prompts"
    agents_dir.mkdir(parents=True)
    prompts_dir.mkdir(parents=True)

    prompt_file = prompts_dir / "system.txt"
    prompt_file.write_text("System prompt for {language}", encoding="utf-8")

    agent_yaml = agents_dir / "demo.yaml"
    agent_yaml.write_text(
        json.dumps(
            {
                "version": "9.9.9",
                "owner": "methodology",
                "input_schema": "DemoInput",
                "llm": {"temperature": 0.2},
                "prompts": {
                    "system": {
                        "type": "file",
                        "path": "prompts/system.txt",
                        "output_schema": "SystemPromptOutput",
                    },
                    "user": {"type": "inline", "value": "Hello {skill}"},
                },
            }
        ),
        encoding="utf-8",
    )

    _reset_loader_caches(monkeypatch)
    monkeypatch.setattr(loader, "REPO_ROOT", repo_root)
    monkeypatch.setattr(loader, "CONFIG_ROOT", config_root)

    cfg1 = loader.get_agent_config("demo")
    cfg2 = loader.get_agent_config("demo")

    assert cfg1 is cfg2  # кеширование
    assert cfg1.version == "9.9.9"
    assert cfg1.get_prompt("system") == "System prompt for {language}"
    assert cfg1.get_prompt("user") == "Hello {skill}"
    assert loader.get_loaded_agent_versions() == {"demo": "9.9.9"}

    system_record = cfg1.get_prompt_record("system")
    assert system_record.prompt_id == "demo.system"
    assert system_record.version == "9.9.9"
    assert system_record.owner == "methodology"
    assert system_record.input_schema == "DemoInput"
    assert system_record.output_schema == "SystemPromptOutput"
    assert len(system_record.prompt_hash) == 16

    trace_kwargs = cfg1.prompt_trace_kwargs("system", "user", output_schema="DemoOutput")
    assert trace_kwargs["prompt_id"] == "demo.system+demo.user"
    assert trace_kwargs["prompt_version"] == "9.9.9"
    assert trace_kwargs["prompt_owner"] == "methodology"
    assert trace_kwargs["prompt_input_schema"] == "DemoInput"
    assert trace_kwargs["prompt_output_schema"] == "DemoOutput"
    assert len(trace_kwargs["prompt_hash"]) == 16

    registry = loader.build_prompt_registry(["demo"])
    assert sorted(registry) == ["demo.system", "demo.user"]

