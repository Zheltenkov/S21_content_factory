"""Repair components for deterministic or model-backed corrections.

Repair modules transform existing artifacts. They are separate from LLM agents
and validators so workflow code can reason about responsibility boundaries.
"""

from .style_guard import LintIssue, StyleGuardRepair

__all__ = [
    "LintIssue",
    "StyleGuardRepair",
]
