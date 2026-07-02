"""Drop legacy context profile version column

Revision ID: 008
Revises: 007
Create Date: 2026-04-26 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None

_LEGACY_COLUMN = "rag_profile_versions"


def _has_generation_column(column_name: str) -> bool:
    inspector = inspect(op.get_bind())
    if "generation_results" not in inspector.get_table_names():
        return False
    return any(column["name"] == column_name for column in inspector.get_columns("generation_results"))


def upgrade() -> None:
    if _has_generation_column(_LEGACY_COLUMN):
        with op.batch_alter_table("generation_results") as batch_op:
            batch_op.drop_column(_LEGACY_COLUMN)


def downgrade() -> None:
    if not _has_generation_column(_LEGACY_COLUMN):
        with op.batch_alter_table("generation_results") as batch_op:
            batch_op.add_column(sa.Column(_LEGACY_COLUMN, sa.JSON(), nullable=True))
