"""add distribution saved configs for onboarding step 4 status

Revision ID: 20260414_distribution_saved_configs
Revises: 20260403_advisor_v1_tables
Create Date: 2026-04-14
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260414_distribution_saved_configs"
down_revision = "20260403_advisor_v1_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "distribution_saved_configs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "rows",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("signature", sa.String(length=200), nullable=False),
        sa.Column(
            "percent_mode",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'equal'"),
        ),
        sa.Column(
            "auto_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("scope_hash", sa.String(length=120), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_distribution_saved_configs_user_updated",
        "distribution_saved_configs",
        ["user_id", "updated_at"],
        unique=False,
    )
    op.create_index(
        "ix_distribution_saved_configs_user_signature",
        "distribution_saved_configs",
        ["user_id", "signature"],
        unique=False,
    )
    op.create_index(
        "uq_distribution_saved_configs_user_active",
        "distribution_saved_configs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_distribution_saved_configs_user_active",
        table_name="distribution_saved_configs",
        postgresql_where=sa.text("is_active = true"),
    )
    op.drop_index(
        "ix_distribution_saved_configs_user_signature",
        table_name="distribution_saved_configs",
    )
    op.drop_index(
        "ix_distribution_saved_configs_user_updated",
        table_name="distribution_saved_configs",
    )
    op.drop_table("distribution_saved_configs")
