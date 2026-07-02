"""Add request_logs table and token_hash to user_sessions

Revision ID: 003
Revises: 002
Create Date: 2024-01-01 12:00:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Используем PostgreSQL JSON тип
    json_type = postgresql.JSON(astext_type=sa.Text())

    # Создаем таблицу request_logs
    op.create_table(
        'request_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('request_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=100), nullable=True),
        sa.Column('method', sa.String(length=10), nullable=False),
        sa.Column('path', sa.String(length=500), nullable=False),
        sa.Column('status_code', sa.Integer(), nullable=False),
        sa.Column('request_body', json_type, nullable=True),
        sa.Column('response_time_ms', sa.Integer(), nullable=True),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

    # Создаем индексы для request_logs
    op.create_index('idx_request_logs_request_id', 'request_logs', ['request_id'])
    op.create_index('idx_request_logs_user_id', 'request_logs', ['user_id'])
    op.create_index('idx_request_logs_timestamp', 'request_logs', ['timestamp'])
    op.create_index('idx_request_logs_status', 'request_logs', ['status_code'])
    op.create_index('idx_request_logs_user_timestamp', 'request_logs', ['user_id', 'timestamp'])
    op.create_index('idx_request_logs_path', 'request_logs', ['path'])

    # Добавляем поле token_hash в user_sessions
    op.add_column('user_sessions', sa.Column('token_hash', sa.String(length=255), nullable=True))

    # Создаем индексы для user_sessions
    op.create_index('idx_sessions_token_hash', 'user_sessions', ['token_hash'])
    op.create_index('idx_sessions_token_active', 'user_sessions', ['session_token', 'is_active'])
    op.create_index('idx_sessions_user_activity', 'user_sessions', ['user_id', 'last_activity'])


def downgrade() -> None:
    # Удаляем индексы для user_sessions
    op.drop_index('idx_sessions_user_activity', table_name='user_sessions')
    op.drop_index('idx_sessions_token_active', table_name='user_sessions')
    op.drop_index('idx_sessions_token_hash', table_name='user_sessions')

    # Удаляем поле token_hash из user_sessions
    op.drop_column('user_sessions', 'token_hash')

    # Удаляем индексы для request_logs
    op.drop_index('idx_request_logs_path', table_name='request_logs')
    op.drop_index('idx_request_logs_user_timestamp', table_name='request_logs')
    op.drop_index('idx_request_logs_status', table_name='request_logs')
    op.drop_index('idx_request_logs_timestamp', table_name='request_logs')
    op.drop_index('idx_request_logs_user_id', table_name='request_logs')
    op.drop_index('idx_request_logs_request_id', table_name='request_logs')

    # Удаляем таблицу request_logs
    op.drop_table('request_logs')

