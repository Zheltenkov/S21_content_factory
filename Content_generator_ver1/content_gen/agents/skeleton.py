"""Compatibility import for the former SkeletonAgent location.

New code should import deterministic skeleton rendering from
``content_gen.renderers.skeleton``.
"""

from ..renderers.skeleton import LOCALE, SkeletonAgent, SkeletonParts, SkeletonRenderer

__all__ = ["LOCALE", "SkeletonAgent", "SkeletonParts", "SkeletonRenderer"]
