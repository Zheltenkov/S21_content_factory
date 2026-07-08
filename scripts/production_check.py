"""Production readiness checks for the content generator service."""

from __future__ import annotations

import argparse
import importlib
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_ENV_KEYS = {
    "DATABASE_URL",
    "JWT_SECRET_KEY",
}
PROVIDER_REQUIRED_ENV_KEYS = {
    "polza": ("POLZA_AI_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "azure": ("AZURE_OPENAI_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "gigachat": ("GIGACHAT_CREDENTIALS", "GIGACHAT_API_KEY"),
}
PROVIDER_ENV_PREFIXES = {
    "POLZA_AI_": "polza",
    "POLZA_": "polza",
    "OPENAI_": "openai",
    "AZURE_OPENAI_": "azure",
    "DEEPSEEK_": "deepseek",
    "GIGACHAT_": "gigachat",
}
SECRET_KEY_PARTS = ("KEY", "TOKEN", "SECRET", "PASSWORD")
OPTIONAL_EMPTY_DEFAULT_KEYS = {"MERMAID_CLI_PATH", "METHODOLOGY_HUMAN_CHECKPOINTS", "REDIS_URL"}
OPTIONAL_MISSING_ENV_KEYS = {
    "LLM_BUDGET_USD_PER_ROLE",
    "METHODOLOGY_ASSISTANT_MODEL",
    "OBSERVABILITY_EXPORTERS",
}
STALE_KEY_PREFIXES = ("RAG_", "OPEN_ROUTER_", "OPENROUTER_")
KNOWN_STALE_KEYS = {"ACCESS_PASSWORD", "OPENAI_USE_RESPONSES"}
GENERATED_SECRET_KEYS = {"JWT_SECRET_KEY"}

# Hardcoded fallback secrets that must never reach any deployed environment.
INSECURE_JWT_DEFAULTS = {"your-secret-key-change-in-production"}
# Values treated as "on" for boolean env flags (mirrors runtime parsing).
TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}
# ENVIRONMENT values that switch on the production security gate.
PRODUCTION_ENV_VALUES = {"production", "prod"}

IMPORT_CHECKS = {
    "pydantic[email]": "pydantic",
    "openai": "openai",
    "python-dotenv": "dotenv",
    "pandas": "pandas",
    "numpy": "numpy",
    "openpyxl": "openpyxl",
    "requests": "requests",
    "httpx": "httpx",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "python-multipart": "multipart",
    "python-jose": "jose",
    "passlib": "passlib",
    "slowapi": "slowapi",
    "sqlalchemy": "sqlalchemy",
    "alembic": "alembic",
    "psycopg2-binary": "psycopg2",
    "redis": "redis",
    "psutil": "psutil",
    "pyyaml": "yaml",
}


@dataclass
class CheckReport:
    """Collect check findings and expose a deterministic exit status."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    def ok(self, message: str) -> None:
        self.info.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def error(self, message: str) -> None:
        self.errors.append(message)

    @property
    def exit_code(self) -> int:
        return 1 if self.errors else 0


def parse_env_file(path: Path) -> tuple[dict[str, str], dict[str, list[int]]]:
    """Parse dotenv-like key/value pairs without expanding or printing secrets."""
    values: dict[str, str] = {}
    locations: dict[str, list[int]] = {}
    if not path.exists():
        return values, locations

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        values[key] = value.strip()
        locations.setdefault(key, []).append(line_number)
    return values, locations


def _is_secret_key(key: str) -> bool:
    upper = key.upper()
    return any(part in upper for part in SECRET_KEY_PARTS)


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized in {"", "change_me", "your_password", "your_smtp_password", "your_api_key"}


def _is_safe_default(key: str, value: str) -> bool:
    if key in OPTIONAL_EMPTY_DEFAULT_KEYS:
        return True
    return not _is_secret_key(key) and not _is_placeholder(value)


def _langfuse_is_requested(env: dict[str, str]) -> bool:
    exporters = {part.strip().lower() for part in env.get("OBSERVABILITY_EXPORTERS", "").split(",") if part.strip()}
    return env.get("LANGFUSE_ENABLED", "").lower() == "true" or "langfuse" in exporters


def _is_optional_missing_key(key: str, env: dict[str, str]) -> bool:
    """Return True for optional provider/config keys that may be absent locally."""
    if key in OPTIONAL_MISSING_ENV_KEYS:
        return True
    active_provider = _active_llm_provider(env)
    provider = _provider_for_env_key(key)
    if provider and provider != active_provider:
        return True
    if provider == active_provider and key not in PROVIDER_REQUIRED_ENV_KEYS.get(provider, ()):
        return True
    if key.startswith("OPENAI_") and key.endswith("_MODEL"):
        return True
    if key.startswith("LANGFUSE_") and not _langfuse_is_requested(env):
        return True
    return False


def _active_llm_provider(env: dict[str, str]) -> str:
    """Return configured LLM provider; Polza AI is the production default."""
    raw_provider = env.get("LLM_PROVIDER", "").strip()
    if raw_provider:
        normalized = raw_provider.lower().replace("open_router", "openrouter")
        return "polza" if normalized in {"gpt", "openrouter", "polza_ai"} else normalized
    for provider, keys in PROVIDER_REQUIRED_ENV_KEYS.items():
        if any(not _is_placeholder(env.get(key, "")) for key in keys):
            return provider
    return "polza"


def _provider_for_env_key(key: str) -> str | None:
    """Map provider-prefixed env keys to the provider that owns them."""
    for prefix, provider in PROVIDER_ENV_PREFIXES.items():
        if key.startswith(prefix):
            return provider
    return None


def check_env_files(
    report: CheckReport,
    project_root: Path,
    write_missing_defaults: bool = False,
    generate_missing_secrets: bool = False,
    dedupe_env: bool = False,
) -> None:
    """Validate .env coverage and optionally append safe non-secret defaults."""
    example_path = project_root / ".env.example"
    env_path = project_root / ".env"
    example, example_locations = parse_env_file(example_path)
    env, env_locations = parse_env_file(env_path)

    if not example:
        report.error(".env.example is missing or empty")
        return
    report.ok(f".env.example keys: {len(example)}")

    duplicate_example = sorted(key for key, lines in example_locations.items() if len(lines) > 1)
    if duplicate_example:
        report.error(f".env.example has duplicate keys: {', '.join(duplicate_example)}")

    if not env_path.exists():
        report.error(".env is missing")
        return
    report.ok(f".env keys: {len(env)}")

    duplicate_env = sorted(key for key, lines in env_locations.items() if len(lines) > 1)
    if duplicate_env:
        report.warn(f".env has duplicate keys: {', '.join(duplicate_env)}")

    generated_keys = []
    if generate_missing_secrets:
        for key in sorted(GENERATED_SECRET_KEYS):
            if key in example and _is_placeholder(env.get(key, "")):
                secret_value = secrets.token_urlsafe(48)
                _append_values(env_path, {key: secret_value}, "Generated by scripts/production_check.py")
                env[key] = secret_value
                env_locations.setdefault(key, []).append(-1)
                generated_keys.append(key)
        if generated_keys:
            report.ok(f"Generated missing production secrets in .env: {', '.join(generated_keys)}")

    missing_keys_all = sorted(set(example) - set(env))
    missing_keys = [key for key in missing_keys_all if not _is_optional_missing_key(key, env)]
    if missing_keys:
        safe_missing = [key for key in missing_keys if _is_safe_default(key, example[key])]
        unsafe_missing = sorted(set(missing_keys) - set(safe_missing))
        if write_missing_defaults and safe_missing:
            _append_missing_defaults(env_path, example, safe_missing)
            for key in safe_missing:
                env[key] = example[key]
                env_locations.setdefault(key, []).append(-1)
            report.ok(f"Appended safe missing defaults to .env: {', '.join(safe_missing)}")
        if unsafe_missing:
            report.warn(f".env is missing keys that need manual values: {', '.join(unsafe_missing)}")
        elif safe_missing:
            report.ok(f".env relies on documented code defaults for {len(safe_missing)} optional keys")

    stale_keys = sorted(
        key
        for key in env
        if key in KNOWN_STALE_KEYS or any(key.startswith(prefix) for prefix in STALE_KEY_PREFIXES)
    )
    if stale_keys:
        report.warn(f".env contains stale keys ignored by current runtime: {', '.join(stale_keys)}")

    missing_required = sorted(key for key in REQUIRED_ENV_KEYS if _is_placeholder(env.get(key, "")))
    if missing_required:
        report.error(f"Required production env values are empty/placeholders: {', '.join(missing_required)}")

    if env.get("JWT_SECRET_KEY", "").strip() in INSECURE_JWT_DEFAULTS:
        report.error("JWT_SECRET_KEY is set to a known insecure default; generate a strong secret")

    active_provider = _active_llm_provider(env)
    active_provider_keys = PROVIDER_REQUIRED_ENV_KEYS.get(active_provider, ())
    if not any(not _is_placeholder(env.get(key, "")) for key in active_provider_keys):
        names = " or ".join(active_provider_keys) or "a provider API key"
        report.error(f"No production LLM API key configured for provider '{active_provider}': set {names}")

    _check_database_url(report, env.get("DATABASE_URL", ""))
    _check_methodology_env(report, env.get("METHODOLOGY_GATE_MODE", "observe"), env.get("METHODOLOGY_HUMAN_CHECKPOINTS", ""))
    _check_mermaid_env(report, env.get("MERMAID_EXPORT_MODE", "none"))

    if dedupe_env:
        _rewrite_env(env_path, example, env)
        report.ok(".env normalized: duplicate keys removed, current values preserved")


def _append_missing_defaults(env_path: Path, example: dict[str, str], keys: list[str]) -> None:
    """Append safe missing values while preserving existing secrets and comments."""
    values = {key: example[key] for key in sorted(keys)}
    _append_values(env_path, values, "Added by scripts/production_check.py")


def _append_values(env_path: Path, values: dict[str, str], header: str) -> None:
    """Append exact key/value pairs to .env without printing their values."""
    lines = ["", f"# {header}"]
    for key, value in values.items():
        lines.append(f"{key}={value}")
    with env_path.open("a", encoding="utf-8", newline="") as file:
        file.write("\n".join(lines) + "\n")


def _rewrite_env(env_path: Path, example: dict[str, str], env: dict[str, str]) -> None:
    """Rewrite .env in .env.example order, preserving last known values."""
    lines = ["# Normalized by scripts/production_check.py"]
    stale_keys = set(KNOWN_STALE_KEYS)
    for prefix in STALE_KEY_PREFIXES:
        stale_keys.update(key for key in env if key.startswith(prefix))

    for key in example:
        if key in env:
            lines.append(f"{key}={env[key]}")

    extra_keys = sorted(set(env) - set(example) - stale_keys)
    if extra_keys:
        lines.append("")
        lines.append("# Extra local values not documented in .env.example")
        for key in extra_keys:
            lines.append(f"{key}={env[key]}")

    env_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _check_database_url(report: CheckReport, database_url: str) -> None:
    if not database_url:
        return
    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgresql", "postgresql+psycopg2"}:
        report.error("DATABASE_URL must use postgresql:// or postgresql+psycopg2://")
        return
    if not parsed.hostname or not parsed.path.strip("/"):
        report.error("DATABASE_URL must include host and database name")
        return
    report.ok(f"DATABASE_URL target: {parsed.scheme}://{parsed.hostname}:{parsed.port or 5432}{parsed.path}")


def _check_methodology_env(report: CheckReport, mode: str, checkpoints: str) -> None:
    if mode not in {"observe", "approval", "strict"}:
        report.error("METHODOLOGY_GATE_MODE must be one of: observe, approval, strict")
    allowed = {"", "all", "off", "none", "disabled", "annotation", "theory", "practice", "quality", "evaluation"}
    values = {part.strip() for part in checkpoints.split(",") if part.strip()}
    unknown = sorted(value for value in values if value not in allowed)
    if unknown:
        report.error(f"METHODOLOGY_HUMAN_CHECKPOINTS has unknown values: {', '.join(unknown)}")


def _check_mermaid_env(report: CheckReport, mode: str) -> None:
    if mode not in {"none", "local", "kroki", "auto", "off", "disabled"}:
        report.error("MERMAID_EXPORT_MODE must be one of: none, local, kroki, auto")
    if mode in {"kroki", "auto"}:
        report.warn("MERMAID_EXPORT_MODE uses remote rendering; keep 'none' or 'local' for isolated production")


def _is_truthy(value: str) -> bool:
    return value.strip().lower() in TRUTHY_ENV_VALUES


def _is_production(env: dict[str, str], force_production: bool) -> bool:
    """Production is signalled by --production or ENVIRONMENT=production in .env."""
    if force_production:
        return True
    return env.get("ENVIRONMENT", "").strip().lower() in PRODUCTION_ENV_VALUES


def check_production_security(report: CheckReport, env: dict[str, str], force_production: bool) -> None:
    """Hard-fail on unsafe auth/secret/cookie config when targeting production.

    These are advisory outside production so the dev ``.env`` (auth bypass on,
    non-secure cookies) still passes the check locally.
    """
    if not _is_production(env, force_production):
        report.ok("Environment is non-production; auth/cookie hardening checks are advisory")
        return

    if _is_truthy(env.get("DISABLE_AUTH", "false")):
        report.error("DISABLE_AUTH must not be enabled in production (authentication bypass)")
    if not _is_truthy(env.get("AUTH_COOKIE_SECURE", "false")):
        report.error("AUTH_COOKIE_SECURE must be true in production (auth cookie must be HTTPS-only)")
    if _is_truthy(env.get("RELOAD", "false")):
        report.error("RELOAD must be false in production")
    jwt_value = env.get("JWT_SECRET_KEY", "").strip()
    if jwt_value in INSECURE_JWT_DEFAULTS or _is_placeholder(jwt_value):
        report.error("JWT_SECRET_KEY must be a strong non-default secret in production")


def check_imports(report: CheckReport) -> None:
    """Verify that runtime packages from requirements can be imported."""
    missing: list[str] = []
    for package_name, module_name in IMPORT_CHECKS.items():
        try:
            importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{package_name} ({type(exc).__name__})")
    if missing:
        report.error(f"Missing or broken runtime imports: {', '.join(missing)}")
    else:
        report.ok("Runtime imports are available")


def check_database_connection(report: CheckReport, database_url: str, timeout_seconds: int) -> None:
    """Run an optional SELECT 1 against PostgreSQL using a masked result report."""
    if not database_url:
        report.error("Cannot check database: DATABASE_URL is empty")
        return
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(database_url, connect_args={"connect_timeout": timeout_seconds})
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        report.ok("Database connection check passed")
    except Exception as exc:  # noqa: BLE001
        report.error(f"Database connection check failed: {str(exc).splitlines()[0][:300]}")


def run_checks(args: argparse.Namespace) -> CheckReport:
    report = CheckReport()
    check_env_files(
        report,
        PROJECT_ROOT,
        write_missing_defaults=args.write_missing_defaults,
        generate_missing_secrets=args.generate_missing_secrets,
        dedupe_env=args.dedupe_env,
    )
    check_imports(report)
    env, _ = parse_env_file(PROJECT_ROOT / ".env")
    check_production_security(report, env, force_production=args.production)
    if args.check_db:
        check_database_connection(report, env.get("DATABASE_URL", ""), args.db_timeout)
    return report


def print_report(report: CheckReport) -> None:
    for message in report.info:
        print(f"OK    {message}")
    for message in report.warnings:
        print(f"WARN  {message}")
    for message in report.errors:
        print(f"ERROR {message}")
    print(f"SUMMARY errors={len(report.errors)} warnings={len(report.warnings)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check production readiness without printing secrets.")
    parser.add_argument("--check-db", action="store_true", help="Run SELECT 1 against DATABASE_URL.")
    parser.add_argument(
        "--production",
        action="store_true",
        help="Enforce production security (fail on DISABLE_AUTH, insecure cookie, default/placeholder secret, RELOAD).",
    )
    parser.add_argument("--db-timeout", type=int, default=3, help="PostgreSQL connect timeout in seconds.")
    parser.add_argument(
        "--write-missing-defaults",
        action="store_true",
        help="Append safe non-secret defaults from .env.example to .env.",
    )
    parser.add_argument(
        "--generate-missing-secrets",
        action="store_true",
        help="Generate missing local production secrets such as JWT_SECRET_KEY.",
    )
    parser.add_argument(
        "--dedupe-env",
        action="store_true",
        help="Rewrite .env once, preserving current last values and removing stale duplicate keys.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_checks(args)
    print_report(report)
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
