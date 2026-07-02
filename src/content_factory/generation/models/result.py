"""Result contracts for content generation orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .schemas import ProjectSpec


@dataclass
class OrchestratorResult:
    """Final generation result returned by the application orchestration layer."""

    spec: ProjectSpec
    warnings: list[str]
    report_json: dict[str, Any]
    assets: dict[str, Any] | None = None
    practice_critic_issues: list[dict[str, Any]] | None = None
    agent_config_versions: dict[str, str] | None = None
    flow_trace: list[dict[str, Any]] | None = None
    methodology_reviews: list[dict[str, Any]] | None = None
    methodology_repairs: list[dict[str, Any]] | None = None
    methodology_gate_decisions: list[dict[str, Any]] | None = None
