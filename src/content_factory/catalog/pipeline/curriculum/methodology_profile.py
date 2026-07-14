"""Versioned methodology profiles and their publication policies.

``project_quality`` measures profile-independent facts; the publication gate interprets
them through the exact profile identity stored in the UP snapshot. New builds use the
current default profile, legacy payloads without an identity stay on frozen V1 semantics,
and unknown explicit versions keep the draft readable while blocking publication.

Only ``{profile_id, version}`` is serialized, never executable policy objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .. import config
from .domain import ActivityArchetype

CapstonePolicy = Literal["follow_design", "always", "never"]
ResolutionStatus = Literal["resolved", "legacy_default", "unavailable"]


@dataclass(frozen=True)
class PublicationThresholds:
    """Profile-varying publication thresholds (interpreted by the gate)."""

    required_policy_coverage_pct: float
    single_skill_max_pct: float
    required_activity_archetype_coverage_pct: float = 0.0
    required_artifact_contract_coverage_pct: float = 0.0
    max_artifact_merge_error_count: int | None = None


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
    artifact_policy_set: str = "digital-product-project/v1"
    # Empty means the profile does not restrict assigned archetypes.
    allowed_activity_archetypes: tuple[ActivityArchetype, ...] = ()

    def snapshot(self) -> dict[str, str]:
        """The identity written into the UP payload (never the full profile)."""
        return {"profile_id": self.profile_id, "version": self.version}


#: Frozen legacy profile — reproduces the pre-archetype-gate behavior exactly.
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
    artifact_policy_set="digital-product-project/v1",
)

#: New plans use V2: the profile owns archetype and artifact-contract readiness.
DIGITAL_PRODUCT_PROJECT_BASED_V2 = MethodologyProfile(
    profile_id="digital_product_project_based",
    version="2",
    program_family="digital_product_project_based",
    skill_density_range=(config.UP_TARGET_SKILLS_MIN, config.UP_TARGET_SKILLS_MAX),
    single_skill_exempt_kinds=("lab",),
    capstone_policy="follow_design",
    publication_thresholds=PublicationThresholds(
        required_policy_coverage_pct=100.0,
        single_skill_max_pct=25.0,
        required_activity_archetype_coverage_pct=100.0,
        required_artifact_contract_coverage_pct=100.0,
        max_artifact_merge_error_count=0,
    ),
    artifact_policy_set="digital-product-project/v1",
    allowed_activity_archetypes=(
        "investigate",
        "design",
        "construct",
        "operate",
        "decide",
        "perform",
    ),
)

LEGACY_DEFAULT_PROFILE = DIGITAL_PRODUCT_PROJECT_BASED_V1
DEFAULT_PROFILE = DIGITAL_PRODUCT_PROJECT_BASED_V2

_PROFILES: dict[tuple[str, str], MethodologyProfile] = {
    (
        DIGITAL_PRODUCT_PROJECT_BASED_V1.profile_id,
        DIGITAL_PRODUCT_PROJECT_BASED_V1.version,
    ): DIGITAL_PRODUCT_PROJECT_BASED_V1,
    (
        DIGITAL_PRODUCT_PROJECT_BASED_V2.profile_id,
        DIGITAL_PRODUCT_PROJECT_BASED_V2.version,
    ): DIGITAL_PRODUCT_PROJECT_BASED_V2,
}


@dataclass(frozen=True)
class ProfileResolution:
    """Result of resolving a payload profile snapshot to a known profile."""

    profile: MethodologyProfile | None
    status: ResolutionStatus


def resolve_profile(snapshot: dict[str, Any] | None) -> ProfileResolution:
    """Resolve a payload ``methodology_profile`` snapshot to a known profile.

    - missing / empty (legacy plan) → frozen V1 with status ``legacy_default``;
    - explicit id+version that is known → that profile, ``resolved``;
    - explicit but unknown → ``None`` with ``unavailable`` (draft opens, publish blocks).
    Never silently substitutes an unknown profile with the default.
    """
    if not snapshot or not str(snapshot.get("profile_id") or "").strip():
        return ProfileResolution(LEGACY_DEFAULT_PROFILE, "legacy_default")
    key = (str(snapshot.get("profile_id")), str(snapshot.get("version") or ""))
    profile = _PROFILES.get(key)
    if profile is None:
        return ProfileResolution(None, "unavailable")
    return ProfileResolution(profile, "resolved")
