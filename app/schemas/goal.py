from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class GoalCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    goal_type: str = Field(default="goal", min_length=1, max_length=24)
    target_amount: Decimal = Field(gt=0)
    target_date: Optional[date] = None
    contribution_amount: Optional[Decimal] = Field(default=None, gt=0)
    auto_contribute: bool = True
    priority: int = Field(default=2, ge=1, le=3)


class GoalUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    goal_type: Optional[str] = Field(default=None, min_length=1, max_length=24)
    target_amount: Optional[Decimal] = Field(default=None, gt=0)
    target_date: Optional[date] = None
    contribution_amount: Optional[Decimal] = Field(default=None, gt=0)
    auto_contribute: Optional[bool] = None
    priority: Optional[int] = Field(default=None, ge=1, le=3)


class GoalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    envelope_id: UUID
    name: str
    goal_type: str
    target_amount: Decimal
    target_date: Optional[date]
    contribution_amount: Decimal
    auto_contribute: bool
    priority: int
    current_balance: Decimal = Field(default=Decimal("0.00"))
    created_at: datetime
    updated_at: Optional[datetime]


class GoalDistributeRequest(BaseModel):
    amount: Optional[Decimal] = Field(default=None, gt=0)
    occurred_on: Optional[date] = None
