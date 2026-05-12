from __future__ import annotations

from datetime import date
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.allocation import PeriodBalanceOut
from app.schemas.envelope import EnvelopeOut
from app.schemas.transaction import TransactionOut


class DashboardUserOut(BaseModel):
    id: UUID
    email: str
    currency: str
    sweep_interval_days: int


class CurrentPeriodOut(BaseModel):
    start: date
    end: date


class EnvelopeBalanceOut(BaseModel):
    envelope: EnvelopeOut
    period_id: Optional[UUID]
    balance: PeriodBalanceOut


class SpendingByCategoryOut(BaseModel):
    category_id: UUID
    category_name: str
    total: str


class SpendingByEnvelopeOut(BaseModel):
    envelope_id: UUID
    envelope_name: str
    total: str


class DashboardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user: DashboardUserOut
    current_period: CurrentPeriodOut
    sweep_status: Optional["SweepStatusOut"] = None
    sweep_bootstrap: Optional["SweepBootstrapOut"] = None
    net_worth: str
    cash_balance: str
    available_to_allocate: str
    period_income: str
    period_expenses_mapped: str
    period_net: str
    envelopes: list[EnvelopeBalanceOut]
    recent_transactions: list[TransactionOut]
    spending_by_category: list[SpendingByCategoryOut]
    spending_by_envelope: list[SpendingByEnvelopeOut]


class DashboardSummaryOut(BaseModel):
    period_start: date
    period_end: date
    income: str
    expenses_mapped: str
    net: str
    cash_balance: str


class SweepStatusOut(BaseModel):
    due: bool
    period_start: date
    period_end: date
    income_declared: bool
    already_swept: bool


class SweepBootstrapOut(BaseModel):
    needs_first_income_declaration: bool
    last_income_date: Optional[date] = None
    last_income_amount: Optional[str] = None
    expected_income_amount: Optional[str] = None
    cadence: Optional[str] = None
    interval_days: Optional[int] = None


class DashboardAlertOut(BaseModel):
    unmapped_categories: int
    overspent_envelopes: list[str]
    sweep_due: bool
    current_period: Optional[CurrentPeriodOut] = None
    sweep_status: Optional["SweepStatusOut"] = None


class DashboardTrendPointOut(BaseModel):
    period_start: date
    period_end: date
    net_worth: str


class CashBalanceBreakdownOut(BaseModel):
    period_id: Optional[UUID]
    opening_balance: str
    total_allocations: str
    total_movements: str
    sweeps_out: str
    sweeps_in: str
    closing_balance: str


class DashboardDiagnosticsOut(BaseModel):
    current_period: CurrentPeriodOut
    cash_balance: str
    cash_negative: bool
    cash_breakdown: CashBalanceBreakdownOut
