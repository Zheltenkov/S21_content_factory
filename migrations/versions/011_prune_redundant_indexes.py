"""Prune redundant non-unique indexes.

Revision ID: 011
Revises: 010
Create Date: 2026-05-14 00:00:00.000000

"""

from alembic import op
from sqlalchemy import inspect

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


_NONUNIQUE_INDEXES = {
    "logs": [
        "ix_logs_id",
        "ix_logs_request_id",
        "ix_logs_user_id",
        "ix_logs_timestamp",
    ],
    "users": [
        "ix_users_id",
    ],
    "password_reset_tokens": [
        "ix_password_reset_tokens_id",
        "idx_reset_tokens_user_id",
        "idx_reset_tokens_token",
        "idx_reset_tokens_expires",
    ],
    "user_sessions": [
        "ix_user_sessions_id",
        "ix_user_sessions_user_id",
        "ix_user_sessions_token_hash",
        "ix_user_sessions_started_at",
        "ix_user_sessions_last_activity",
    ],
    "request_logs": [
        "ix_request_logs_id",
        "ix_request_logs_request_id",
        "ix_request_logs_user_id",
        "ix_request_logs_status_code",
        "ix_request_logs_timestamp",
    ],
    "generation_results": [
        "ix_generation_results_id",
        "ix_generation_results_user_id",
        "ix_generation_results_created_at",
    ],
    "paused_generation_sessions": [
        "ix_paused_generation_sessions_id",
        "ix_paused_generation_sessions_user_id",
        "ix_paused_generation_sessions_status",
        "ix_paused_generation_sessions_created_at",
    ],
    "generation_workflow_states": [
        "ix_generation_workflow_states_id",
        "ix_generation_workflow_states_user_id",
        "ix_generation_workflow_states_status",
        "ix_generation_workflow_states_created_at",
        "ix_generation_workflow_states_updated_at",
    ],
    "generation_workflow_checkpoints": [
        "ix_generation_workflow_checkpoints_id",
        "ix_generation_workflow_checkpoints_request_id",
        "ix_generation_workflow_checkpoints_user_id",
        "ix_generation_workflow_checkpoints_node_id",
        "ix_generation_workflow_checkpoints_status",
        "ix_generation_workflow_checkpoints_created_at",
    ],
    "user_runs": [
        "ix_user_runs_id",
        "ix_user_runs_user_id",
        "ix_user_runs_kind",
        "ix_user_runs_status",
        "ix_user_runs_created_at",
        "ix_user_runs_updated_at",
    ],
    "llm_usage_ledger": [
        "ix_llm_usage_ledger_id",
        "ix_llm_usage_ledger_user_id",
        "ix_llm_usage_ledger_run_id",
        "ix_llm_usage_ledger_node",
        "ix_llm_usage_ledger_role",
        "ix_llm_usage_ledger_provider",
        "ix_llm_usage_ledger_created_at",
        "ix_llm_usage_ledger_updated_at",
    ],
    "rubric_results": [
        "ix_rubric_results_id",
    ],
    "report_results": [
        "ix_report_results_id",
    ],
}


_DUPLICATE_WHEN_UNIQUE_EXISTS = {
    "generation_results": [
        ("idx_gen_results_request_id", ["request_id"]),
    ],
    "paused_generation_sessions": [
        ("idx_paused_gen_request_id", ["request_id"]),
    ],
    "generation_workflow_states": [
        ("idx_generation_workflow_request_id", ["request_id"]),
    ],
    "llm_usage_ledger": [
        ("idx_llm_usage_user_run_node", ["user_id", "run_id", "node"]),
        ("idx_llm_usage_user_run", ["user_id", "run_id"]),
    ],
    "rubric_results": [
        ("idx_rubric_gen_id", ["generation_result_id"]),
    ],
    "report_results": [
        ("idx_report_gen_id", ["generation_result_id"]),
    ],
}


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _indexes(table_name: str) -> list[dict]:
    return inspect(op.get_bind()).get_indexes(table_name)


def _drop_nonunique_index(table_name: str, index_name: str) -> None:
    for index in _indexes(table_name):
        if index.get("name") == index_name and not index.get("unique", False):
            op.drop_index(index_name, table_name=table_name)
            return


def _has_unique_prefix(table_name: str, columns: list[str]) -> bool:
    inspector = inspect(op.get_bind())
    for constraint in inspector.get_unique_constraints(table_name):
        names = constraint.get("column_names") or []
        if names[: len(columns)] == columns:
            return True
    for index in inspector.get_indexes(table_name):
        names = index.get("column_names") or []
        if index.get("unique", False) and names[: len(columns)] == columns:
            return True
    return False


def upgrade() -> None:
    existing_tables = _tables()
    for table_name, index_names in _NONUNIQUE_INDEXES.items():
        if table_name not in existing_tables:
            continue
        for index_name in index_names:
            _drop_nonunique_index(table_name, index_name)

    for table_name, candidates in _DUPLICATE_WHEN_UNIQUE_EXISTS.items():
        if table_name not in existing_tables:
            continue
        for index_name, columns in candidates:
            if _has_unique_prefix(table_name, columns):
                _drop_nonunique_index(table_name, index_name)


def downgrade() -> None:
    # Optimization-only migration: re-adding redundant indexes would restore the
    # previous bloat without restoring data. Older migrations still document the
    # original index set if a historical rebuild is required.
    pass
