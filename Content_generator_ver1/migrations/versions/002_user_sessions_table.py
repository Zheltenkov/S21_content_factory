"""User sessions table

Revision ID: 002
Revises: 001
Create Date: 2024-01-02 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'user_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.String(length=100), nullable=False),
        sa.Column('username', sa.String(length=100), nullable=False),
        sa.Column('session_token', sa.String(length=255), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('last_activity', sa.DateTime(), nullable=False),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('is_active', sa.String(length=10), nullable=False, server_default='true'),
        sa.Column('ended_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_sessions_user_id', 'user_sessions', ['user_id'])
    op.create_index('idx_sessions_token', 'user_sessions', ['session_token'], unique=True)
    op.create_index('idx_sessions_started_at', 'user_sessions', ['started_at'])
    op.create_index('idx_sessions_active', 'user_sessions', ['is_active'])


def downgrade() -> None:
    op.drop_index('idx_sessions_active', table_name='user_sessions')
    op.drop_index('idx_sessions_started_at', table_name='user_sessions')
    op.drop_index('idx_sessions_token', table_name='user_sessions')
    op.drop_index('idx_sessions_user_id', table_name='user_sessions')
    op.drop_table('user_sessions')

