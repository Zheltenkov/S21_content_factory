"""Add paused generation sessions

Revision ID: 009
Revises: 008
Create Date: 2026-04-27 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None

_TABLE = "paused_generation_sessions"


def _has_table(table_name: str) -> bool:
    return table_name in inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table(_TABLE):
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="needs_review"),
        sa.Column("project_seed", sa.JSON(), nullable=True),
        sa.Column("track_paths", sa.JSON(), nullable=True),
        sa.Column("context_payload", sa.JSON(), nullable=True),
        sa.Column("steps_payload", sa.JSON(), nullable=True),
        sa.Column("resume_from_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("methodology", sa.JSON(), nullable=True),
        sa.Column("review_actions", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index("ix_paused_generation_sessions_id", _TABLE, ["id"])
    op.create_index("ix_paused_generation_sessions_request_id", _TABLE, ["request_id"])
    op.create_index("idx_paused_gen_request_id", _TABLE, ["request_id"])
    op.create_index("idx_paused_gen_user_id", _TABLE, ["user_id"])
    op.create_index("idx_paused_gen_status", _TABLE, ["status"])
    op.create_index("idx_paused_gen_created_at", _TABLE, ["created_at"])


def downgrade() -> None:
    if _has_table(_TABLE):
        op.drop_table(_TABLE)
