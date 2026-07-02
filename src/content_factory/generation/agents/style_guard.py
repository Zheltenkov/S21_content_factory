"""Compatibility import for the former StyleGuardAgent location.

New code should import deterministic style repair from
``content_gen.repair.style_guard``.
"""

from ..repair.style_guard import LintIssue, StyleGuardAgent, StyleGuardRepair

__all__ = ["LintIssue", "StyleGuardAgent", "StyleGuardRepair"]
