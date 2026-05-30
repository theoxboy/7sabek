"""email center phase 3 preferences suppression queue foundation

Revision ID: 20260530_email_center_phase3_safe_foundation
Revises: 20260530_email_center_phase2e_campaign_drafts
Create Date: 2026-05-30 00:00:03.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260530_email_center_phase3_safe_foundation"
down_revision = "20260530_email_center_phase2e_campaign_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("salary_reminders_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("tips_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("product_updates_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("marketing_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("security_emails_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "email_unsubscribes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("token_hash", sa.String(length=255), nullable=True),
        sa.Column("unsubscribed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
    )

    op.create_table(
        "email_suppressions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("category", sa.String(length=50), nullable=True),
        sa.Column("reason", sa.String(length=50), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by_admin_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_admin_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_email_suppressions_email", "email_suppressions", ["email"], unique=False)
    op.create_index("ix_email_suppressions_user_id", "email_suppressions", ["user_id"], unique=False)

    op.add_column("email_campaigns", sa.Column("last_dry_run_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("email_campaigns", sa.Column("last_test_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("email_campaigns", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("email_campaigns", sa.Column("approved_by_admin_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("email_campaigns", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("email_campaigns", sa.Column("send_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("email_campaigns", sa.Column("send_finished_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("email_campaigns", sa.Column("total_recipients", sa.Integer(), nullable=True))
    op.add_column("email_campaigns", sa.Column("total_sent", sa.Integer(), nullable=True))
    op.add_column("email_campaigns", sa.Column("total_failed", sa.Integer(), nullable=True))
    op.add_column("email_campaigns", sa.Column("total_skipped", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_email_campaigns_approved_by_admin_id_users",
        "email_campaigns",
        "users",
        ["approved_by_admin_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column("email_deliveries", sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("email_deliveries", sa.Column("category", sa.String(length=50), nullable=True))
    op.add_column("email_deliveries", sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("email_deliveries", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("email_deliveries", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("email_deliveries", sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("email_deliveries", sa.Column("provider_status_code", sa.String(length=50), nullable=True))
    op.add_column("email_deliveries", sa.Column("provider_error_code", sa.String(length=100), nullable=True))
    op.create_foreign_key(
        "fk_email_deliveries_campaign_id_email_campaigns",
        "email_deliveries",
        "email_campaigns",
        ["campaign_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_email_deliveries_campaign_id_email_campaigns", "email_deliveries", type_="foreignkey")
    op.drop_column("email_deliveries", "provider_error_code")
    op.drop_column("email_deliveries", "provider_status_code")
    op.drop_column("email_deliveries", "last_attempt_at")
    op.drop_column("email_deliveries", "attempt_count")
    op.drop_column("email_deliveries", "next_attempt_at")
    op.drop_column("email_deliveries", "queued_at")
    op.drop_column("email_deliveries", "category")
    op.drop_column("email_deliveries", "campaign_id")

    op.drop_constraint("fk_email_campaigns_approved_by_admin_id_users", "email_campaigns", type_="foreignkey")
    op.drop_column("email_campaigns", "total_skipped")
    op.drop_column("email_campaigns", "total_failed")
    op.drop_column("email_campaigns", "total_sent")
    op.drop_column("email_campaigns", "total_recipients")
    op.drop_column("email_campaigns", "send_finished_at")
    op.drop_column("email_campaigns", "send_started_at")
    op.drop_column("email_campaigns", "sent_at")
    op.drop_column("email_campaigns", "approved_by_admin_id")
    op.drop_column("email_campaigns", "approved_at")
    op.drop_column("email_campaigns", "last_test_sent_at")
    op.drop_column("email_campaigns", "last_dry_run_at")

    op.drop_index("ix_email_suppressions_user_id", table_name="email_suppressions")
    op.drop_index("ix_email_suppressions_email", table_name="email_suppressions")
    op.drop_table("email_suppressions")
    op.drop_table("email_unsubscribes")
    op.drop_table("email_preferences")
