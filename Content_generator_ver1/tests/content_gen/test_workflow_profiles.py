from content_gen.workflow_profiles import resolve_workflow_profile, workflow_profile_payload


def test_standard_profile_keeps_project_regeneration_enabled() -> None:
    profile = resolve_workflow_profile({"methodology_human_review": False})

    assert profile.id == "standard"
    assert profile.capabilities.project_regeneration is True
    assert profile.capabilities.methodology_assistant is False
    assert profile.gates == []


def test_methodology_profile_keeps_regeneration_and_enables_stage_review() -> None:
    profile = resolve_workflow_profile({"methodology_human_review": "true"})

    assert profile.id == "methodology"
    assert profile.capabilities.project_regeneration is True
    assert profile.capabilities.section_regeneration is True
    assert profile.capabilities.methodology_assistant is True
    assert profile.capabilities.stage_review is True
    assert {gate.after_stage for gate in profile.gates}


def test_profile_payload_is_api_serializable() -> None:
    payload = workflow_profile_payload(resolve_workflow_profile({"workflow_profile_id": "methodology"}))

    assert payload["id"] == "methodology"
    assert payload["capabilities"]["project_regeneration"] is True
    assert payload["capabilities"]["section_regeneration"] is True
    assert payload["gates"][0]["action"] == "approve_or_revise"


def test_unknown_profile_id_does_not_override_review_flag() -> None:
    profile = resolve_workflow_profile(
        {"workflow_profile_id": "unknown", "methodology_human_review": True}
    )

    assert profile.id == "methodology"
