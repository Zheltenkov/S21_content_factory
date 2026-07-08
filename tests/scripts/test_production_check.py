from __future__ import annotations

import importlib.util
import shutil
import sys
import uuid
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "production_check.py"
spec = importlib.util.spec_from_file_location("production_check", MODULE_PATH)
production_check = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = production_check
spec.loader.exec_module(production_check)


def _make_case_dir() -> Path:
    case_dir = Path.cwd() / ".tmp_script_tests" / uuid.uuid4().hex
    case_dir.mkdir(parents=True, exist_ok=False)
    return case_dir


def test_parse_env_file_reports_duplicate_locations() -> None:
    case_dir = _make_case_dir()
    env_file = case_dir / ".env"
    env_file.write_text("A=1\n# comment\nB=2\nA=3\n", encoding="utf-8")

    try:
        values, locations = production_check.parse_env_file(env_file)

        assert values == {"A": "3", "B": "2"}
        assert locations["A"] == [1, 4]
    finally:
        shutil.rmtree(case_dir, ignore_errors=True)


def test_write_missing_defaults_only_appends_safe_values() -> None:
    project_root = _make_case_dir()
    (project_root / ".env.example").write_text(
        "\n".join(
            [
                "DATABASE_URL=postgresql://content_user:change_me@localhost:5432/content_generator",
                "JWT_SECRET_KEY=change_me",
                "OPENAI_API_KEY=change_me",
                "MAX_RESULT_CACHE_SIZE=100",
                "REDIS_URL=",
                "METHODOLOGY_GATE_MODE=observe",
            ]
        ),
        encoding="utf-8",
    )
    (project_root / ".env").write_text(
        "\n".join(
            [
                "DATABASE_URL=postgresql://content_user:pass123@localhost:5432/content_generator",
                "JWT_SECRET_KEY=real-secret",
                "OPENAI_API_KEY=real-key",
            ]
        ),
        encoding="utf-8",
    )
    report = production_check.CheckReport()

    try:
        production_check.check_env_files(report, project_root, write_missing_defaults=True)

        env_text = (project_root / ".env").read_text(encoding="utf-8")
        assert "MAX_RESULT_CACHE_SIZE=100" in env_text
        assert "METHODOLOGY_GATE_MODE=observe" in env_text
        assert "OPENAI_API_KEY=change_me" not in env_text
        assert not report.errors
    finally:
        shutil.rmtree(project_root, ignore_errors=True)


def test_invalid_methodology_mode_is_error() -> None:
    report = production_check.CheckReport()

    production_check._check_methodology_env(report, "bad", "")

    assert report.errors


def test_optional_provider_keys_do_not_warn_when_provider_is_not_enabled() -> None:
    project_root = _make_case_dir()
    (project_root / ".env.example").write_text(
        "\n".join(
            [
                "DATABASE_URL=postgresql://content_user:change_me@localhost:5432/content_generator",
                "JWT_SECRET_KEY=change_me",
                "OPENAI_API_KEY=",
                "OPENAI_THEORY_MODEL=",
                "DEEPSEEK_API_KEY=",
                "DEEPSEEK_THEORY_MODEL=",
                "GIGACHAT_CREDENTIALS=",
                "GIGACHAT_THEORY_MODEL=",
                "LANGFUSE_PUBLIC_KEY=",
                "LANGFUSE_SECRET_KEY=",
                "OBSERVABILITY_EXPORTERS=",
                "LLM_BUDGET_USD_PER_ROLE=",
            ]
        ),
        encoding="utf-8",
    )
    (project_root / ".env").write_text(
        "\n".join(
            [
                "DATABASE_URL=postgresql://content_user:pass123@localhost:5432/content_generator",
                "JWT_SECRET_KEY=real-secret",
                "OPENAI_API_KEY=real-key",
            ]
        ),
        encoding="utf-8",
    )
    report = production_check.CheckReport()

    try:
        production_check.check_env_files(report, project_root)

        assert not report.errors
        assert not report.warnings
    finally:
        shutil.rmtree(project_root, ignore_errors=True)


def test_polza_provider_key_satisfies_llm_check() -> None:
    project_root = _make_case_dir()
    (project_root / ".env.example").write_text(
        "\n".join(
            [
                "DATABASE_URL=postgresql://content_user:change_me@localhost:5432/content_generator",
                "JWT_SECRET_KEY=change_me",
                "LLM_PROVIDER=polza",
                "POLZA_AI_API_KEY=",
                "OPENAI_API_KEY=",
                "DEEPSEEK_API_KEY=",
            ]
        ),
        encoding="utf-8",
    )
    (project_root / ".env").write_text(
        "\n".join(
            [
                "DATABASE_URL=postgresql://content_user:pass123@localhost:5432/content_generator",
                "JWT_SECRET_KEY=real-secret",
                "LLM_PROVIDER=polza",
                "POLZA_AI_API_KEY=real-polza-key",
            ]
        ),
        encoding="utf-8",
    )
    report = production_check.CheckReport()

    try:
        production_check.check_env_files(report, project_root)

        assert not report.errors
        assert not report.warnings
    finally:
        shutil.rmtree(project_root, ignore_errors=True)


def _secure_prod_env() -> dict[str, str]:
    return {
        "ENVIRONMENT": "production",
        "DISABLE_AUTH": "false",
        "AUTH_COOKIE_SECURE": "true",
        "RELOAD": "false",
        "JWT_SECRET_KEY": "a-strong-random-secret",
    }


def test_production_security_is_advisory_outside_production() -> None:
    report = production_check.CheckReport()
    env = {"DISABLE_AUTH": "true", "AUTH_COOKIE_SECURE": "false", "JWT_SECRET_KEY": "change_me"}

    production_check.check_production_security(report, env, force_production=False)

    # dev .env (auth bypass, insecure cookie) must not fail the check locally
    assert not report.errors


def test_production_security_passes_on_secure_config() -> None:
    report = production_check.CheckReport()

    production_check.check_production_security(report, _secure_prod_env(), force_production=False)

    assert not report.errors


def test_production_disable_auth_is_error() -> None:
    report = production_check.CheckReport()
    env = {**_secure_prod_env(), "DISABLE_AUTH": "true"}

    production_check.check_production_security(report, env, force_production=False)

    assert any("DISABLE_AUTH" in message for message in report.errors)


def test_production_insecure_cookie_is_error() -> None:
    report = production_check.CheckReport()
    env = {**_secure_prod_env(), "AUTH_COOKIE_SECURE": "false"}

    production_check.check_production_security(report, env, force_production=False)

    assert any("AUTH_COOKIE_SECURE" in message for message in report.errors)


def test_production_reload_enabled_is_error() -> None:
    report = production_check.CheckReport()
    env = {**_secure_prod_env(), "RELOAD": "true"}

    production_check.check_production_security(report, env, force_production=False)

    assert any("RELOAD" in message for message in report.errors)


def test_production_default_jwt_secret_is_error() -> None:
    report = production_check.CheckReport()
    env = {**_secure_prod_env(), "JWT_SECRET_KEY": "your-secret-key-change-in-production"}

    production_check.check_production_security(report, env, force_production=False)

    assert any("JWT_SECRET_KEY" in message for message in report.errors)


def test_production_flag_forces_enforcement_without_environment_key() -> None:
    report = production_check.CheckReport()
    env = {"DISABLE_AUTH": "true"}  # no ENVIRONMENT=production, but flag forces it

    production_check.check_production_security(report, env, force_production=True)

    assert any("DISABLE_AUTH" in message for message in report.errors)


def test_known_insecure_jwt_default_always_errors() -> None:
    project_root = _make_case_dir()
    (project_root / ".env.example").write_text(
        "\n".join(
            [
                "DATABASE_URL=postgresql://content_user:change_me@localhost:5432/content_generator",
                "JWT_SECRET_KEY=change_me",
            ]
        ),
        encoding="utf-8",
    )
    (project_root / ".env").write_text(
        "\n".join(
            [
                "DATABASE_URL=postgresql://content_user:pass123@localhost:5432/content_generator",
                "JWT_SECRET_KEY=your-secret-key-change-in-production",
            ]
        ),
        encoding="utf-8",
    )
    report = production_check.CheckReport()

    try:
        production_check.check_env_files(report, project_root)
        assert any("insecure default" in message for message in report.errors)
    finally:
        shutil.rmtree(project_root, ignore_errors=True)


def test_dedupe_env_preserves_last_values_and_removes_stale_keys() -> None:
    project_root = _make_case_dir()
    (project_root / ".env.example").write_text(
        "\n".join(
            [
                "DATABASE_URL=postgresql://content_user:change_me@localhost:5432/content_generator",
                "JWT_SECRET_KEY=change_me",
                "OPENAI_API_KEY=",
                "METHODOLOGY_GATE_MODE=observe",
            ]
        ),
        encoding="utf-8",
    )
    (project_root / ".env").write_text(
        "\n".join(
            [
                "DATABASE_URL=postgresql://old:old@localhost:5432/old",
                "RAG_MIN_RETURNED=15",
                "JWT_SECRET_KEY=first",
                "JWT_SECRET_KEY=second",
                "OPENAI_API_KEY=real-key",
                "LOCAL_ONLY=value",
            ]
        ),
        encoding="utf-8",
    )
    report = production_check.CheckReport()

    try:
        production_check.check_env_files(report, project_root, dedupe_env=True)

        env_text = (project_root / ".env").read_text(encoding="utf-8")
        assert "JWT_SECRET_KEY=second" in env_text
        assert "JWT_SECRET_KEY=first" not in env_text
        assert "RAG_MIN_RETURNED" not in env_text
        assert "LOCAL_ONLY=value" in env_text
    finally:
        shutil.rmtree(project_root, ignore_errors=True)
