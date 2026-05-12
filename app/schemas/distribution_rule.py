from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class DistributionRuleBase(BaseModel):
    target_type: str = Field(pattern="^(envelope|goal)$")
    target_id: UUID
    mode: str = Field(pattern="^(fixed|percent|fixed_per_period|percent_of_income)$")
    amount: Optional[Decimal] = None
    percent: Optional[Decimal] = None
    priority: int = Field(default=100, ge=0, le=10_000)
    rank: int = Field(default=1, ge=1, le=10_000)
    enabled: bool = True
    auto_apply_on_income: bool = True


class DistributionRuleCreate(DistributionRuleBase):
    pass


class DistributionRuleUpdate(BaseModel):
    target_type: Optional[str] = Field(default=None, pattern="^(envelope|goal)$")
    target_id: Optional[UUID] = None
    mode: Optional[str] = Field(
        default=None, pattern="^(fixed|percent|fixed_per_period|percent_of_income)$"
    )
    amount: Optional[Decimal] = None
    percent: Optional[Decimal] = None
    priority: Optional[int] = Field(default=None, ge=0, le=10_000)
    rank: Optional[int] = Field(default=None, ge=1, le=10_000)
    enabled: Optional[bool] = None
    auto_apply_on_income: Optional[bool] = None


class DistributionRuleReorderItem(BaseModel):
    id: UUID
    rank: int = Field(ge=1, le=10_000)


class DistributionRuleOut(DistributionRuleBase):
    id: UUID
    created_at: datetime
