"""add cash envelope flag

Revision ID: 20240313_add_cash
Revises: 20240313_rollover_default
Create Date: 2024-03-13 00:00:04.000000
"""

from __future__ import annotations

from uuid import uuid4

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20240313_add_cash"
down_revision = "20240313_rollover_default"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "envelopes",
        sa.Column(
            "is_cash", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.create_index(
        "uq_envelopes_user_cash",
        "envelopes",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_cash = true"),
    )

    bind = op.get_bind()
    users = bind.execute(sa.text("SELECT id FROM users")).fetchall()
    for (user_id,) in users:
        existing = bind.execute(
            sa.text(
                "SELECT 1 FROM envelopes WHERE user_id = :user_id AND is_cash = true"
            ),
            {"user_id": user_id},
        ).first()
        if existing:
            continue
        bind.execute(
            sa.text(
                """
                INSERT INTO envelopes (id, user_id, name, rollover_enabled, is_default_savings, is_cash, deletable)
                VALUES (:id, :user_id, :name, :rollover_enabled, :is_default_savings, :is_cash, :deletable)
                """
            ),
            {
                "id": str(uuid4()),
                "user_id": user_id,
                "name": "Cash",
                "rollover_enabled": False,
                "is_default_savings": False,
                "is_cash": True,
                "deletable": False,
            },
        )

    op.alter_column("envelopes", "is_cash", server_default=None)


def downgrade() -> None:
    op.drop_index("uq_envelopes_user_cash", table_name="envelopes")
    op.drop_column("envelopes", "is_cash")
