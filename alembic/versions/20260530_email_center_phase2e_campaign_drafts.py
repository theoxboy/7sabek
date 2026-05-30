"""add email center campaign drafts

Revision ID: 20260530_email_center_phase2e_campaign_drafts
Revises: 20260530_email_center_phase2c_templates
Create Date: 2026-05-30 00:00:02.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260530_email_center_phase2e_campaign_drafts"
down_revision = "20260530_email_center_phase2c_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_campaigns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("type", sa.String(length=40), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("audience_type", sa.String(length=40), nullable=False),
        sa.Column("audience_filter_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("language_mode", sa.String(length=16), nullable=False, server_default="auto"),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subject_by_language_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("preview_by_language_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("body_by_language_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("cta_label_by_language_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("cta_url", sa.Text(), nullable=True),
        sa.Column("design_settings_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("estimated_recipient_count", sa.Integer(), nullable=True),
        sa.Column("created_by_admin_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["template_id"], ["email_templates.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_admin_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_email_campaigns_status", "email_campaigns", ["status"], unique=False)
    op.create_index("ix_email_campaigns_audience_type", "email_campaigns", ["audience_type"], unique=False)
    op.create_index("ix_email_campaigns_created_at", "email_campaigns", ["created_at"], unique=False)
    op.create_index("ix_email_campaigns_deleted_at", "email_campaigns", ["deleted_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_email_campaigns_deleted_at", table_name="email_campaigns")
    op.drop_index("ix_email_campaigns_created_at", table_name="email_campaigns")
    op.drop_index("ix_email_campaigns_audience_type", table_name="email_campaigns")
    op.drop_index("ix_email_campaigns_status", table_name="email_campaigns")
    op.drop_table("email_campaigns")
