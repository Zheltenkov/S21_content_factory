"""add flow_trace column to generation_results

Revision ID: 005
Revises: 004
Create Date: 2025-01-05
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generation_results",
        sa.Column("flow_trace", postgresql.JSON(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("generation_results", "flow_trace")


