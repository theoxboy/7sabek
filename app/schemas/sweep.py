from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class SweepRun(BaseModel):
    as_of: date


class SweepRunOut(BaseModel):
    periods_swept: int
    sweeps_created: int


class SweepOut(BaseModel):
    id: UUID
    amount: Decimal
    swept_on: date
    created_at: datetime
    from_envelope_id: UUID
    from_envelope_name: Optional[str] = None
    to_envelope_id: UUID
    to_envelope_name: Optional[str] = None


class SweepPreviewItem(BaseModel):
    from_envelope_id: UUID
    from_envelope_name: str
    to_envelope_id: UUID
    to_envelope_name: str
    amount: Decimal
