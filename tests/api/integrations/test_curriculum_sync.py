"""Curriculum-plan conversion for the Postgres-native catalog.

The SQLite→Postgres mirror was removed in the full-PG cutover (``sync_spravochnik_curriculum_plans``
is now a no-op); only the generator-facing conversion contract remains under test here.
"""

from __future__ import annotations


def test_convert_assigns_distinct_project_order_within_block() -> None:
    """Two projects in one block must get distinct ``order`` values.

    Real rows carry a 0-based ``project_index_in_block``. A prior ``_parse_int(...) or ...``
    chain treated index 0 as missing and fell back to ``row_number``, so the first project
    (index 0 → row_number 1) and the second (index 1) both resolved to order 1 — colliding,
    which made the generator's block/project dropdown load the wrong project.
    """

    from content_factory.api.integrations.spravochnik_curriculum_sync import (
        convert_spravochnik_plan_to_generator_curriculum,
    )

    payload = {
        "id": 1,
        "title": "UP Test",
        "direction": "PjM",
        "rows": [
            {"block_index": 1, "row_number": 1, "project_index_in_block": 0, "block_title": "A", "project_name": "P1"},
            {"block_index": 1, "row_number": 2, "project_index_in_block": 1, "block_title": "A", "project_name": "P2"},
        ],
    }

    curriculum = convert_spravochnik_plan_to_generator_curriculum(payload)
    projects = curriculum["blocks"][0]["projects"]
    orders = [p["order"] for p in projects]

    assert len(projects) == 2
    assert len(set(orders)) == 2, f"project orders must be distinct within a block, got {orders}"


def test_sync_is_a_native_noop() -> None:
    """The mirror is obsolete: sync reports a native no-op without touching any store."""

    from content_factory.api.integrations.spravochnik_curriculum_sync import (
        sync_spravochnik_curriculum_plans,
    )

    result = sync_spravochnik_curriculum_plans()
    assert result["native"] is True
    assert result["synced"] == 0
