"""
Утилиты для преобразования Mermaid-диаграмм в изображения.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

MERMAID_BLOCK_RE = re.compile(r"```mermaid\s*\n(?P<body>.+?)```", re.DOTALL | re.IGNORECASE)
MERMAID_INIT_RE = re.compile(r"^\s*%%\{init:[\s\S]*?\}%%\s*", re.IGNORECASE)

KROKI_ENDPOINT = "https://kroki.io/mermaid/png"
DEFAULT_EXPORT_MODE = "none"
DEFAULT_KROKI_TIMEOUT_SECONDS = 4.0
DEFAULT_LOCAL_TIMEOUT_SECONDS = 8.0
DEFAULT_MAX_DIAGRAMS = 4
THEME_INIT = (
    '%%{init: {"theme":"base","flowchart":{'
    '"htmlLabels":true,"curve":"basis","padding":18,"nodeSpacing":68,"rankSpacing":82,'
    '"wrappingWidth":230,"useMaxWidth":true},"themeVariables":{'
    '"primaryColor":"#ffffff","primaryTextColor":"#111820","primaryBorderColor":"#9aa79d",'
    '"lineColor":"#334238","secondaryColor":"#eef4ef","tertiaryColor":"#f7faf6",'
    '"background":"#ffffff","mainBkg":"#ffffff","secondBkg":"#eef4ef","textColor":"#111820",'
    '"border1":"#9aa79d","border2":"#7f8d83","arrowheadColor":"#334238",'
    '"edgeLabelBackground":"#ffffff",'
    '"actorBkg":"#ffffff","actorBorder":"#9aa79d","actorTextColor":"#111820",'
    '"actorLineColor":"#334238","signalColor":"#334238","signalTextColor":"#111820",'
    '"labelBoxBkgColor":"#ffffff","labelBoxBorderColor":"#9aa79d",'
    '"noteBkgColor":"#f7faf6","noteTextColor":"#111820",'
    '"activationBkgColor":"#eef4ef","activationBorderColor":"#9aa79d",'
    '"fontSize":"18px",'
    '"fontFamily":"-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Arial, sans-serif"'
    '}}}%%'
)


class MermaidRendererUnavailable(RuntimeError):
    """Raised when an optional Mermaid renderer is not configured."""


def convert_mermaid_blocks(
    md: str,
    *,
    export_mode: str | None = None,
) -> tuple[str, list[dict[str, bytes]]]:
    """
    Optionally replaces ```mermaid``` blocks with PNG image links.

    By default this function keeps Mermaid source blocks intact. The web UI and
    GitHub/GitLab can render Mermaid directly, so finalization must not depend on
    a remote renderer. PNG export is an optional best-effort path for archives.

    Env:
        MERMAID_EXPORT_MODE:
            none/off/disabled - keep Mermaid blocks, no network calls (default)
            local - use local Mermaid CLI (`mmdc`) only
            kroki/remote - use Kroki API only
            auto/best_effort - try local CLI, then Kroki
        MERMAID_EXPORT_MAX_DIAGRAMS: max exported diagrams per markdown
        MERMAID_KROKI_TIMEOUT_SECONDS: per-diagram Kroki timeout
        MERMAID_LOCAL_TIMEOUT_SECONDS: per-diagram local CLI timeout

    Returns:
        (updated_markdown, [{"name": "...", "data": b"..."}])
    """
    mode = _normalize_export_mode(export_mode)
    if mode == "none":
        return md, []
    unavailable_reason = _preflight_unavailable_reason(mode)
    if unavailable_reason:
        logger.info("Mermaid PNG export disabled: %s; source blocks preserved", unavailable_reason)
        return md, []

    assets: list[dict[str, bytes]] = []
    attempts = 0
    max_diagrams = _int_env("MERMAID_EXPORT_MAX_DIAGRAMS", DEFAULT_MAX_DIAGRAMS)
    reported_failures: set[str] = set()

    def _replace(match: re.Match) -> str:
        nonlocal attempts
        code = match.group("body").strip()
        if not code:
            return match.group(0)
        if attempts >= max_diagrams:
            logger.info(
                "Mermaid PNG export skipped after %s diagrams; source block preserved",
                max_diagrams,
            )
            return match.group(0)

        attempts += 1
        image_name = f"diagram_{attempts}.png"
        try:
            png_bytes = _render_mermaid(code, mode=mode)
        except Exception as exc:
            failure = str(exc)
            if failure not in reported_failures:
                logger.warning("⚠️ Не удалось экспортировать Mermaid PNG: %s; source blocks preserved", failure)
                reported_failures.add(failure)
            else:
                logger.info("Mermaid PNG export skipped for %s after repeated renderer failure", image_name)
            return match.group(0)

        assets.append({"name": image_name, "data": png_bytes})
        return f"![Диаграмма {len(assets)}](images/{image_name})"

    updated_md = MERMAID_BLOCK_RE.sub(_replace, md)
    return updated_md, assets


def _preflight_unavailable_reason(mode: str) -> str | None:
    """Return a stable reason when the selected renderer is known to be unavailable."""
    if mode == "local" and not (os.getenv("MERMAID_CLI_PATH") or shutil.which("mmdc")):
        return "local Mermaid CLI `mmdc` is not installed"
    return None


def _render_mermaid(code: str, *, mode: str = "auto") -> bytes:
    """Render Mermaid through configured best-effort backends."""
    renderers = []
    if mode in {"auto", "local"}:
        renderers.append(_render_mermaid_local)
    if mode in {"auto", "kroki"}:
        renderers.append(_render_mermaid_kroki)

    last_error: Exception | None = None
    for renderer in renderers:
        try:
            return renderer(code)
        except MermaidRendererUnavailable as exc:
            last_error = exc
            logger.debug("Mermaid renderer unavailable: %s", exc)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("Mermaid renderer failed: %s", exc)

    raise last_error or MermaidRendererUnavailable(f"No Mermaid renderer configured for mode '{mode}'")


def _render_mermaid_local(code: str) -> bytes:
    """Render Mermaid via local Mermaid CLI (`mmdc`) when it is installed."""
    code_with_theme = _ensure_theme(code)
    mmdc_path = os.getenv("MERMAID_CLI_PATH") or shutil.which("mmdc")
    if not mmdc_path:
        raise MermaidRendererUnavailable("local Mermaid CLI `mmdc` is not installed")

    timeout = _float_env("MERMAID_LOCAL_TIMEOUT_SECONDS", DEFAULT_LOCAL_TIMEOUT_SECONDS)
    with tempfile.TemporaryDirectory(prefix="content-gen-mermaid-") as tmp_dir:
        input_path = Path(tmp_dir) / "diagram.mmd"
        output_path = Path(tmp_dir) / "diagram.png"
        input_path.write_text(code_with_theme, encoding="utf-8")

        completed = subprocess.run(
            [
                mmdc_path,
                "-i",
                str(input_path),
                "-o",
                str(output_path),
                "-b",
                "white",
            ],
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(stderr or f"mmdc exited with code {completed.returncode}")
        return output_path.read_bytes()


def _render_mermaid_kroki(code: str) -> bytes:
    """Send Mermaid code to Kroki and return PNG bytes."""
    code_with_theme = _ensure_theme(code)
    timeout = _float_env("MERMAID_KROKI_TIMEOUT_SECONDS", DEFAULT_KROKI_TIMEOUT_SECONDS)

    resp = requests.post(
        KROKI_ENDPOINT,
        data=code_with_theme.encode("utf-8"),
        timeout=timeout,
        headers={"Content-Type": "text/plain; charset=utf-8"},
    )
    resp.raise_for_status()
    return resp.content


def _ensure_theme(code: str) -> str:
    """Force the product Mermaid theme before exporting static images."""
    body = MERMAID_INIT_RE.sub("", code.strip(), count=1).strip()
    return f"{THEME_INIT}\n{body}"


def _normalize_export_mode(export_mode: str | None) -> str:
    """Normalize export mode from explicit argument or environment."""
    raw_value = (export_mode if export_mode is not None else os.getenv("MERMAID_EXPORT_MODE", DEFAULT_EXPORT_MODE))
    normalized = (raw_value or DEFAULT_EXPORT_MODE).strip().lower()
    aliases = {
        "": "none",
        "0": "none",
        "false": "none",
        "off": "none",
        "disabled": "none",
        "disable": "none",
        "no": "none",
        "remote": "kroki",
        "api": "kroki",
        "best_effort": "auto",
        "best-effort": "auto",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"none", "local", "kroki", "auto"}:
        logger.warning("Unknown MERMAID_EXPORT_MODE=%r, Mermaid PNG export disabled", raw_value)
        return "none"
    return normalized


def _float_env(name: str, default: float) -> float:
    """Read a positive float env value with fallback."""
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _int_env(name: str, default: int) -> int:
    """Read a positive int env value with fallback."""
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default
