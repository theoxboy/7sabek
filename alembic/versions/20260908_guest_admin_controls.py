"""guest mode admin controls (superadmin toggle + custom message)

Revision ID: 20260908_guest_admin
Revises: 20260903_guest_events
Create Date: 2026-09-08 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260908_guest_admin"
down_revision = "20260903_guest_events"
branch_labels = None
depends_on = None


_DEFAULT_FR = (
    "Le mode découverte est en pause pour le moment. "
    "Crée ton compte gratuit — ça prend 20 secondes."
)
_DEFAULT_EN = (
    "Discovery mode is paused for now. "
    "Create your free account — it takes 20 seconds."
)
_DEFAULT_AR = "وضع الاكتشاف موقّف دابا. صاوب حسابك المجاني — كياخد 20 ثانية."


def upgrade() -> None:
    op.add_column(
        "platform_settings",
        sa.Column("guest_mode_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "platform_settings",
        sa.Column("guest_mode_button", sa.String(length=16), nullable=False, server_default="message"),
    )
    op.add_column(
        "platform_settings",
        sa.Column("guest_mode_message_fr", sa.String(length=600), nullable=False, server_default=_DEFAULT_FR),
    )
    op.add_column(
        "platform_settings",
        sa.Column("guest_mode_message_en", sa.String(length=600), nullable=False, server_default=_DEFAULT_EN),
    )
    op.add_column(
        "platform_settings",
        sa.Column("guest_mode_message_ar", sa.String(length=600), nullable=False, server_default=_DEFAULT_AR),
    )
    op.add_column(
        "platform_settings",
        sa.Column("guest_mode_message_type", sa.String(length=16), nullable=False, server_default="info"),
    )
    op.add_column(
        "platform_settings",
        sa.Column("guest_mode_fallback_cta", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "platform_settings",
        sa.Column("guest_mode_placements", sa.JSON(), nullable=False, server_default='["login", "register"]'),
    )
    op.add_column(
        "platform_settings",
        sa.Column("guest_mode_kill_existing", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    for col in (
        "guest_mode_kill_existing",
        "guest_mode_placements",
        "guest_mode_fallback_cta",
        "guest_mode_message_type",
        "guest_mode_message_ar",
        "guest_mode_message_en",
        "guest_mode_message_fr",
        "guest_mode_button",
        "guest_mode_enabled",
    ):
        op.drop_column("platform_settings", col)
