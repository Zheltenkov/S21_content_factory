"""Add durable workflow, dashboard runs and LLM usage ledger.

Revision ID: 010
Revises: 009
Create Date: 2026-05-14 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def _json_type() -> sa.types.TypeEngine:
    return postgresql.JSON(astext_type=sa.Text())


def _has_table(table_name: str) -> bool:
    return table_name in inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    json_type = _json_type()

    if not _has_table("generation_workflow_states"):
        op.create_table(
            "generation_workflow_states",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("request_id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=100), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="created"),
            sa.Column("current_node", sa.String(length=120), nullable=True),
            sa.Column("last_completed_node", sa.String(length=120), nullable=True),
            sa.Column("resume_from_node", sa.String(length=120), nullable=True),
            sa.Column("progress_current", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("progress_total", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("meta_data", json_type, nullable=True),
            sa.Column("commands", json_type, nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("request_id", name="uq_generation_workflow_states_request_id"),
        )
        op.create_index("idx_generation_workflow_user_status", "generation_workflow_states", ["user_id", "status"])
        op.create_index("idx_generation_workflow_updated", "generation_workflow_states", ["updated_at"])

    if not _has_table("generation_workflow_checkpoints"):
        op.create_table(
            "generation_workflow_checkpoints",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("request_id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=100), nullable=True),
            sa.Column("checkpoint_index", sa.Integer(), nullable=False),
            sa.Column("node_id", sa.String(length=120), nullable=False),
            sa.Column("node_name", sa.String(length=300), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("input_hash", sa.String(length=64), nullable=False),
            sa.Column("output_artifact", json_type, nullable=True),
            sa.Column("validation_result", json_type, nullable=True),
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("duration_ms", sa.Numeric(18, 3), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("request_id", "checkpoint_index", name="uq_workflow_checkpoint_request_index"),
        )
        op.create_index(
            "idx_workflow_checkpoint_request_node",
            "generation_workflow_checkpoints",
            ["request_id", "node_id"],
        )
        op.create_index("idx_workflow_checkpoint_status", "generation_workflow_checkpoints", ["status"])

    if not _has_table("user_runs"):
        op.create_table(
            "user_runs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("request_id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=100), nullable=False),
            sa.Column("kind", sa.String(length=40), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("title", sa.String(length=500), nullable=True),
            sa.Column("score", json_type, nullable=True),
            sa.Column("result_url", sa.String(length=500), nullable=True),
            sa.Column("meta_data", json_type, nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("request_id", name="uq_user_runs_request_id"),
        )
        op.create_index("idx_user_runs_user_updated", "user_runs", ["user_id", "updated_at"])
        op.create_index("idx_user_runs_user_status", "user_runs", ["user_id", "status"])
        op.create_index("idx_user_runs_kind_status", "user_runs", ["kind", "status"])

    if not _has_table("llm_usage_ledger"):
        op.create_table(
            "llm_usage_ledger",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.String(length=100), nullable=False),
            sa.Column("run_id", sa.String(length=100), nullable=False),
            sa.Column("node", sa.String(length=100), nullable=False),
            sa.Column("role", sa.String(length=60), nullable=True),
            sa.Column("provider", sa.String(length=40), nullable=True),
            sa.Column("model", sa.String(length=120), nullable=True),
            sa.Column("calls_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cost_usd", sa.Numeric(18, 8), nullable=False, server_default="0"),
            sa.Column("route_data", json_type, nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "run_id", "node", name="uq_llm_usage_user_run_node"),
        )
        op.create_index("idx_llm_usage_role_provider", "llm_usage_ledger", ["role", "provider"])


def downgrade() -> None:
    for table_name in (
        "llm_usage_ledger",
        "user_runs",
        "generation_workflow_checkpoints",
        "generation_workflow_states",
    ):
        if _has_table(table_name):
            op.drop_table(table_name)
