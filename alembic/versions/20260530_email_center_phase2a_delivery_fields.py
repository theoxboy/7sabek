"""add email center phase 2a delivery fields

Revision ID: 20260530_email_center_phase2a_delivery_fields
Revises: 20260529_email_center_phase01
Create Date: 2026-05-30 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260530_email_center_phase2a_delivery_fields"
down_revision = "20260529_email_center_phase01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_deliveries", sa.Column("original_recipient_email", sa.String(length=255), nullable=True))
    op.add_column("email_deliveries", sa.Column("note", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("email_deliveries", "note")
    op.drop_column("email_deliveries", "original_recipient_email")
