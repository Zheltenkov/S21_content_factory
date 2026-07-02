"""Compatibility import for the former TOCAgent location.

New code should import deterministic TOC rendering from
``content_gen.renderers.toc``.
"""

from ..renderers.toc import TOCAgent, TOCRenderer, TOCResult

__all__ = ["TOCAgent", "TOCRenderer", "TOCResult"]
