"""Add practice critic and config version columns

Revision ID: 004
Revises: 003
Create Date: 2025-01-05 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = inspector.get_table_names()

    json_type = postgresql.JSON(astext_type=sa.Text())

    # Создаем таблицу generation_results, если она еще не существует
    if "generation_results" not in tables:
        op.create_table(
            "generation_results",
            sa.Column("id", sa.Integer, primary_key=True, index=True),
            sa.Column("request_id", sa.String(36), nullable=False, unique=True, index=True),
            sa.Column("user_id", sa.String(100), index=True),
            sa.Column("seed_data", json_type, nullable=True),
            sa.Column("markdown", sa.Text, nullable=True),
            sa.Column("text_stats", json_type, nullable=True),
            sa.Column("task_plan", json_type, nullable=True),
            sa.Column("issues", json_type, nullable=True),
            sa.Column("regenerated_markdown", sa.Text, nullable=True),
            sa.Column("regeneration_comments", sa.Text, nullable=True),
            sa.Column("regeneration_changes", json_type, nullable=True),
            sa.Column("original_markdown", sa.Text, nullable=True),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.Column("updated_at", sa.DateTime, nullable=False),
        )
        op.create_index("idx_gen_results_request_id", "generation_results", ["request_id"])
        op.create_index("idx_gen_results_user_id", "generation_results", ["user_id"])
        op.create_index("idx_gen_results_created_at", "generation_results", ["created_at"])

    # Таблицы rubric_results и report_results также могли еще не существовать
    if "rubric_results" not in tables:
        op.create_table(
            "rubric_results",
            sa.Column("id", sa.Integer, primary_key=True, index=True),
            sa.Column("generation_result_id", sa.Integer, sa.ForeignKey("generation_results.id", ondelete="CASCADE"), nullable=False, unique=True, index=True),
            sa.Column("rubric_data", json_type, nullable=False),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.Column("updated_at", sa.DateTime, nullable=False),
        )
        op.create_index("idx_rubric_gen_id", "rubric_results", ["generation_result_id"])

    if "report_results" not in tables:
        op.create_table(
            "report_results",
            sa.Column("id", sa.Integer, primary_key=True, index=True),
            sa.Column("generation_result_id", sa.Integer, sa.ForeignKey("generation_results.id", ondelete="CASCADE"), nullable=False, unique=True, index=True),
            sa.Column("report_data", json_type, nullable=False),
            sa.Column("created_at", sa.DateTime, nullable=False),
            sa.Column("updated_at", sa.DateTime, nullable=False),
        )
        op.create_index("idx_report_gen_id", "report_results", ["generation_result_id"])

    # Добавляем новые JSON-поля к generation_results
    op.add_column("generation_results", sa.Column("practice_critic_issues", json_type, nullable=True))
    op.add_column("generation_results", sa.Column("agent_config_versions", json_type, nullable=True))


def downgrade() -> None:
    op.drop_column('generation_results', 'agent_config_versions')
    op.drop_column('generation_results', 'practice_critic_issues')
