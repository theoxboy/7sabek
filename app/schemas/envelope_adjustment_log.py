from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class EnvelopeAdjustmentLogOut(BaseModel):
    id: UUID
    envelope_id: UUID
    period_start: date
    period_end: date
    previous_balance: Decimal
    new_balance: Decimal
    delta: Decimal
    created_at: datetime

    class Config:
        from_attributes = True
