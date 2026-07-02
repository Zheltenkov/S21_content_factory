"""LLM-backed generation agents and legacy compatibility imports.

Exports stay lazy so callers can import lightweight agent contracts without
pulling optional subsystems eagerly.
"""

from __future__ import annotations

__all__ = [
    "IntentMapper",
    "IntentWarnings",
    "ContextAnalysisResult",
]


def __getattr__(name: str):
    """Lazy-load agent exports on demand."""
    if name in {"IntentMapper", "IntentWarnings"}:
        from .intent_mapper import IntentMapper, IntentWarnings

        exports = {
            "IntentMapper": IntentMapper,
            "IntentWarnings": IntentWarnings,
        }
        return exports[name]

    if name == "ContextAnalysisResult":
        from .context_analysis import ContextAnalysisResult

        exports = {
            "ContextAnalysisResult": ContextAnalysisResult,
        }
        return exports[name]

    raise AttributeError(f"module 'content_gen.agents' has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
