"""distribution config versioning and execution binding

Revision ID: 20260501_distribution_config_versioning
Revises: 20260501_user_auto_sweep_enabled
Create Date: 2026-05-01 21:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260501_distribution_config_versioning"
down_revision = "20260501_user_auto_sweep_enabled"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "distribution_saved_configs",
        sa.Column("source", sa.String(length=32), nullable=False, server_default="post_onboarding_adjustment"),
    )
    op.add_column(
        "distribution_saved_configs",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "distribution_saved_configs",
        sa.Column("effective_from_period_start", sa.Date(), nullable=True),
    )
    op.create_check_constraint(
        "ck_distribution_saved_configs_source",
        "distribution_saved_configs",
        "source IN ('onboarding_initial','post_onboarding_adjustment')",
    )
    op.create_unique_constraint(
        "uq_distribution_saved_configs_user_version",
        "distribution_saved_configs",
        ["user_id", "version"],
    )

    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                user_id,
                ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY created_at ASC, id ASC) AS rn
            FROM distribution_saved_configs
        )
        UPDATE distribution_saved_configs c
        SET
            source = CASE WHEN r.rn = 1 THEN 'onboarding_initial' ELSE 'post_onboarding_adjustment' END,
            version = r.rn
        FROM ranked r
        WHERE c.id = r.id
        """
    )

    op.add_column(
        "distribution_logs",
        sa.Column("config_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "distribution_logs",
        sa.Column("config_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "distribution_logs",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="applied"),
    )
    op.create_check_constraint(
        "ck_distribution_logs_status",
        "distribution_logs",
        "status IN ('applied','simulated','skipped')",
    )
    op.create_foreign_key(
        "fk_distribution_logs_config_id",
        "distribution_logs",
        "distribution_saved_configs",
        ["config_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_distribution_logs_config_id", "distribution_logs", type_="foreignkey")
    op.drop_constraint("ck_distribution_logs_status", "distribution_logs", type_="check")
    op.drop_column("distribution_logs", "status")
    op.drop_column("distribution_logs", "config_version")
    op.drop_column("distribution_logs", "config_id")

    op.drop_constraint("uq_distribution_saved_configs_user_version", "distribution_saved_configs", type_="unique")
    op.drop_constraint("ck_distribution_saved_configs_source", "distribution_saved_configs", type_="check")
    op.drop_column("distribution_saved_configs", "effective_from_period_start")
    op.drop_column("distribution_saved_configs", "version")
    op.drop_column("distribution_saved_configs", "source")
