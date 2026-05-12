from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class EnvelopeMovementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    envelope_period_id: UUID
    amount: Decimal
