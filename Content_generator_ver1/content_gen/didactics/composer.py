"""Didactics prompt composer for agent bindings."""

from __future__ import annotations

from functools import lru_cache
import logging
from pathlib import Path
from typing import Any

from .loader import DidacticsManifest, load_didactics_manifest

logger = logging.getLogger("content_gen.didactics.composer")


@lru_cache(maxsize=1)
def _load_manifest_cached() -> DidacticsManifest:
    return load_didactics_manifest()


def compose_didactics_context(agent_name: str) -> tuple[str, dict[str, Any]]:
    """
    Compose didactics context for a specific agent.

    Returns:
        (context_text, trace_meta)
    """
    manifest = _load_manifest_cached()
    base_dir = Path(__file__).parent

    requested_ids = manifest.agent_bindings.get(agent_name, [])
    collected_paths: list[str] = []
    used_ids: list[str] = []
    missing_ids: list[str] = []
    missing_files: list[str] = []

    skill_by_id = {s.id: s for s in manifest.skill_specs}
    for skill_id in requested_ids:
        spec = skill_by_id.get(skill_id)
        if spec is None:
            missing_ids.append(skill_id)
            continue
        if spec.file:
            collected_paths.append(spec.file)
            used_ids.append(skill_id)

    if missing_ids and manifest.mode == "strict":
        raise KeyError(f"Didactics binding for '{agent_name}' references unknown skill ids: {missing_ids}")

    parts = [f"didactics_manifest_version={manifest.version}"]
    for rel_path in collected_paths:
        full_path = (base_dir / rel_path).resolve()
        if not full_path.exists():
            missing_files.append(str(full_path))
            continue
        content = full_path.read_text(encoding="utf-8").strip()
        if content:
            parts.append(content)
    if missing_files:
        if manifest.mode == "strict":
            raise FileNotFoundError(
                f"Didactics binding for '{agent_name}' references missing files: {missing_files}"
            )
        logger.warning("Didactics binding for '%s' has missing files: %s", agent_name, missing_files)
    context = "\n\n".join(parts).strip() if len(parts) > 1 else ""

    trace = {
        "didactics_bundle_version": manifest.version,
        "didactics_mode": manifest.mode,
        "didactics_skills_used": used_ids,
        "didactics_missing_skill_ids": missing_ids,
        "didactics_missing_files": missing_files,
        "didactics_agent": agent_name,
    }
    return context, trace


def get_didactics_trace() -> dict[str, Any]:
    """Return high-level didactics trace metadata."""
    manifest = _load_manifest_cached()
    return {
        "didactics_bundle_version": manifest.version,
        "didactics_mode": manifest.mode,
        "didactics_bindings": manifest.agent_bindings,
    }
