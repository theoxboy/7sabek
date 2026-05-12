from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class EnvelopePeriodOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    period_start: date
    period_end: date
    opening_balance: Decimal
    total_allocations: Decimal
    total_spent: Decimal
    closing_balance: Decimal
    swept_at: Optional[datetime] = None
