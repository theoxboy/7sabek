"""passkeys foundation tables

Revision ID: 20260523_passkeys_foundation
Revises: 20260508_platform_settings_guided_tours_toggle
Create Date: 2026-05-23 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260523_passkeys_foundation"
down_revision = "20260508_platform_settings_guided_tours_toggle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.create_table(
        "user_passkeys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_id", sa.Text(), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("sign_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("transports", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("aaguid", sa.Text(), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("credential_id", name="uq_user_passkeys_credential_id"),
    )
    op.create_index(
        "ix_user_passkeys_user_revoked",
        "user_passkeys",
        ["user_id", "revoked_at"],
        unique=False,
    )

    op.create_table(
        "webauthn_challenges",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("challenge_hash", sa.String(length=64), nullable=False),
        sa.Column("flow", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("request_ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint("flow IN ('register', 'login')", name="ck_webauthn_challenges_flow"),
    )
    op.create_index(
        "ix_webauthn_challenges_flow_user_expires",
        "webauthn_challenges",
        ["flow", "user_id", "expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_webauthn_challenges_expires",
        "webauthn_challenges",
        ["expires_at"],
        unique=False,
    )
    op.alter_column("user_passkeys", "sign_count", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_webauthn_challenges_expires", table_name="webauthn_challenges")
    op.drop_index(
        "ix_webauthn_challenges_flow_user_expires",
        table_name="webauthn_challenges",
    )
    op.drop_table("webauthn_challenges")

    op.drop_index("ix_user_passkeys_user_revoked", table_name="user_passkeys")
    op.drop_table("user_passkeys")
