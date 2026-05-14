"""initial schemas and system tables

Revision ID: 0001
Revises:
Create Date: 2026-05-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from macro_trader.db.schemas import DOMAIN_SCHEMAS

revision = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


METHOD_STATUS_VALUES = ("development", "baseline", "shadow", "production", "deprecated")
USER_ROLE_VALUES = ("admin", "user")


def upgrade() -> None:
    # ----- 1. Create all domain schemas. ------------------------------
    for schema in DOMAIN_SCHEMAS:
        op.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))

    # ----- 2. Native enum types. --------------------------------------
    method_status = postgresql.ENUM(
        *METHOD_STATUS_VALUES, name="method_status", schema="system", create_type=False
    )
    method_status.create(op.get_bind(), checkfirst=True)

    user_role = postgresql.ENUM(
        *USER_ROLE_VALUES, name="user_role", schema="auth", create_type=False
    )
    user_role.create(op.get_bind(), checkfirst=True)

    # ----- 3. system.methods_registry ---------------------------------
    op.create_table(
        "methods_registry",
        sa.Column("method_id", sa.String(128), primary_key=True),
        sa.Column("component", sa.String(64), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                *METHOD_STATUS_VALUES, name="method_status", schema="system", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("metadata_json", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("status_changed_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("status_reason", sa.Text, nullable=False, server_default=""),
        sa.Column("serialized_blob", sa.LargeBinary, nullable=True),
        schema="system",
    )
    op.create_index(
        "ix_methods_registry_component",
        "methods_registry",
        ["component"],
        schema="system",
    )
    op.create_index(
        "ix_methods_registry_status", "methods_registry", ["status"], schema="system"
    )

    # ----- 4. system.method_status_history ----------------------------
    op.create_table(
        "method_status_history",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "method_id",
            sa.String(128),
            sa.ForeignKey("system.methods_registry.method_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "old_status",
            postgresql.ENUM(
                *METHOD_STATUS_VALUES, name="method_status", schema="system", create_type=False
            ),
            nullable=True,
        ),
        sa.Column(
            "new_status",
            postgresql.ENUM(
                *METHOD_STATUS_VALUES, name="method_status", schema="system", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("changed_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        schema="system",
    )
    op.create_index(
        "ix_method_status_history_method_id",
        "method_status_history",
        ["method_id"],
        schema="system",
    )

    # ----- 5. system.method_comparisons -------------------------------
    op.create_table(
        "method_comparisons",
        sa.Column("comparison_id", sa.String(64), primary_key=True),
        sa.Column("component", sa.String(64), nullable=False),
        sa.Column("method_a_id", sa.String(128), nullable=False),
        sa.Column("method_b_id", sa.String(128), nullable=False),
        sa.Column("period_start", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("period_end", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("metrics", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("agreement", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("stability", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        schema="system",
    )
    op.create_index(
        "ix_method_comparisons_component",
        "method_comparisons",
        ["component"],
        schema="system",
    )
    op.create_index(
        "ix_method_comparisons_methods",
        "method_comparisons",
        ["method_a_id", "method_b_id"],
        schema="system",
    )

    # ----- 6. system.heartbeat ----------------------------------------
    op.create_table(
        "heartbeat",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("timestamp", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("meta", postgresql.JSONB, nullable=False, server_default="{}"),
        schema="system",
    )

    # ----- 7. auth.users + auth.refresh_tokens ------------------------
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("email", sa.String(256), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(256), nullable=False),
        sa.Column(
            "role",
            postgresql.ENUM(
                *USER_ROLE_VALUES, name="user_role", schema="auth", create_type=False
            ),
            nullable=False,
            server_default="user",
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column(
            "is_superuser", sa.Boolean, nullable=False, server_default=sa.text("false")
        ),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        schema="auth",
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True, schema="auth")

    op.create_table(
        "refresh_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("auth.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(256), nullable=False),
        sa.Column("issued_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("user_agent", sa.Text, nullable=False, server_default=""),
        schema="auth",
    )
    op.create_index(
        "ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"], schema="auth"
    )


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens", schema="auth")
    op.drop_table("refresh_tokens", schema="auth")
    op.drop_index("ix_users_email", table_name="users", schema="auth")
    op.drop_table("users", schema="auth")

    op.drop_table("heartbeat", schema="system")

    op.drop_index(
        "ix_method_comparisons_methods", table_name="method_comparisons", schema="system"
    )
    op.drop_index(
        "ix_method_comparisons_component", table_name="method_comparisons", schema="system"
    )
    op.drop_table("method_comparisons", schema="system")

    op.drop_index(
        "ix_method_status_history_method_id",
        table_name="method_status_history",
        schema="system",
    )
    op.drop_table("method_status_history", schema="system")

    op.drop_index("ix_methods_registry_status", table_name="methods_registry", schema="system")
    op.drop_index(
        "ix_methods_registry_component", table_name="methods_registry", schema="system"
    )
    op.drop_table("methods_registry", schema="system")

    op.execute(sa.text('DROP TYPE IF EXISTS "auth"."user_role"'))
    op.execute(sa.text('DROP TYPE IF EXISTS "system"."method_status"'))

    for schema in reversed(DOMAIN_SCHEMAS):
        op.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
