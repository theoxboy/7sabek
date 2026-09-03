"""guest mode (Mode Découverte) — guest users + idempotency keys

Revision ID: 20260902_guest_mode
Revises: 20260826_income_dist_once
Create Date: 2026-09-02 00:00:00.000000

Note: `main` already carries two unmerged alembic heads
(`20260530_registration_leads` and `20260826_income_dist_once`); this chains off
the applied one. Use `alembic upgrade 20260902_guest_mode` rather than `head`.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260902_guest_mode"
down_revision = "20260826_income_dist_once"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A guest is a real users row with is_guest = true, no email/password/onboarding.
    op.add_column(
        "users",
        sa.Column("is_guest", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("users", sa.Column("guest_token_hash", sa.String(length=64), nullable=True))
    op.add_column("users", sa.Column("guest_created_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("recovery_code_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "users",
        sa.Column("recovery_code_ack_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index(
        "ix_users_guest_token_hash",
        "users",
        ["guest_token_hash"],
        unique=True,
        postgresql_where=sa.text("guest_token_hash IS NOT NULL"),
    )
    op.create_index(
        "ix_users_is_guest",
        "users",
        ["is_guest"],
        unique=False,
        postgresql_where=sa.text("is_guest = true"),
    )

    # Guests have no email until they claim an account.
    op.alter_column("users", "email", existing_type=sa.String(length=255), nullable=True)

    # Idempotency for POST /auth/guest: a retried request (flaky mobile network)
    # must return the same guest, never create a second one.
    op.create_table(
        "guest_idempotency_keys",
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("guest_token", sa.String(length=128), nullable=False),
        sa.Column("recovery_code", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_guest_idempotency_keys_expires_at",
        "guest_idempotency_keys",
        ["expires_at"],
        unique=False,
    )

    # L2 device anchors — table created now, populated in phase 3.
    op.create_table(
        "device_anchors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("anchor_hash", sa.String(length=64), nullable=False),
        sa.Column("signals", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_device_anchors_anchor_hash", "device_anchors", ["anchor_hash"], unique=False)
    op.create_index("ix_device_anchors_user_id", "device_anchors", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_device_anchors_user_id", table_name="device_anchors")
    op.drop_index("ix_device_anchors_anchor_hash", table_name="device_anchors")
    op.drop_table("device_anchors")

    op.drop_index("ix_guest_idempotency_keys_expires_at", table_name="guest_idempotency_keys")
    op.drop_table("guest_idempotency_keys")

    op.alter_column("users", "email", existing_type=sa.String(length=255), nullable=False)

    op.drop_index("ix_users_is_guest", table_name="users")
    op.drop_index("ix_users_guest_token_hash", table_name="users")
    op.drop_column("users", "recovery_code_ack_at")
    op.drop_column("users", "recovery_code_hash")
    op.drop_column("users", "claimed_at")
    op.drop_column("users", "guest_created_at")
    op.drop_column("users", "guest_token_hash")
    op.drop_column("users", "is_guest")
