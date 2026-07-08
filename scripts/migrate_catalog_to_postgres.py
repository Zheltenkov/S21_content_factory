"""Migrate the canonical catalog from the Spravochnik SQLite store into a PostgreSQL
`catalog` schema (the Phase-4b hybrid: canonical catalog in Postgres, intake pipeline
still on SQLite).

The 28 canonical catalog tables are copied with explicit ids in FK-safe (parent-first)
order, inside a single transaction. Intake/DAG working tables (new_tables.sql) are left
in SQLite. Safe to re-run against an empty target; it refuses to run if the target
catalog tables already hold rows unless --truncate is passed.

Usage:
    python scripts/migrate_catalog_to_postgres.py \
        --sqlite src/content_factory/catalog/artifacts/skills_catalog.sqlite \
        --pg-url "postgresql://user:pass@host/db?sslmode=require"

The schema itself is created by the Alembic migration
(migrations/versions/*_catalog_schema.py); run `alembic upgrade head` first.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time

import psycopg2
from psycopg2.extras import execute_values


def connect_with_retry(url: str, attempts: int = 8, delay: float = 4.0):
    """Connect to Postgres, tolerating cold-start EOF drops (e.g. Neon serverless).

    A freshly-woken compute often drops the first query on a new connection; we
    verify the connection with a throwaway SELECT and retry the whole connect.
    """
    last: Exception | None = None
    for i in range(attempts):
        try:
            conn = psycopg2.connect(url, connect_timeout=30)
            with conn.cursor() as c:
                c.execute("SELECT 1")
                c.fetchone()
            conn.rollback()
            return conn
        except psycopg2.Error as exc:
            last = exc
            try:
                conn.close()  # type: ignore[possibly-undefined]
            except Exception:
                pass
            print(f"connect attempt {i + 1}/{attempts} failed: {str(exc).splitlines()[0][:60]}", file=sys.stderr)
            time.sleep(delay)
    raise SystemExit(f"could not establish a stable connection after {attempts} attempts: {last}")

# FK-safe order (parents first).
CANONICAL_TABLES = [
    "ingest_run", "source_workbook", "source_sheet", "source_block", "profile",
    "profile_source", "proficiency_scale", "proficiency_level", "dimension",
    "competency", "typed_competency", "profile_competency", "skill", "skill_alias",
    "competency_skill", "typed_competency_skill", "indicator_row", "indicator_row_meta",
    "indicator_level_cell", "taxonomy_node", "taxonomy_edge", "skill_taxonomy",
    "course", "project", "project_indicator", "ai_analysis_run",
    "ai_analysis_suggestion", "review_queue",
]

# Working/intake tables (FK-safe, parents first; reference canonical, so loaded after it).
# skill_group/indicator (migration 017): admin-runtime tables; indicator FKs skill (canonical,
# loaded earlier), skill_group is standalone. skill.group_id is a soft integer (no FK).
WORKING_TABLES = [
    "profile_brief", "evidence_source", "evidence_query_cache", "skill_suggestion",
    "skill_prerequisite", "prerequisite_edge_decision", "intake_job",
    "curriculum_plan", "curriculum_plan_row", "curriculum_artifact_template",
    "curriculum_artifact_template_scope", "skill_set", "skill_set_item",
    "curriculum_artifact_template_proposal", "skill_promotion_log",
    "skill_group", "indicator",
]

# Full-PG cutover: migrate canonical first, then working tables (FK-safe overall).
TABLES = CANONICAL_TABLES + WORKING_TABLES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", required=True, help="Path to skills_catalog.sqlite")
    parser.add_argument("--pg-url", default=os.getenv("CATALOG_TARGET_URL") or os.getenv("DATABASE_URL"))
    parser.add_argument("--schema", default="catalog")
    parser.add_argument("--truncate", action="store_true", help="Truncate target catalog tables first")
    parser.add_argument("--page-size", type=int, default=500)
    args = parser.parse_args(argv)
    if not args.pg_url:
        parser.error("provide --pg-url or set CATALOG_TARGET_URL / DATABASE_URL")

    sq = sqlite3.connect(args.sqlite)
    sq.row_factory = sqlite3.Row
    present = {t for (t,) in sq.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    pg = connect_with_retry(args.pg_url)
    pg.autocommit = False
    src_counts: dict[str, int] = {}
    dst_counts: dict[str, int] = {}
    try:
        with pg.cursor() as cur:
            # Bulk-load mode: defer FK/trigger enforcement so the copy faithfully mirrors
            # SQLite (which does not enforce FKs — the source may hold dangling references).
            # Requires a superuser/replication role; the local dev PG user has it.
            cur.execute("SET session_replication_role = replica")
            # guard: non-empty target
            if not args.truncate:
                for t in TABLES:
                    cur.execute(f"SELECT count(*) FROM {args.schema}.{t}")
                    if cur.fetchone()[0]:
                        print(f"ERROR: {args.schema}.{t} already has rows; pass --truncate to overwrite", file=sys.stderr)
                        return 2
            else:
                for t in reversed(TABLES):
                    cur.execute(f"TRUNCATE {args.schema}.{t} CASCADE")

            for t in TABLES:
                if t not in present:
                    print(f"skip {t}: not in sqlite")
                    continue
                rows = sq.execute(f"SELECT * FROM {t}").fetchall()
                src_counts[t] = len(rows)
                if not rows:
                    continue
                cols = rows[0].keys()
                collist = ", ".join(cols)
                template = "(" + ", ".join(["%s"] * len(cols)) + ")"
                data = [tuple(r[c] for c in cols) for r in rows]
                execute_values(
                    cur,
                    f"INSERT INTO {args.schema}.{t} ({collist}) VALUES %s",
                    data,
                    template=template,
                    page_size=args.page_size,
                )
                print(f"loaded {t}: {len(rows)}")

            # Reset IDENTITY sequences to max(id)+1 so runtime INSERTs don't collide
            # with the explicit ids we just copied. Only tables that actually have an `id`
            # column (some canonical join tables have composite PKs and no `id`); of those,
            # non-identity tables return NULL from pg_get_serial_sequence and are skipped.
            cur.execute(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = %s AND column_name = 'id'",
                (args.schema,),
            )
            id_tables = {r[0] for r in cur.fetchall()}
            for t in TABLES:
                if t not in id_tables:
                    continue
                cur.execute("SELECT pg_get_serial_sequence(%s, 'id')", (f"{args.schema}.{t}",))
                seq_row = cur.fetchone()
                seq = seq_row[0] if seq_row else None
                if not seq:
                    continue
                cur.execute(
                    f"SELECT setval(%s, COALESCE((SELECT MAX(id) FROM {args.schema}.{t}), 0) + 1, false)",
                    (seq,),
                )

            # verify counts inside the same tx
            for t in TABLES:
                cur.execute(f"SELECT count(*) FROM {args.schema}.{t}")
                dst_counts[t] = cur.fetchone()[0]
            cur.execute("SET session_replication_role = origin")
        pg.commit()
    except Exception:
        pg.rollback()
        raise
    finally:
        pg.close()
        sq.close()

    mismatches = {t: (src_counts.get(t, 0), dst_counts.get(t, 0)) for t in TABLES if src_counts.get(t, 0) != dst_counts.get(t, 0)}
    total_src = sum(src_counts.values())
    total_dst = sum(dst_counts.values())
    print(f"\nrow totals: sqlite={total_src} postgres={total_dst}")
    if mismatches:
        print("COUNT MISMATCHES:", mismatches, file=sys.stderr)
        return 1
    print("OK: all catalog table row counts match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
