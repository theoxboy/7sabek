"""registration leads capture

Revision ID: 20260530_registration_leads
Revises: 20260530_email_center_phase3_safe_foundation
Create Date: 2026-05-30 12:40:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260530_registration_leads"
down_revision = "20260530_email_center_phase3_safe_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "registration_leads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("first_name", sa.String(length=120), nullable=True),
        sa.Column("last_name", sa.String(length=120), nullable=True),
        sa.Column("phone", sa.String(length=30), nullable=True),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("normalized_email", sa.String(length=255), nullable=True),
        sa.Column("country", sa.String(length=120), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("current_step", sa.Integer(), nullable=True),
        sa.Column("highest_step_reached", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=True, server_default="register"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="partial"),
        sa.Column("converted_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("converted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["converted_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_registration_leads_normalized_email", "registration_leads", ["normalized_email"], unique=False)
    op.create_index("ix_registration_leads_status", "registration_leads", ["status"], unique=False)
    op.create_index("ix_registration_leads_created_at", "registration_leads", ["created_at"], unique=False)
    op.create_index("ix_registration_leads_last_seen_at", "registration_leads", ["last_seen_at"], unique=False)

    op.add_column("email_deliveries", sa.Column("registration_lead_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_email_deliveries_registration_lead_id_registration_leads",
        "email_deliveries",
        "registration_leads",
        ["registration_lead_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_email_deliveries_registration_lead_id_registration_leads",
        "email_deliveries",
        type_="foreignkey",
    )
    op.drop_column("email_deliveries", "registration_lead_id")

    op.drop_index("ix_registration_leads_last_seen_at", table_name="registration_leads")
    op.drop_index("ix_registration_leads_created_at", table_name="registration_leads")
    op.drop_index("ix_registration_leads_status", table_name="registration_leads")
    op.drop_index("ix_registration_leads_normalized_email", table_name="registration_leads")
    op.drop_table("registration_leads")
