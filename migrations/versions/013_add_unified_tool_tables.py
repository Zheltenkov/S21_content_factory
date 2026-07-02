"""Add unified tool run and Spravochnik catalog mirror tables.

Revision ID: 013
Revises: 012
Create Date: 2026-07-02 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def _json_type() -> sa.types.TypeEngine:
    return postgresql.JSON(astext_type=sa.Text())


def _has_table(table_name: str) -> bool:
    return table_name in inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("tool_runs"):
        op.create_table(
            "tool_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("run_id", sa.String(length=36), nullable=False),
            sa.Column("tool_name", sa.String(length=40), nullable=False),
            sa.Column("user_id", sa.String(length=100), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
            sa.Column("input_ref", sa.Text(), nullable=True),
            sa.Column("output_ref", sa.Text(), nullable=True),
            sa.Column("summary", _json_type(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("run_id", name="uq_tool_runs_run_id"),
        )
        op.create_index("idx_tool_runs_tool_status", "tool_runs", ["tool_name", "status"])
        op.create_index("idx_tool_runs_user_created", "tool_runs", ["user_id", "created_at"])

    if not _has_table("spravochnik_catalog_entities"):
        op.create_table(
            "spravochnik_catalog_entities",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("entity_type", sa.String(length=80), nullable=False),
            sa.Column("source_id", sa.String(length=120), nullable=False),
            sa.Column("title", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="active"),
            sa.Column("payload", _json_type(), nullable=False),
            sa.Column("source_updated_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("entity_type", "source_id", name="uq_spravochnik_entity_source"),
        )
        op.create_index(
            "idx_spravochnik_entities_type_status",
            "spravochnik_catalog_entities",
            ["entity_type", "status"],
        )


def downgrade() -> None:
    if _has_table("spravochnik_catalog_entities"):
        op.drop_index("idx_spravochnik_entities_type_status", table_name="spravochnik_catalog_entities")
        op.drop_table("spravochnik_catalog_entities")
    if _has_table("tool_runs"):
        op.drop_index("idx_tool_runs_user_created", table_name="tool_runs")
        op.drop_index("idx_tool_runs_tool_status", table_name="tool_runs")
        op.drop_table("tool_runs")

