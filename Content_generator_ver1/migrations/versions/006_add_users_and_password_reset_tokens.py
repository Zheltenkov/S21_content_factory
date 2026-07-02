"""Add users and password_reset_tokens tables

Revision ID: 006
Revises: 005
Create Date: 2024-01-15 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '006'
down_revision = '005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Создаем таблицу users
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('username', sa.String(length=100), nullable=False),
        sa.Column('hashed_password', sa.String(length=255), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False, server_default='user'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('is_email_verified', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('last_login', sa.DateTime(), nullable=True),
        sa.Column('failed_login_attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('locked_until', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Создаем индексы для users
    op.create_index('ix_users_email', 'users', ['email'], unique=True)
    op.create_index('ix_users_username', 'users', ['username'], unique=True)
    op.create_index('ix_users_id', 'users', ['id'])

    # Создаем таблицу password_reset_tokens
    op.create_table(
        'password_reset_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('token', sa.String(length=255), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('used', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # Создаем индексы для password_reset_tokens
    op.create_index('ix_password_reset_tokens_token', 'password_reset_tokens', ['token'], unique=True)
    op.create_index('ix_password_reset_tokens_user_id', 'password_reset_tokens', ['user_id'])
    op.create_index('ix_password_reset_tokens_expires_at', 'password_reset_tokens', ['expires_at'])
    op.create_index('idx_reset_tokens_user_id', 'password_reset_tokens', ['user_id'])
    op.create_index('idx_reset_tokens_token', 'password_reset_tokens', ['token'])
    op.create_index('idx_reset_tokens_expires', 'password_reset_tokens', ['expires_at'])

    # Добавляем поле user_id_fk в user_sessions для связи с users
    op.add_column('user_sessions', sa.Column('user_id_fk', sa.Integer(), nullable=True))

    # Создаем внешний ключ для user_id_fk
    op.create_foreign_key(
        'fk_user_sessions_user_id_fk',
        'user_sessions',
        'users',
        ['user_id_fk'],
        ['id']
    )

    # Создаем индекс для user_id_fk
    op.create_index('ix_user_sessions_user_id_fk', 'user_sessions', ['user_id_fk'])


def downgrade() -> None:
    # Удаляем индекс и внешний ключ для user_id_fk
    op.drop_index('ix_user_sessions_user_id_fk', table_name='user_sessions')
    op.drop_constraint('fk_user_sessions_user_id_fk', 'user_sessions', type_='foreignkey')

    # Удаляем поле user_id_fk из user_sessions
    op.drop_column('user_sessions', 'user_id_fk')

    # Удаляем индексы для password_reset_tokens
    op.drop_index('idx_reset_tokens_expires', table_name='password_reset_tokens')
    op.drop_index('idx_reset_tokens_token', table_name='password_reset_tokens')
    op.drop_index('idx_reset_tokens_user_id', table_name='password_reset_tokens')
    op.drop_index('ix_password_reset_tokens_expires_at', table_name='password_reset_tokens')
    op.drop_index('ix_password_reset_tokens_user_id', table_name='password_reset_tokens')
    op.drop_index('ix_password_reset_tokens_token', table_name='password_reset_tokens')

    # Удаляем таблицу password_reset_tokens
    op.drop_table('password_reset_tokens')

    # Удаляем индексы для users
    op.drop_index('ix_users_id', table_name='users')
    op.drop_index('ix_users_username', table_name='users')
    op.drop_index('ix_users_email', table_name='users')

    # Удаляем таблицу users
    op.drop_table('users')

