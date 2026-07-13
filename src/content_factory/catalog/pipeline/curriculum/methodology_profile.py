"""Versioned methodology profile (redirect step 2).

The current methodology is overfit to one program class. This module makes it ONE
versioned profile instead of global code: the profile OWNS the thresholds/policies that
vary by program family, while ``project_quality`` measures profile-independent facts and
the publication gate INTERPRETS those facts through a resolved profile.

A profile identity ``{profile_id, version}`` is snapshotted into the UP payload so the plan
is always evaluated by the profile it was built with — never silently re-scored by a newer
one. Resolution rules (see ``resolve_profile``): explicit + known → that profile; missing
field (legacy) → V1; explicit + unknown → None (draft opens, publish blocks). Nothing
executable/registry is serialized — only the identity.

Pure leaf: frozen dataclasses + stdlib (+ config constants for the V1 values).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .. import config

CapstonePolicy = Literal["follow_design", "always", "never"]
ResolutionStatus = Literal["resolved", "legacy_default", "unavailable"]


@dataclass(frozen=True)
class PublicationThresholds:
    """Profile-varying publication thresholds (interpreted by the gate)."""

    required_policy_coverage_pct: float
    single_skill_max_pct: float


@dataclass(frozen=True)
class MethodologyProfile:
    """A versioned program-family methodology: the thresholds/policies that are NOT global."""

    profile_id: str
    version: str
    program_family: str
    skill_density_range: tuple[int, int]
    single_skill_exempt_kinds: tuple[str, ...]
    capstone_policy: CapstonePolicy
    publication_thresholds: PublicationThresholds

    def snapshot(self) -> dict[str, str]:
        """The identity written into the UP payload (never the full profile)."""
        return {"profile_id": self.profile_id, "version": self.version}


#: First profile — reproduces the current behavior exactly. Named, versioned, default.
DIGITAL_PRODUCT_PROJECT_BASED_V1 = MethodologyProfile(
    profile_id="digital_product_project_based",
    version="1",
    program_family="digital_product_project_based",
    skill_density_range=(config.UP_TARGET_SKILLS_MIN, config.UP_TARGET_SKILLS_MAX),
    single_skill_exempt_kinds=("lab",),
    capstone_policy="follow_design",
    publication_thresholds=PublicationThresholds(
        required_policy_coverage_pct=100.0,
        single_skill_max_pct=25.0,
    ),
)

DEFAULT_PROFILE = DIGITAL_PRODUCT_PROJECT_BASED_V1

_PROFILES: dict[tuple[str, str], MethodologyProfile] = {
    (DIGITAL_PRODUCT_PROJECT_BASED_V1.profile_id, DIGITAL_PRODUCT_PROJECT_BASED_V1.version): DIGITAL_PRODUCT_PROJECT_BASED_V1,
}


@dataclass(frozen=True)
class ProfileResolution:
    """Result of resolving a payload profile snapshot to a known profile."""

    profile: MethodologyProfile | None
    status: ResolutionStatus


def resolve_profile(snapshot: dict[str, Any] | None) -> ProfileResolution:
    """Resolve a payload ``methodology_profile`` snapshot to a known profile.

    - missing / empty (legacy plan) → DEFAULT_PROFILE with status ``legacy_default``;
    - explicit id+version that is known → that profile, ``resolved``;
    - explicit but unknown → ``None`` with ``unavailable`` (draft opens, publish blocks).
    Never silently substitutes an unknown profile with the default.
    """
    if not snapshot or not str(snapshot.get("profile_id") or "").strip():
        return ProfileResolution(DEFAULT_PROFILE, "legacy_default")
    key = (str(snapshot.get("profile_id")), str(snapshot.get("version") or ""))
    profile = _PROFILES.get(key)
    if profile is None:
        return ProfileResolution(None, "unavailable")
    return ProfileResolution(profile, "resolved")
