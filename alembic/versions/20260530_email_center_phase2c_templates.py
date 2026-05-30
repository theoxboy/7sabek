"""add email center templates library

Revision ID: 20260530_email_center_phase2c_templates
Revises: 20260530_email_center_phase2a_delivery_fields
Create Date: 2026-05-30 00:00:01.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260530_email_center_phase2c_templates"
down_revision = "20260530_email_center_phase2a_delivery_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.String(length=120), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False, server_default="custom"),
        sa.Column("language", sa.String(length=16), nullable=False, server_default="fr"),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("preview_text", sa.String(length=255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("cta_label", sa.String(length=120), nullable=True),
        sa.Column("cta_url", sa.String(length=500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_admin_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["created_by_admin_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("key", name="uq_email_templates_key"),
    )
    op.create_index("ix_email_templates_language", "email_templates", ["language"], unique=False)
    op.create_index("ix_email_templates_category", "email_templates", ["category"], unique=False)
    op.create_index("ix_email_templates_active", "email_templates", ["is_active"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_email_templates_active", table_name="email_templates")
    op.drop_index("ix_email_templates_category", table_name="email_templates")
    op.drop_index("ix_email_templates_language", table_name="email_templates")
    op.drop_table("email_templates")
