"""add rank to distribution rules

Revision ID: 20260201_distribution_rules_rank
Revises: 20260126_distribution_simple
Create Date: 2026-02-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260201_distribution_rules_rank"
down_revision = "20260126_distribution_simple"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "distribution_rules",
        sa.Column("rank", sa.Integer(), nullable=False, server_default="1"),
    )
    op.execute("UPDATE distribution_rules SET rank = priority")
    op.alter_column("distribution_rules", "rank", server_default=None)


def downgrade() -> None:
    op.drop_column("distribution_rules", "rank")
