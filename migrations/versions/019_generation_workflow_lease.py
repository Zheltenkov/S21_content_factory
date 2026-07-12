"""generation_workflow_states lease/heartbeat/retry columns for a durable generation worker

Adds a DB-lease model to ``generation_workflow_states`` so a background generation
worker can atomically claim a recoverable workflow (status ``created`` or
``interrupted``) by taking a time-boxed lease, heartbeat to hold it, and have an
expired *worker-owned* lease reclaimed (requeued for retry, or failed once attempts
are exhausted). Legacy in-process runs keep ``lease_owner`` NULL and are never touched
by the worker's claim/reclaim scans, so this ships safely alongside the current
asyncio dispatch.

Idempotent (guarded ``add_column``) so it is a no-op on a DB that already has the
columns. Mirrors the ORM model in ``api/db/models.py`` (tests build the schema from
ORM metadata; production applies this migration).

Revision ID: 019_generation_lease
Revises: 018_intake_lease
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "019_generation_lease"
down_revision = "018_intake_lease"
branch_labels = None
depends_on = None

_TABLE = "generation_workflow_states"


def _has_table(table_name: str) -> bool:
    return table_name in inspect(op.get_bind()).get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(column["name"] == column_name for column in inspect(op.get_bind()).get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(index["name"] == index_name for index in inspect(op.get_bind()).get_indexes(table_name))


def upgrade() -> None:
    if not _has_table(_TABLE):
        return
    if not _has_column(_TABLE, "lease_owner"):
        op.add_column(_TABLE, sa.Column("lease_owner", sa.String(length=200), nullable=True))
    if not _has_column(_TABLE, "lease_expires_at"):
        op.add_column(_TABLE, sa.Column("lease_expires_at", sa.DateTime(), nullable=True))
    if not _has_column(_TABLE, "heartbeat_at"):
        op.add_column(_TABLE, sa.Column("heartbeat_at", sa.DateTime(), nullable=True))
    if not _has_column(_TABLE, "attempt_count"):
        op.add_column(
            _TABLE,
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        )
    if not _has_index(_TABLE, "idx_generation_workflow_lease"):
        op.create_index("idx_generation_workflow_lease", _TABLE, ["status", "lease_expires_at"])


def downgrade() -> None:
    if not _has_table(_TABLE):
        return
    if _has_index(_TABLE, "idx_generation_workflow_lease"):
        op.drop_index("idx_generation_workflow_lease", table_name=_TABLE)
    for column_name in ("attempt_count", "heartbeat_at", "lease_expires_at", "lease_owner"):
        if _has_column(_TABLE, column_name):
            op.drop_column(_TABLE, column_name)
