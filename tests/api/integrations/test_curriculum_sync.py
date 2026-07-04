"""Phase 4c.2 — structured curriculum-plan sync source reader.

Covers the SQLite-side reader of the relational mirror sync (no Postgres needed).
The Postgres relational upsert is verified live against the dev database during the
migration; the generator-facing read path is covered by the curriculum router tests.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_SOURCE_SQLITE = _REPO / "src" / "content_factory" / "catalog" / "artifacts" / "skills_catalog.sqlite"


@pytest.fixture()
def seeded_sqlite(tmp_path) -> Path:
    """A copy of the real catalog SQLite (full runtime schema) with one UP plan."""

    db = tmp_path / "src.sqlite"
    shutil.copy(_SOURCE_SQLITE, db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO curriculum_plan"
        "(id,status,title,total_blocks,total_projects,created_at,updated_at,direction,version,metadata_json) "
        "VALUES(101,'built','UP Test',1,2,'2026-07-03','2026-07-03','PjM','v1','{}')"
    )
    conn.execute(
        "INSERT INTO curriculum_plan_row"
        "(id,plan_id,block_index,row_number,project_index_in_block,block_title,project_name,skills_list) "
        "VALUES(201,101,1,1,1,'Block A','Proj 1','SQL, Python')"
    )
    conn.execute(
        "INSERT INTO curriculum_plan_row"
        "(id,plan_id,block_index,row_number,project_index_in_block,project_name) "
        "VALUES(202,101,1,2,2,'Proj 2')"
    )
    conn.commit()
    conn.close()
    return db


def test_read_sqlite_up_returns_plan_and_rows(seeded_sqlite: Path) -> None:
    from content_factory.api.integrations.spravochnik_curriculum_sync import _read_sqlite_up

    plans, rows_by_plan = _read_sqlite_up(seeded_sqlite, limit=500)

    assert [p["id"] for p in plans] == [101]
    plan = plans[0]
    assert plan["title"] == "UP Test"
    assert plan["status"] == "built"

    rows = rows_by_plan[101]
    assert [r["id"] for r in rows] == [201, 202]
    assert rows[0]["project_name"] == "Proj 1"
    assert rows[0]["skills_list"] == "SQL, Python"
    assert rows[1]["skills_list"] is None  # nullable columns preserved


def test_read_sqlite_up_empty_when_no_plans(tmp_path) -> None:
    from content_factory.api.integrations.spravochnik_curriculum_sync import _read_sqlite_up

    db = tmp_path / "empty.sqlite"
    shutil.copy(_SOURCE_SQLITE, db)  # real DB has 0 curriculum_plan rows
    plans, rows_by_plan = _read_sqlite_up(db, limit=500)
    assert plans == []
    assert rows_by_plan == {}


def test_convert_assigns_distinct_project_order_within_block() -> None:
    """Two projects in one block must get distinct ``order`` values.

    Real mirror rows carry a 0-based ``project_index_in_block``. A prior
    ``_parse_int(...) or ...`` chain treated index 0 as missing and fell back to
    ``row_number``, so the first project (index 0 → row_number 1) and the second
    (index 1) both resolved to order 1 — colliding, which made the generator's
    block/project dropdown load the wrong project.
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


def test_plan_and_row_column_lists_match_the_mirror_schema() -> None:
    """The sync's column tuples must stay in lockstep with the Postgres DDL."""

    from content_factory.api.integrations import spravochnik_curriculum_sync as sync

    ddl = (
        _REPO / "src" / "content_factory" / "catalog" / "sql" / "catalog_curriculum_plan_postgres.sql"
    ).read_text(encoding="utf-8")
    for col in sync._PLAN_COLUMNS:
        assert f" {col} " in ddl or f"\n    {col} " in ddl, f"plan column {col} missing from DDL"
    for col in sync._ROW_COLUMNS:
        assert f" {col} " in ddl or f"\n    {col} " in ddl, f"row column {col} missing from DDL"
