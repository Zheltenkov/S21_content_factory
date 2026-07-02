"""Backwards-compat shim. LLM/node observability moved to
content_factory.platform.observability during the platform-core unification.
Kept so existing `content_factory.generation.observability` importers keep
working; scheduled for removal in the final cleanup phase.
"""

from content_factory.platform.observability import *  # noqa: F401,F403
