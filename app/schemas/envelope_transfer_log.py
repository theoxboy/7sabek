from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class EnvelopeTransferLogOut(BaseModel):
    id: UUID
    to_envelope_id: UUID
    from_envelope_id: Optional[UUID]
    from_envelope_name: str
    amount: Decimal
    period_start: date
    period_end: date
    created_at: datetime

    class Config:
        from_attributes = True
