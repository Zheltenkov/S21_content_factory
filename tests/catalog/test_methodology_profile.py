"""MethodologyProfile seam + resolver (redirect step 2)."""

from __future__ import annotations

from content_factory.catalog.pipeline import stage_dag_to_up
from content_factory.catalog.pipeline.curriculum.methodology_profile import (
    DEFAULT_PROFILE,
    DIGITAL_PRODUCT_PROJECT_BASED_V1,
    DIGITAL_PRODUCT_PROJECT_BASED_V2,
    LEGACY_DEFAULT_PROFILE,
    resolve_profile,
)


def test_v1_is_frozen_and_v2_is_default() -> None:
    assert LEGACY_DEFAULT_PROFILE is DIGITAL_PRODUCT_PROJECT_BASED_V1
    assert DEFAULT_PROFILE is DIGITAL_PRODUCT_PROJECT_BASED_V2
    v1_thresholds = DIGITAL_PRODUCT_PROJECT_BASED_V1.publication_thresholds
    assert v1_thresholds.required_policy_coverage_pct == 100.0
    assert v1_thresholds.single_skill_max_pct == 25.0
    assert v1_thresholds.required_activity_archetype_coverage_pct == 0.0
    assert v1_thresholds.required_artifact_contract_coverage_pct == 0.0
    assert v1_thresholds.max_artifact_merge_error_count is None
    assert DIGITAL_PRODUCT_PROJECT_BASED_V1.single_skill_exempt_kinds == ("lab",)
    assert DIGITAL_PRODUCT_PROJECT_BASED_V1.capstone_policy == "follow_design"
    assert DIGITAL_PRODUCT_PROJECT_BASED_V1.artifact_policy_set == "digital-product-project/v1"

    v2_thresholds = DIGITAL_PRODUCT_PROJECT_BASED_V2.publication_thresholds
    assert v2_thresholds.required_policy_coverage_pct == 0.0
    assert v2_thresholds.required_activity_archetype_coverage_pct == 100.0
    assert v2_thresholds.required_artifact_contract_coverage_pct == 100.0
    assert v2_thresholds.max_artifact_merge_error_count == 0
    assert set(DIGITAL_PRODUCT_PROJECT_BASED_V2.allowed_activity_archetypes) == {
        "investigate",
        "design",
        "construct",
        "operate",
        "decide",
        "perform",
    }


def test_snapshot_serializes_only_identity_not_executables() -> None:
    snapshot = DIGITAL_PRODUCT_PROJECT_BASED_V1.snapshot()
    assert snapshot == {"profile_id": "digital_product_project_based", "version": "1"}
    # only strings — nothing executable / no registry / no thresholds leaked
    assert all(isinstance(value, str) for value in snapshot.values())


def test_new_build_snapshots_v2_without_upgrading_legacy_payloads() -> None:
    built = stage_dag_to_up.run({}, [], {})

    assert built["status"] == "deferred"
    assert built["methodology_profile"] == DIGITAL_PRODUCT_PROJECT_BASED_V2.snapshot()


def test_resolve_legacy_missing_field_uses_v1() -> None:
    resolution = resolve_profile(None)
    assert resolution.profile is LEGACY_DEFAULT_PROFILE
    assert resolution.status == "legacy_default"
    assert resolve_profile({}).status == "legacy_default"


def test_resolve_known_profile() -> None:
    resolution = resolve_profile({"profile_id": "digital_product_project_based", "version": "1"})
    assert resolution.profile is DIGITAL_PRODUCT_PROJECT_BASED_V1
    assert resolution.status == "resolved"


def test_resolve_current_profile() -> None:
    resolution = resolve_profile({"profile_id": "digital_product_project_based", "version": "2"})
    assert resolution.profile is DEFAULT_PROFILE
    assert resolution.status == "resolved"


def test_resolve_unknown_version_is_unavailable_not_default() -> None:
    resolution = resolve_profile({"profile_id": "digital_product_project_based", "version": "999"})
    assert resolution.profile is None  # never silently substituted with the default
    assert resolution.status == "unavailable"
