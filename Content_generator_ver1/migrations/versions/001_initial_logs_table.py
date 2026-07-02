"""Initial logs table

Revision ID: 001
Revises: 
Create Date: 2024-01-01 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('request_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=100), nullable=True),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.Column('level', sa.String(length=10), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('agent_name', sa.String(length=100), nullable=True),
        sa.Column('phase', sa.String(length=100), nullable=True),
            sa.Column('meta_data', sa.JSON(), nullable=True),  # Переименовано из 'metadata' (зарезервированное имя в SQLAlchemy)
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_logs_request_id', 'logs', ['request_id'])
    op.create_index('idx_logs_user_id', 'logs', ['user_id'])
    op.create_index('idx_logs_timestamp', 'logs', ['timestamp'])
    op.create_index('idx_logs_level', 'logs', ['level'])


def downgrade() -> None:
    op.drop_index('idx_logs_level', table_name='logs')
    op.drop_index('idx_logs_timestamp', table_name='logs')
    op.drop_index('idx_logs_user_id', table_name='logs')
    op.drop_index('idx_logs_request_id', table_name='logs')
    op.drop_table('logs')

