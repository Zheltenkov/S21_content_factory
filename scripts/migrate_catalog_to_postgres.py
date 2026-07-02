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

import psycopg2
from psycopg2.extras import execute_values

# FK-safe order (parents first).
TABLES = [
    "ingest_run", "source_workbook", "source_sheet", "source_block", "profile",
    "profile_source", "proficiency_scale", "proficiency_level", "dimension",
    "competency", "typed_competency", "profile_competency", "skill", "skill_alias",
    "competency_skill", "typed_competency_skill", "indicator_row", "indicator_row_meta",
    "indicator_level_cell", "taxonomy_node", "taxonomy_edge", "skill_taxonomy",
    "course", "project", "project_indicator", "ai_analysis_run",
    "ai_analysis_suggestion", "review_queue",
]


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

    pg = psycopg2.connect(args.pg_url, connect_timeout=30)
    pg.autocommit = False
    src_counts: dict[str, int] = {}
    dst_counts: dict[str, int] = {}
    try:
        with pg.cursor() as cur:
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

            # verify counts inside the same tx
            for t in TABLES:
                cur.execute(f"SELECT count(*) FROM {args.schema}.{t}")
                dst_counts[t] = cur.fetchone()[0]
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
