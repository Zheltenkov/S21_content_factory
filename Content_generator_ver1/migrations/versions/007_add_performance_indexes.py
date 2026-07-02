"""Add performance indexes

Revision ID: 007
Revises: 006
Create Date: 2024-01-16 00:00:00.000000

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = '007'
down_revision = '006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Составной индекс для частых запросов логов по request_id и timestamp
    op.create_index(
        'idx_logs_request_timestamp',
        'logs',
        ['request_id', 'timestamp'],
        unique=False
    )

    # Составной индекс для сессий по user_id, is_active и last_activity
    op.create_index(
        'idx_sessions_user_active',
        'user_sessions',
        ['user_id_fk', 'is_active', 'last_activity'],
        unique=False
    )

    # Составной индекс для результатов генерации по user_id и created_at
    op.create_index(
        'idx_gen_results_user_created',
        'generation_results',
        ['user_id', 'created_at'],
        unique=False
    )

    # Индекс для быстрого поиска активных пользователей
    op.create_index(
        'idx_users_active_email',
        'users',
        ['is_active', 'email'],
        unique=False
    )


def downgrade() -> None:
    op.drop_index('idx_users_active_email', table_name='users')
    op.drop_index('idx_gen_results_user_created', table_name='generation_results')
    op.drop_index('idx_sessions_user_active', table_name='user_sessions')
    op.drop_index('idx_logs_request_timestamp', table_name='logs')

