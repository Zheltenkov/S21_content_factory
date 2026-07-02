"""Генератор контента учебных проектов.

Top-level package keeps imports lazy, so lightweight modules can be used without
pulling generation orchestration dependencies at import time.
"""

from __future__ import annotations

from .models import ProjectSeed, ProjectSpec

__all__ = [
    "Orchestrator",
    "OrchestratorResult",
    "ProjectSeed",
    "ProjectSpec",
]


def __getattr__(name: str):
    """Lazy-load heavy exports on first access."""
    if name in {"Orchestrator", "OrchestratorResult"}:
        from .orchestrator import Orchestrator, OrchestratorResult

        exports = {
            "Orchestrator": Orchestrator,
            "OrchestratorResult": OrchestratorResult,
        }
        return exports[name]

    raise AttributeError(f"module 'content_gen' has no attribute {name!r}")


def __dir__() -> list[str]:
    """Expose lazy exports in interactive environments."""
    return sorted(__all__)
