"""MethodologyProfile seam + resolver (redirect step 2)."""

from __future__ import annotations

from content_factory.catalog.pipeline.curriculum.methodology_profile import (
    DEFAULT_PROFILE,
    DIGITAL_PRODUCT_PROJECT_BASED_V1,
    resolve_profile,
)


def test_v1_is_the_default_and_reproduces_current_thresholds() -> None:
    assert DEFAULT_PROFILE is DIGITAL_PRODUCT_PROJECT_BASED_V1
    thresholds = DIGITAL_PRODUCT_PROJECT_BASED_V1.publication_thresholds
    assert thresholds.required_policy_coverage_pct == 100.0
    assert thresholds.single_skill_max_pct == 25.0
    assert DIGITAL_PRODUCT_PROJECT_BASED_V1.single_skill_exempt_kinds == ("lab",)
    assert DIGITAL_PRODUCT_PROJECT_BASED_V1.capstone_policy == "follow_design"


def test_snapshot_serializes_only_identity_not_executables() -> None:
    snapshot = DIGITAL_PRODUCT_PROJECT_BASED_V1.snapshot()
    assert snapshot == {"profile_id": "digital_product_project_based", "version": "1"}
    # only strings — nothing executable / no registry / no thresholds leaked
    assert all(isinstance(value, str) for value in snapshot.values())


def test_resolve_legacy_missing_field_uses_v1() -> None:
    resolution = resolve_profile(None)
    assert resolution.profile is DEFAULT_PROFILE
    assert resolution.status == "legacy_default"
    assert resolve_profile({}).status == "legacy_default"


def test_resolve_known_profile() -> None:
    resolution = resolve_profile({"profile_id": "digital_product_project_based", "version": "1"})
    assert resolution.profile is DIGITAL_PRODUCT_PROJECT_BASED_V1
    assert resolution.status == "resolved"


def test_resolve_unknown_version_is_unavailable_not_default() -> None:
    resolution = resolve_profile({"profile_id": "digital_product_project_based", "version": "999"})
    assert resolution.profile is None  # never silently substituted with the default
    assert resolution.status == "unavailable"
