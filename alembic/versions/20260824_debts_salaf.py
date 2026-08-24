"""create debts and salaf table

Revision ID: 20260824_debts_salaf
Revises: 20260824_advisor_chat
Create Date: 2026-08-24 16:00:00.000000

Adds the debts table to store user debts and loans (Salaf - كنسال / كيسالوني)
with contact details, total and paid amounts, due date and notes.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '20260824_debts_salaf'
down_revision: Union[str, None] = '20260824_advisor_chat'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'debts' in set(inspector.get_table_names()):
        return

    op.create_table(
        'debts',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            'user_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('contact_name', sa.String(length=120), nullable=False),
        sa.Column('contact_phone', sa.String(length=50), nullable=True),
        sa.Column('total_amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('paid_amount', sa.Numeric(precision=12, scale=2), server_default='0.00', nullable=False),
        sa.Column('is_loan_given', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_debts_user_created',
        'debts',
        ['user_id', 'created_at'],
        unique=False,
    )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'debts' not in set(inspector.get_table_names()):
        return
    op.drop_index('ix_debts_user_created', table_name='debts')
    op.drop_table('debts')
