"""add email center phase 0/1 tables

Revision ID: 20260529_email_center_phase01
Revises: 20260523_passkeys_foundation
Create Date: 2026-05-29 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260529_email_center_phase01"
down_revision = "20260523_passkeys_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_design_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("brand_name", sa.String(length=120), nullable=False, server_default="7sabek"),
        sa.Column("logo_url", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("primary_color", sa.String(length=20), nullable=False, server_default="#0f172a"),
        sa.Column("button_color", sa.String(length=20), nullable=False, server_default="#0f172a"),
        sa.Column(
            "footer_text",
            sa.String(length=500),
            nullable=False,
            server_default="Merci d'utiliser 7sabek.",
        ),
        sa.Column("support_email", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
    )
    op.create_index("ix_email_design_settings_id", "email_design_settings", ["id"], unique=False)

    op.create_table(
        "email_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("recipient_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_by_admin_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_admin_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_email_deliveries_created_at", "email_deliveries", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_email_deliveries_created_at", table_name="email_deliveries")
    op.drop_table("email_deliveries")
    op.drop_index("ix_email_design_settings_id", table_name="email_design_settings")
    op.drop_table("email_design_settings")
