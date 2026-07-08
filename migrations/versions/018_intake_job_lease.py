"""intake_job lease/heartbeat/retry columns for durable multi-worker intake

Adds a DB-lease model to ``catalog.intake_job`` so background intake jobs survive
process restart and are safe across multiple workers: a worker atomically claims a
job (taking a time-boxed lease), heartbeats to extend it, and an expired lease is
reclaimed (requeued for retry, or failed once attempts are exhausted) instead of a
crashed job being silently lost.

Idempotent ``ADD COLUMN IF NOT EXISTS`` so it is a no-op on a DB that already has
the columns. The frozen ``working_tables_postgres.sql`` (migration 016) is left
untouched — new schema always ships as a new migration.

Revision ID: 018_intake_lease
Revises: 017
"""

from alembic import op

revision = "018_intake_lease"
down_revision = "017"
branch_labels = None
depends_on = None

_SCHEMA = "catalog"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {_SCHEMA}.intake_job
            ADD COLUMN IF NOT EXISTS lease_owner      text,
            ADD COLUMN IF NOT EXISTS lease_expires_at timestamptz,
            ADD COLUMN IF NOT EXISTS heartbeat_at     timestamptz,
            ADD COLUMN IF NOT EXISTS attempt_count    integer NOT NULL DEFAULT 0
        """
    )
    # Supports the claim/reclaim scan: pending jobs and running jobs with a dead lease.
    op.execute(
        f"CREATE INDEX IF NOT EXISTS idx_intake_job_lease "
        f"ON {_SCHEMA}.intake_job(status, lease_expires_at)"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_SCHEMA}.idx_intake_job_lease")
    op.execute(
        f"""
        ALTER TABLE {_SCHEMA}.intake_job
            DROP COLUMN IF EXISTS lease_owner,
            DROP COLUMN IF EXISTS lease_expires_at,
            DROP COLUMN IF EXISTS heartbeat_at,
            DROP COLUMN IF EXISTS attempt_count
        """
    )
