from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class ReportRange(BaseModel):
    start: date
    end: date


class ReportSpendingByEnvelopeOut(BaseModel):
    envelope_id: UUID
    envelope_name: str
    total: Decimal


class ReportSpendingByCategoryOut(BaseModel):
    category_id: UUID
    category_name: str
    total: Decimal


class ReportIncomeExpenseOut(BaseModel):
    income: Decimal
    expense: Decimal
    net: Decimal


class ReportTopLabelOut(BaseModel):
    label: str
    total: Decimal


class ReportSummaryOut(BaseModel):
    range: ReportRange
    income: Decimal
    expense: Decimal
    net: Decimal
    transactions_count: int
    top_label: Optional[str] = None
