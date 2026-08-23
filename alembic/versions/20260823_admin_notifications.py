"""add_admin_notifications

Revision ID: 20260823_admin_notifications
Revises: 9fe616f2d3b7
Create Date: 2026-08-23 18:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '20260823_admin_notifications'
down_revision: Union[str, None] = '9fe616f2d3b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    # 1. admin_notifications
    if 'admin_notifications' not in tables:
        op.create_table(
            'admin_notifications',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('title_fr', sa.String(length=255), nullable=False),
            sa.Column('title_ar', sa.String(length=255), nullable=False),
            sa.Column('message_fr', sa.Text(), nullable=False),
            sa.Column('message_ar', sa.Text(), nullable=False),
            sa.Column('notification_type', sa.String(length=50), nullable=False, server_default='general'),
            sa.Column('target_audience', sa.String(length=50), nullable=False, server_default='all'),
            sa.Column('target_user_email', sa.String(length=255), nullable=True),
            sa.Column('action_type', sa.String(length=50), nullable=False, server_default='none'),
            sa.Column('action_url', sa.String(length=500), nullable=True),
            sa.Column('haptic_effect', sa.String(length=50), nullable=False, server_default='Success'),
            sa.Column('priority', sa.String(length=20), nullable=False, server_default='normal'),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('sent_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('read_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('created_by_email', sa.String(length=255), nullable=True),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_admin_notifications_id'), 'admin_notifications', ['id'], unique=False)

    # 2. admin_notification_reads
    if 'admin_notification_reads' not in tables:
        op.create_table(
            'admin_notification_reads',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('notification_id', sa.Integer(), nullable=False),
            sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('read_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_admin_notification_reads_id'), 'admin_notification_reads', ['id'], unique=False)
        op.create_index(op.f('ix_admin_notification_reads_notification_id'), 'admin_notification_reads', ['notification_id'], unique=False)
        op.create_index(op.f('ix_admin_notification_reads_user_id'), 'admin_notification_reads', ['user_id'], unique=False)

    # 3. device_tokens
    if 'device_tokens' not in tables:
        op.create_table(
            'device_tokens',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('token', sa.String(length=500), nullable=False),
            sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=True),
            sa.Column('user_email', sa.String(length=255), nullable=True),
            sa.Column('platform', sa.String(length=50), nullable=False, server_default='android'),
            sa.Column('language', sa.String(length=10), nullable=False, server_default='fr'),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_device_tokens_id'), 'device_tokens', ['id'], unique=False)
        op.create_index(op.f('ix_device_tokens_token'), 'device_tokens', ['token'], unique=True)
        op.create_index(op.f('ix_device_tokens_user_id'), 'device_tokens', ['user_id'], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = set(inspector.get_table_names())

    if 'device_tokens' in tables:
        op.drop_table('device_tokens')

    if 'admin_notification_reads' in tables:
        op.drop_table('admin_notification_reads')

    if 'admin_notifications' in tables:
        op.drop_table('admin_notifications')
