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


class GuestFunnelDailyPoint(BaseModel):
    day: str
    created: int
    claimed: int


class GuestFunnelWallPoint(BaseModel):
    """One conversion "wall" a guest can hit, and how it converts."""
    wall: str
    hits: int              # distinct guests who hit this wall
    dialog_opened: int     # distinct guests who opened the claim dialog from it
    claimed_after: int     # distinct guests who hit this wall and later claimed


class GuestFunnelOut(BaseModel):
    window_days: int
    guests_created: int
    guests_first_tx: int
    guests_claimed: int
    guests_active_now: int
    protection_70: int
    claim_by_passkey: int
    claim_by_email: int
    activation_rate: float
    claim_rate: float
    anchor_recovery_offered: int
    silent_loss_rate: float
    daily: List[GuestFunnelDailyPoint]
    per_wall: List[GuestFunnelWallPoint] = []
    protection_40: int = 0
    protection_100: int = 0
    guests_at_risk: int = 0


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
