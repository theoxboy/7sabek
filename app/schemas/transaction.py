from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.envelope_movement import EnvelopeMovementOut


class TransactionCreate(BaseModel):
    type: str = Field(pattern="^(income|expense)$")
    category_id: UUID
    amount: Decimal = Field(gt=0)
    occurred_on: date
    description: Optional[str] = Field(default=None, max_length=255)
    source: str = Field(default="manual", pattern="^(manual|assistant)$")


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    category_id: UUID
    amount: Decimal
    occurred_on: date
    description: Optional[str]
    source: str
    envelope_movement: Optional[EnvelopeMovementOut]
    created_at: Optional[datetime] = None


class TransactionUpdate(BaseModel):
    type: Optional[str] = Field(default=None, pattern="^(income|expense)$")
    category_id: Optional[UUID] = None
    amount: Optional[Decimal] = Field(default=None, gt=0)
    occurred_on: Optional[date] = None
    description: Optional[str] = Field(default=None, max_length=255)
    source: Optional[str] = Field(default=None, pattern="^(manual|assistant)$")
