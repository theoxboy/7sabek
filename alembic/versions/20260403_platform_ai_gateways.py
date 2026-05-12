"""add platform ai gateways settings

Revision ID: 20260403_platform_ai_gateways
Revises: 20260331_goal_type_and_sinking_funds
Create Date: 2026-04-03
"""

from alembic import op
import sqlalchemy as sa


revision = "20260403_platform_ai_gateways"
down_revision = "20260331_goal_type_and_sinking_funds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "platform_settings",
        sa.Column("ai_gateways", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
    )
    op.add_column(
        "platform_settings",
        sa.Column(
            "ai_routing",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )
    op.execute(
        """
        UPDATE platform_settings
        SET ai_routing = json_build_object(
            'default_gateway_id', '',
            'default_model', '',
            'fallback_gateway_ids', '[]'::json,
            'request_timeout_ms', 60000
        )
        """
    )
    op.alter_column("platform_settings", "ai_gateways", server_default=None)
    op.alter_column("platform_settings", "ai_routing", server_default=None)


def downgrade() -> None:
    op.drop_column("platform_settings", "ai_routing")
    op.drop_column("platform_settings", "ai_gateways")
