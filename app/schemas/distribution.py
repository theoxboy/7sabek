from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class DistributionConfigItemIn(BaseModel):
    target_id: UUID
    mode: str = Field(pattern="^(none|fixed|percent)$")
    fixed_amount: Optional[Decimal] = Field(default=None, gt=0)
    fixed_priority: Optional[int] = Field(default=None, ge=0, le=10_000)
    percent: Optional[Decimal] = Field(default=None, gt=0)
    enabled: bool = True


class DistributionConfigItemOut(DistributionConfigItemIn):
    name: str


class DistributionConfigOut(BaseModel):
    auto_enabled: bool
    envelopes: List[DistributionConfigItemOut]
    goals: List[DistributionConfigItemOut]


class DistributionConfigIn(BaseModel):
    auto_enabled: bool
    envelopes: List[DistributionConfigItemIn]
    goals: List[DistributionConfigItemIn]


class DistributionSimulateRequest(BaseModel):
    income_amount: Optional[Decimal] = Field(default=None, gt=0)
    use_cash_available: bool = True
    occurred_on: Optional[date] = None


class DistributionSimulateItemOut(BaseModel):
    target_type: str
    target_id: UUID
    name: str
    mode: str
    amount: Decimal
    fixed_priority: Optional[int] = None


class DistributionSimulateOut(BaseModel):
    period_start: date
    period_end: date
    cash_before: Decimal
    cash_after: Decimal
    remaining_after_fixed: Decimal
    remaining_after_percent: Decimal
    items: List[DistributionSimulateItemOut]
    warnings: List[str]


class DistributionApplyRequest(BaseModel):
    income_amount: Optional[Decimal] = Field(default=None, gt=0)
    use_cash_available: bool = True
    occurred_on: Optional[date] = None


class DistributionApplyOut(BaseModel):
    run_id: UUID
    cash_before: Decimal
    cash_after: Decimal
    total_distributed: Decimal
    warnings: List[str]


class DistributionSavedRowIn(BaseModel):
    target_type: Literal["envelope", "goal"]
    target_id: UUID
    mode: Literal["none", "fixed", "percent"]
    enabled: bool = True
    fixed_amount: Optional[Decimal] = Field(default=None, gt=0)
    percent: Optional[Decimal] = Field(default=None, gt=0)
    rank: int = Field(default=1, ge=1, le=10_000)


class DistributionSavedRowOut(DistributionSavedRowIn):
    name: Optional[str] = None


class DistributionSavedConfigUpsertIn(BaseModel):
    id: Optional[UUID] = None
    name: str = Field(min_length=1, max_length=120)
    auto_enabled: bool = False
    percent_mode: Literal["equal", "ranked"] = "equal"
    rows: List[DistributionSavedRowIn] = Field(default_factory=list)
    scope_hash: Optional[str] = Field(default=None, max_length=120)


class DistributionSavedConfigOut(BaseModel):
    id: UUID
    name: str
    auto_enabled: bool
    percent_mode: Literal["equal", "ranked"]
    rows: List[DistributionSavedRowOut]
    scope_hash: Optional[str] = None
    signature: str
    source: Literal["onboarding_initial", "post_onboarding_adjustment"] = "post_onboarding_adjustment"
    version: int = 1
    effective_from_period_start: Optional[date] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class DistributionActiveConfigIn(BaseModel):
    config_id: UUID


class DistributionRebalancePreviewIn(BaseModel):
    cut1_pct: Decimal = Field(ge=0, le=100)
    cut2_pct: Decimal = Field(ge=0, le=100)


class DistributionRebalancePreviewOut(BaseModel):
    debt_amount: Decimal
    goals_amount: Decimal
    morona_amount: Decimal
    delta_vs_active: dict[str, Decimal]


class DistributionApplyNextCycleIn(DistributionRebalancePreviewIn):
    effective_from_period_start: date


class DistributionRevertBaselineIn(BaseModel):
    effective_from_period_start: date


class DistributionOnboardingStatusIn(BaseModel):
    eligible_envelope_names: List[str] = Field(default_factory=list)
    eligible_envelope_ids: List[UUID] = Field(default_factory=list)
    eligible_envelope_keys: List[str] = Field(default_factory=list)
    scope_hash: Optional[str] = Field(default=None, max_length=120)


class DistributionOnboardingStatusOut(BaseModel):
    setup_status: Literal[
        "not_started",
        "draft_opened",
        "saved_valid",
        "applied",
        "invalidated",
        "legacy_rules_detected",
    ]
    eligible_total: int = 0
    eligible_envelope_names: List[str] = Field(default_factory=list)
    covered_total: int = 0
    unresolved_total: int = 0
    unresolved_envelope_names: List[str] = Field(default_factory=list)
    missing_envelope_names: List[str] = Field(default_factory=list)
    source: Literal["active_config", "legacy_rules", "none"] = "none"
    active_config: Optional[DistributionSavedConfigOut] = None
    scoped_target_ids: List[UUID] = Field(default_factory=list)
    scoped_target_keys: List[str] = Field(default_factory=list)
    scoped_target_names: List[str] = Field(default_factory=list)
    ignored_non_target_names: List[str] = Field(default_factory=list)
    missing_current_target_names: List[str] = Field(default_factory=list)
    unresolved_current_target_names: List[str] = Field(default_factory=list)
    message: str = ""
