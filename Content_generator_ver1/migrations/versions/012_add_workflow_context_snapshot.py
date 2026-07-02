"""Add durable workflow context snapshots.

Revision ID: 012
Revises: 011
Create Date: 2026-05-14 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def _json_type() -> sa.types.TypeEngine:
    return postgresql.JSON(astext_type=sa.Text())


def _has_table(table_name: str) -> bool:
    return table_name in inspect(op.get_bind()).get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(column["name"] == column_name for column in inspect(op.get_bind()).get_columns(table_name))


def upgrade() -> None:
    if _has_table("generation_workflow_checkpoints") and not _has_column(
        "generation_workflow_checkpoints",
        "context_snapshot",
    ):
        op.add_column(
            "generation_workflow_checkpoints",
            sa.Column("context_snapshot", _json_type(), nullable=True),
        )


def downgrade() -> None:
    if _has_column("generation_workflow_checkpoints", "context_snapshot"):
        op.drop_column("generation_workflow_checkpoints", "context_snapshot")
