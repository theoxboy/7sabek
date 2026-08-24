"""advisor chat history shared across clients

Revision ID: 20260824_advisor_chat
Revises: 20260823_admin_notifications
Create Date: 2026-08-24 10:00:00.000000

The advisor conversation was kept in the browser's localStorage, so the web app
and the Android app each showed a different history for the same account. This
table makes the conversation belong to the user instead of to the device.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '20260824_advisor_chat'
down_revision: Union[str, None] = '20260823_admin_notifications'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'advisor_chat_messages' in set(inspector.get_table_names()):
        return

    op.create_table(
        'advisor_chat_messages',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        # Insertion order: now() is the transaction timestamp, so two rows
        # written by the same request cannot be ordered by created_at.
        sa.Column('seq', sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column(
            'user_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_advisor_chat_messages_user_seq',
        'advisor_chat_messages',
        ['user_id', 'seq'],
        unique=False,
    )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'advisor_chat_messages' not in set(inspector.get_table_names()):
        return
    op.drop_index('ix_advisor_chat_messages_user_seq', table_name='advisor_chat_messages')
    op.drop_table('advisor_chat_messages')
