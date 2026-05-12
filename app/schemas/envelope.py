from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EnvelopeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    rollover_enabled: bool


class EnvelopeUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    rollover_enabled: Optional[bool] = None


class EnvelopeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    rollover_enabled: bool
    is_default_savings: bool
    is_cash: bool
    is_goal: bool
    deletable: bool


class EnvelopeAdjustmentCreate(BaseModel):
    new_balance: Decimal = Field(ge=0)
    occurred_on: Optional[date] = None
