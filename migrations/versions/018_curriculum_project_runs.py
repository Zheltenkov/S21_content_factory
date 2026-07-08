"""curriculum project snapshots and generation run statuses

Revision ID: 018
Revises: 017
"""

import sqlalchemy as sa
from alembic import op

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "curriculum_project_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.String(length=80), nullable=False),
        sa.Column("pipeline_run_id", sa.String(length=80), nullable=False),
        sa.Column("source_plan_id", sa.Integer(), nullable=False),
        sa.Column("plan_version", sa.String(length=160), nullable=False),
        sa.Column("plan_hash", sa.String(length=64), nullable=False),
        sa.Column("plan_row_id", sa.Integer(), nullable=True),
        sa.Column("row_hash", sa.String(length=64), nullable=True),
        sa.Column("block_index", sa.Integer(), nullable=True),
        sa.Column("row_number", sa.Integer(), nullable=True),
        sa.Column("project_index", sa.Integer(), nullable=True),
        sa.Column("project_order", sa.Integer(), nullable=True),
        sa.Column("project_title", sa.String(length=500), nullable=True),
        sa.Column("context_data", sa.JSON(), nullable=True),
        sa.Column("seed_data", sa.JSON(), nullable=True),
        sa.Column("readiness_data", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pipeline_run_id"),
        sa.UniqueConstraint("snapshot_id"),
    )
    op.create_index(
        "idx_curriculum_project_snapshots_created",
        "curriculum_project_snapshots",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "idx_curriculum_project_snapshots_plan_hash",
        "curriculum_project_snapshots",
        ["source_plan_id", "plan_hash"],
        unique=False,
    )
    op.create_index(
        "idx_curriculum_project_snapshots_plan_row",
        "curriculum_project_snapshots",
        ["source_plan_id", "plan_row_id"],
        unique=False,
    )

    op.create_table(
        "curriculum_project_generation_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pipeline_run_id", sa.String(length=80), nullable=False),
        sa.Column("snapshot_id", sa.String(length=80), nullable=True),
        sa.Column("request_id", sa.String(length=36), nullable=True),
        sa.Column("user_id", sa.String(length=100), nullable=True),
        sa.Column("source_plan_id", sa.Integer(), nullable=False),
        sa.Column("plan_version", sa.String(length=160), nullable=False),
        sa.Column("plan_hash", sa.String(length=64), nullable=False),
        sa.Column("plan_row_id", sa.Integer(), nullable=True),
        sa.Column("row_hash", sa.String(length=64), nullable=True),
        sa.Column("block_index", sa.Integer(), nullable=True),
        sa.Column("row_number", sa.Integer(), nullable=True),
        sa.Column("project_index", sa.Integer(), nullable=True),
        sa.Column("project_order", sa.Integer(), nullable=True),
        sa.Column("project_title", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("stage", sa.String(length=120), nullable=True),
        sa.Column("result_url", sa.String(length=500), nullable=True),
        sa.Column("score_data", sa.JSON(), nullable=True),
        sa.Column("review_data", sa.JSON(), nullable=True),
        sa.Column("meta_data", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["curriculum_project_snapshots.snapshot_id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pipeline_run_id"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index(
        "idx_curriculum_project_runs_plan_row",
        "curriculum_project_generation_runs",
        ["source_plan_id", "plan_row_id", "updated_at"],
        unique=False,
    )
    op.create_index(
        "idx_curriculum_project_runs_request",
        "curriculum_project_generation_runs",
        ["request_id"],
        unique=False,
    )
    op.create_index(
        "idx_curriculum_project_runs_updated",
        "curriculum_project_generation_runs",
        ["updated_at"],
        unique=False,
    )
    op.create_index(
        "idx_curriculum_project_runs_user_status",
        "curriculum_project_generation_runs",
        ["user_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_curriculum_project_runs_user_status", table_name="curriculum_project_generation_runs")
    op.drop_index("idx_curriculum_project_runs_updated", table_name="curriculum_project_generation_runs")
    op.drop_index("idx_curriculum_project_runs_request", table_name="curriculum_project_generation_runs")
    op.drop_index("idx_curriculum_project_runs_plan_row", table_name="curriculum_project_generation_runs")
    op.drop_table("curriculum_project_generation_runs")
    op.drop_index("idx_curriculum_project_snapshots_plan_row", table_name="curriculum_project_snapshots")
    op.drop_index("idx_curriculum_project_snapshots_plan_hash", table_name="curriculum_project_snapshots")
    op.drop_index("idx_curriculum_project_snapshots_created", table_name="curriculum_project_snapshots")
    op.drop_table("curriculum_project_snapshots")
