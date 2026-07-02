"""Boundary tests for agent/renderer/repair naming."""

from content_factory.generation.agents.skeleton import SkeletonAgent
from content_factory.generation.agents.style_guard import StyleGuardAgent
from content_factory.generation.agents.toc import TOCAgent
from content_factory.generation.renderers import SkeletonRenderer, TOCRenderer
from content_factory.generation.repair import StyleGuardRepair


def test_deterministic_renderers_have_canonical_non_agent_imports() -> None:
    """TOC and skeleton builders are renderers, not LLM agents."""
    assert TOCRenderer is TOCAgent
    assert SkeletonRenderer is SkeletonAgent


def test_deterministic_style_guard_has_canonical_repair_import() -> None:
    """Style guard is deterministic repair; old Agent name is compatibility only."""
    assert StyleGuardRepair is StyleGuardAgent
