"""Pydantic v2 API data contracts for request validation and response serialization."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class FixedLegPayload(BaseModel):
    fixed_rate: float
    frequency: str
    day_count: str
    business_day_convention: str = "MODIFIED_FOLLOWING"
    calendar: str = "USD_NY"
    stub_type: str = "FRONT_SHORT"
    end_of_month: bool = False
    payment_lag_days: int = 0


class FloatingLegPayload(BaseModel):
    index_name: str
    frequency: str
    day_count: str
    spread: float = 0.0
    business_day_convention: str = "MODIFIED_FOLLOWING"
    calendar: str = "USD_NY"
    stub_type: str = "FRONT_SHORT"
    end_of_month: bool = False
    fixing_lag_days: int = 2
    payment_lag_days: int = 0


class SwapTradePayload(BaseModel):
    trade_id: str
    currency: str
    notional: float
    direction: str
    effective_date: date
    maturity_date: date
    fixed_leg: FixedLegPayload
    floating_leg: FloatingLegPayload
    schema_version: str = "1.0.0"


class CurveNodePayload(BaseModel):
    tenor_years: float
    zero_rate: float
    label: str = ""


class CurveSnapshotPayload(BaseModel):
    curve_id: str
    role: str
    currency: str
    valuation_date: date
    nodes: list[CurveNodePayload]
    day_count: str = "ACT/365F"
    compounding: str = "CONTINUOUS"
    interpolation: str = "LINEAR_ZERO"
    index_name: str | None = None
    schema_version: str = "1.0.0"


class FixingEntryPayload(BaseModel):
    index_name: str
    fixing_date: date
    rate: float


class FixingHistoryPayload(BaseModel):
    entries: list[FixingEntryPayload] = Field(default_factory=list)
    schema_version: str = "1.0.0"


class ValuationConfigPayload(BaseModel):
    default_bump_bps: float = 1.0
    finite_difference_scheme: str = "CENTRAL"
    missing_fixing_policy: str = "ERROR"
    valuation_date_fixing_policy: str = "ALLOW_PROJECT"


class ScheduleRequest(BaseModel):
    trade: SwapTradePayload


class ValuationRequest(BaseModel):
    trade: SwapTradePayload
    discount_curve: CurveSnapshotPayload
    forward_curve: CurveSnapshotPayload
    fixing_history: FixingHistoryPayload | None = None
    config: ValuationConfigPayload | None = None


class SensitivityRequest(ValuationRequest):
    bump_bps: float | None = None
    scheme: str | None = None


class StochasticParamsPayload(BaseModel):
    seed: int = 42
    num_paths: int = 250
    level_vol_bps: float = 65.0
    slope_vol_bps: float = 30.0
    curvature_vol_bps: float = 15.0
    basis_vol_bps: float = 8.0


class ScenariosRequest(ValuationRequest):
    deterministic_scenarios: list[dict[str, Any]] = Field(default_factory=list)
    stochastic: StochasticParamsPayload | None = None


class ErrorEnvelope(BaseModel):
    error_code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
