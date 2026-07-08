from __future__ import annotations

from typing import Any

from content_factory.catalog.viewer.catalog_admin_ops import create_catalog_group, create_catalog_skill
from content_factory.catalog.viewer.intake_cleanup import clear_intake_workspace, prune_empty_generated_catalog_nodes
from content_factory.catalog.viewer.intake_jobs import create_intake_job


def test_prune_empty_generated_catalog_nodes_archives_generated_group(catalog_conn: Any) -> None:
    group_id = create_catalog_group(catalog_conn, "Generated Empty Group", 1, "active")
    create_catalog_skill(
        catalog_conn,
        group_id,
        "Archived generated skill",
        1,
        "",
        "",
        "matched",
        "",
        0,
    )
    catalog_conn.execute("UPDATE skill_group SET source = 'derived' WHERE id = ?", (group_id,))
    catalog_conn.commit()

    stats = prune_empty_generated_catalog_nodes(catalog_conn)

    group = catalog_conn.execute("SELECT status FROM skill_group WHERE id = ?", (group_id,)).fetchone()
    assert stats["skill_group_empty_generated_archived"] == 1
    assert group["status"] == "deprecated"


def test_clear_intake_workspace_removes_runtime_jobs(catalog_conn: Any) -> None:
    create_intake_job(
        catalog_conn,
        source_kind="text",
        source_name=None,
        file_path=None,
        brief_text="brief",
        use_council=False,
    )

    stats = clear_intake_workspace(catalog_conn)

    remaining = catalog_conn.execute("SELECT COUNT(*) AS count FROM intake_job").fetchone()
    assert stats["intake_job"] == 1
    assert remaining["count"] == 0
