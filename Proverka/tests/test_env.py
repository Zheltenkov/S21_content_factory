from pathlib import Path

from content_audit.env import get_env_value, load_env_file


def test_load_env_file_reads_quoted_values(workspace_tmp_path: Path) -> None:
    env_path = workspace_tmp_path / ".env"
    env_path.write_text("OPEN_ROUTER_API_KEY='secret-value'\nOPEN_ROUTER_MODEL=model/name # comment\n", encoding="utf-8")

    values = load_env_file(env_path)

    assert values["OPEN_ROUTER_API_KEY"] == "secret-value"
    assert values["OPEN_ROUTER_MODEL"] == "model/name"


def test_get_env_value_supports_aliases(workspace_tmp_path: Path) -> None:
    del workspace_tmp_path
    values = {"OPEN_ROUTER_API_KEY": "secret-value"}

    assert get_env_value(("OPENROUTER_API_KEY", "OPEN_ROUTER_API_KEY"), values) == "secret-value"
