"""
add advisor_global_instructions column to platform_settings
Revision ID: 20260612_advisor_global_instructions
Revises: 20260530_registration_leads
Create Date: 2026-06-12
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260612_advisor_global_instructions"
down_revision: Union[str, None] = "20260530_registration_leads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "platform_settings",
        sa.Column(
            "advisor_global_instructions",
            sa.String(length=2000),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("platform_settings", "advisor_global_instructions")
