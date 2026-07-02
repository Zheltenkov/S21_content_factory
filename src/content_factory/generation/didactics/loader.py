"""Didactics manifest loader and renderer."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class DidacticsSkillSpec(BaseModel):
    """Single didactics skill declaration."""

    id: str
    file: str | None = None
    machine_rules: list[str] = Field(default_factory=list)
    severity: Literal["hard", "soft"] = "soft"


class DidacticsManifest(BaseModel):
    """Manifest with reusable didactics fragments."""

    schema_version: int = 1
    bundle_id: str = "default"
    version: str
    mode: Literal["strict"] = "strict"
    default_language: str = "ru"
    skill_specs: list[DidacticsSkillSpec] = Field(default_factory=list)
    agent_bindings: dict[str, list[str]] = Field(default_factory=dict)
    # В манифесте пороги могут быть как плоскими ссылками:
    #   intro: "intro_words"
    # так и сгруппированными:
    #   intro: { words: "intro_words" }
    threshold_refs: dict[str, str | dict[str, str]] = Field(default_factory=dict)


def load_didactics_manifest(path: Path | None = None) -> DidacticsManifest:
    """Load didactics manifest from yaml file."""
    if path is None:
        path = Path(__file__).with_name("manifest.yaml")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return DidacticsManifest(**raw)


def build_didactics_context(manifest: DidacticsManifest, base_dir: Path | None = None) -> str:
    """Render strict skill specs into one prompt-ready text block."""
    base = base_dir or Path(__file__).parent
    parts: list[str] = [f"didactics_manifest_version={manifest.version}"]
    paths: list[str] = []
    for spec in manifest.skill_specs:
        if spec.file:
            paths.append(spec.file)
    for rel_path in paths:
        full_path = (base / rel_path).resolve()
        if not full_path.exists():
            if manifest.mode == "strict":
                raise FileNotFoundError(f"Didactics file is missing: {full_path}")
            continue
        content = full_path.read_text(encoding="utf-8").strip()
        if content:
            parts.append(content)
    return "\n\n".join(parts).strip()
