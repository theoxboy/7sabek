"""create advisor preview persistence table (sprint 1)

Revision ID: 20260403_advisor_v1_tables
Revises: 20260403_platform_ai_gateways
Create Date: 2026-04-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260403_advisor_v1_tables"
down_revision = "20260403_platform_ai_gateways"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "advisor_previews",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("preview_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("engine_version", sa.String(length=64), nullable=False),
        sa.Column("proposal_contract_version", sa.String(length=64), nullable=False),
        sa.Column("profile_hash", sa.String(length=128), nullable=False),
        sa.Column("gating_hash", sa.String(length=128), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("degraded_mode", sa.Boolean(), nullable=False),
        sa.Column("can_recommend_confidently", sa.Boolean(), nullable=False),
        sa.Column("recommended_proposal_id", sa.String(length=128), nullable=True),
        sa.Column("warnings_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "blocking_issues_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("data_quality_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("preview_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("superseded_by_preview_id", sa.String(length=128), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("preview_id", name="uq_advisor_previews_preview_id"),
    )

    op.create_index("ix_advisor_previews_user_id", "advisor_previews", ["user_id"], unique=False)
    op.create_index("ix_advisor_previews_status", "advisor_previews", ["status"], unique=False)
    op.create_index("ix_advisor_previews_expires_at", "advisor_previews", ["expires_at"], unique=False)
    op.create_index(
        "ix_advisor_previews_user_generated",
        "advisor_previews",
        ["user_id", "generated_at"],
        unique=False,
    )
    op.create_index(
        "ix_advisor_previews_user_status",
        "advisor_previews",
        ["user_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_advisor_previews_user_status", table_name="advisor_previews")
    op.drop_index("ix_advisor_previews_user_generated", table_name="advisor_previews")
    op.drop_index("ix_advisor_previews_expires_at", table_name="advisor_previews")
    op.drop_index("ix_advisor_previews_status", table_name="advisor_previews")
    op.drop_index("ix_advisor_previews_user_id", table_name="advisor_previews")

    op.drop_table("advisor_previews")
