"""separate brief template acceptance from global publication

Template proposals used to become active global templates as soon as a
methodologist accepted them for one brief.  That leaked brief-specific
methodology into unrelated curricula.  This migration records the two
decisions independently and adds the repeatability contract used by the
planner.

Revision ID: 020_template_scope
Revises: 019_generation_lease
"""

from alembic import op

revision = "020_template_scope"
down_revision = "019_generation_lease"
branch_labels = None
depends_on = None

_SCHEMA = "catalog"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {_SCHEMA}.curriculum_artifact_template
            ADD COLUMN IF NOT EXISTS repeatable boolean NOT NULL DEFAULT false
        """
    )
    op.execute(
        f"""
        ALTER TABLE {_SCHEMA}.curriculum_artifact_template_proposal
            ADD COLUMN IF NOT EXISTS repeatable boolean NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS accepted_at text,
            ADD COLUMN IF NOT EXISTS published_at text
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {_SCHEMA}.curriculum_artifact_template_proposal
            DROP COLUMN IF EXISTS published_at,
            DROP COLUMN IF EXISTS accepted_at,
            DROP COLUMN IF EXISTS repeatable
        """
    )
    op.execute(
        f"""
        ALTER TABLE {_SCHEMA}.curriculum_artifact_template
            DROP COLUMN IF EXISTS repeatable
        """
    )
