"""
Enforce envelope rollover constraints and clean up orphaned distribution rules
Revision ID: 20260626_fix_technical_debt
Revises: 58f9974613c7
Create Date: 2026-06-26 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20260626_fix_technical_debt'
down_revision: Union[str, None] = '58f9974613c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Update envelopes targeted by active fixed distribution rules to have rollover_enabled = True
    op.execute(
        """
        UPDATE envelopes
        SET rollover_enabled = true
        WHERE id IN (
            SELECT target_id
            FROM distribution_rules
            WHERE target_type = 'envelope'
              AND mode IN ('fixed', 'fixed_per_period')
              AND enabled = true
        ) AND rollover_enabled = false;
        """
    )
    
    # 2. Update envelopes matching debt keywords or marked as goal to have rollover_enabled = True
    op.execute(
        """
        UPDATE envelopes
        SET rollover_enabled = true
        WHERE (is_goal = true OR name ~* 'dettes|debt|debts|credit|crédit|crédits|repayment|repayments|loan|loans|دين|الديون|ديون|قرض|قروض')
          AND rollover_enabled = false;
        """
    )
    
    # 3. Purge orphaned distribution rules pointing to deleted envelopes
    op.execute(
        """
        DELETE FROM distribution_rules
        WHERE target_type = 'envelope'
          AND target_id NOT IN (SELECT id FROM envelopes);
        """
    )

    # 4. Purge orphaned distribution items pointing to deleted envelopes
    op.execute(
        """
        DELETE FROM distribution_items
        WHERE target_type = 'envelope'
          AND target_id NOT IN (SELECT id FROM envelopes);
        """
    )


def downgrade() -> None:
    pass
