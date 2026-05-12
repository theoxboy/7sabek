from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class PageViewIn(BaseModel):
    path: str = Field(..., min_length=1, max_length=255)
    referrer: Optional[str] = None
    source: Optional[str] = None


class TrafficDailyOut(BaseModel):
    date: str
    count: int


class TrafficSummaryOut(BaseModel):
    total: int
    previous_total: int
    daily: List[TrafficDailyOut]
    sources: Dict[str, int]


class FinanceDailyOut(BaseModel):
    date: str
    income: float
    expense: float


class UserGrowthPoint(BaseModel):
    date: str
    count: int


class WeeklyActivePoint(BaseModel):
    week: str
    count: int


class MonthlyFinancePoint(BaseModel):
    month: str
    income: float
    expense: float


class TopItemOut(BaseModel):
    name: str
    total: float


class ChurnBucketOut(BaseModel):
    label: str
    count: int


class OnboardingActivationOut(BaseModel):
    total_users: int
    envelopes: int
    categories: int
    transactions: int


class RolloverUsageOut(BaseModel):
    on: int
    off: int


class PlatformAnalyticsOut(BaseModel):
    user_growth: List[UserGrowthPoint]
    weekly_active: List[WeeklyActivePoint]
    monthly_finance: List[MonthlyFinancePoint]
    top_categories: List[TopItemOut]
    top_envelopes: List[TopItemOut]
    churn: List[ChurnBucketOut]
    onboarding: OnboardingActivationOut
    rollover: RolloverUsageOut
    avg_days_to_first_tx: float
