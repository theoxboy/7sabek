from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DebtCreate(BaseModel):
    contact_name: str = Field(min_length=1, max_length=120)
    contact_phone: Optional[str] = Field(default=None, max_length=50)
    total_amount: Decimal = Field(gt=0)
    paid_amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    is_loan_given: bool = True  # True = كنسال (On me doit), False = كيسالوني (Je dois)
    due_date: Optional[date] = None
    note: Optional[str] = None

    @field_validator("contact_phone", "note", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: Optional[str]) -> Optional[str]:
        if isinstance(v, str) and not v.strip():
            return None
        return v.strip() if isinstance(v, str) else v

    @field_validator("due_date", mode="before")
    @classmethod
    def empty_date_to_none(cls, v: Optional[object]) -> Optional[object]:
        if isinstance(v, str) and not v.strip():
            return None
        return v


class DebtUpdate(BaseModel):
    contact_name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    contact_phone: Optional[str] = Field(default=None, max_length=50)
    total_amount: Optional[Decimal] = Field(default=None, gt=0)
    paid_amount: Optional[Decimal] = Field(default=None, ge=0)
    is_loan_given: Optional[bool] = None
    due_date: Optional[date] = None
    note: Optional[str] = None

    @field_validator("contact_phone", "note", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: Optional[str]) -> Optional[str]:
        if isinstance(v, str) and not v.strip():
            return None
        return v.strip() if isinstance(v, str) else v

    @field_validator("due_date", mode="before")
    @classmethod
    def empty_date_to_none(cls, v: Optional[object]) -> Optional[object]:
        if isinstance(v, str) and not v.strip():
            return None
        return v


class DebtRepaymentCreate(BaseModel):
    amount: Decimal = Field(gt=0)


class DebtOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    contact_name: str
    contact_phone: Optional[str] = None
    total_amount: Decimal
    paid_amount: Decimal = Decimal("0.00")
    is_loan_given: bool = True
    due_date: Optional[date] = None
    note: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    @property
    def remaining_amount(self) -> Decimal:
        return max(Decimal("0.00"), self.total_amount - self.paid_amount)

    @property
    def is_settled(self) -> bool:
        return self.paid_amount >= self.total_amount
