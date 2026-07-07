"""One-shot catalog Postgres deploy: schema first, then data, then verify.

Enforces the strict order the cutover requires — ``alembic upgrade head`` (creates
``skill_group``/``indicator`` and promotes IDENTITY on the runtime-written tables) BEFORE
``migrate_catalog_to_postgres.py`` (copies the catalog rows + resets the identity sequences).
Running data before schema, or skipping the migrate, leaves catalog-admin/ingest broken.

Usage (from repo root):
    python scripts/deploy_catalog_pg.py                # upgrade + migrate --truncate + verify
    python scripts/deploy_catalog_pg.py --no-truncate  # append instead of reload (rarely wanted)
    python scripts/deploy_catalog_pg.py --verify-only   # just re-run the post-deploy checks

Target DB comes from CATALOG_DATABASE_URL or DATABASE_URL (``localhost`` is rewritten to
``127.0.0.1`` — the container listens IPv4 only and Windows resolves localhost to ::1 first).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQLITE_SOURCE = ROOT / "src" / "content_factory" / "catalog" / "artifacts" / "skills_catalog.sqlite"


def _target_url() -> str:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    url = os.getenv("CATALOG_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        sys.exit("ERROR: set CATALOG_DATABASE_URL or DATABASE_URL before deploying.")
    return url.replace("localhost", "127.0.0.1")


def _run(cmd: list[str], env: dict[str, str]) -> None:
    print("+ " + " ".join(cmd), flush=True)
    if subprocess.run(cmd, cwd=str(ROOT), env=env).returncode != 0:
        sys.exit(f"ERROR: step failed: {' '.join(cmd)}")


def _verify(url: str) -> None:
    import psycopg2

    conn = psycopg2.connect(url)
    try:
        cur = conn.cursor()
        cur.execute("SELECT version_num FROM alembic_version")
        version = cur.fetchone()[0]
        if version < "017":
            sys.exit(f"ERROR: alembic at {version}, expected >= 017.")
        for table in ("skill_group", "indicator"):
            cur.execute("SELECT to_regclass(%s)", (f"catalog.{table}",))
            if cur.fetchone()[0] is None:
                sys.exit(f"ERROR: catalog.{table} missing — did the migrate step run?")
        counts = {}
        for table in ("skill", "skill_group", "indicator", "competency"):
            cur.execute(f"SELECT count(*) FROM catalog.{table}")
            counts[table] = cur.fetchone()[0]
        # Sanity: id sequence advanced past the copied rows (no runtime-insert collision).
        cur.execute("SELECT pg_get_serial_sequence('catalog.skill', 'id')")
        seq = cur.fetchone()[0]
        seq_note = ""
        if seq:
            cur.execute(f"SELECT last_value FROM {seq}")
            seq_note = f", skill.id seq at {cur.fetchone()[0]}"
        print(f"verify OK: alembic={version}, counts={counts}{seq_note}")
    finally:
        conn.close()


def main() -> None:
    args = set(sys.argv[1:])
    url = _target_url()
    env = dict(os.environ)
    env["DATABASE_URL"] = url

    if "--verify-only" not in args:
        if not SQLITE_SOURCE.exists():
            sys.exit(f"ERROR: catalog source not found: {SQLITE_SOURCE}")
        print(f"== deploy target: {url.rsplit('@', 1)[-1]} ==")
        _run([sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"], env)
        migrate = [
            sys.executable,
            "scripts/migrate_catalog_to_postgres.py",
            "--sqlite",
            str(SQLITE_SOURCE),
        ]
        if "--no-truncate" not in args:
            migrate.append("--truncate")
        migrate_env = dict(env)
        migrate_env["PYTHONPATH"] = "src"
        _run(migrate, migrate_env)

    _verify(url)
    print("DEPLOY OK — restart the app; catalog now serves from Postgres.")


if __name__ == "__main__":
    main()
