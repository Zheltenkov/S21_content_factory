from content_gen.context_phase_executor import _build_narrative_anchor


def test_narrative_anchor_does_not_leak_raw_unrelated_skill_names() -> None:
    anchor = _build_narrative_anchor(
        seed=None,
        prev_projects=[{"title": "Как эффективно планировать"}],
        skills_intersection=[],
        skills_new=["DevOps", "Types and data structures"],
    )

    assert "Как эффективно планировать" in anchor
    assert "DevOps" not in anchor
    assert "Types and data structures" not in anchor
    assert "текущий рабочий кейс" in anchor
