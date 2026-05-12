from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AllocationCreate(BaseModel):
    amount: Decimal = Field(gt=0)
    occurred_on: date


class AllocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    envelope_period_id: UUID
    amount: Decimal


class PeriodBalanceOut(BaseModel):
    opening_balance: Decimal
    total_allocations: Decimal
    total_spent: Decimal
    closing_balance: Decimal


class AllocationWithBalanceOut(BaseModel):
    allocation: AllocationOut
    balance: PeriodBalanceOut


class AllocationFromCashCreate(BaseModel):
    amount: Decimal = Field(gt=0)
    occurred_on: date
    description: Optional[str] = None
