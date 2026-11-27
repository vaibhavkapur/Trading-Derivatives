"""Canonical data contracts, units, and output schemas for the Real-Time Greeks Engine."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def ensure_utc(dt: datetime) -> datetime:
    """Normalize timezone-aware datetimes to UTC; reject naive datetimes."""
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError("Timestamps must be timezone-aware (UTC ISO-8601)")
    return dt.astimezone(timezone.utc)


def _check_finite(value: float, name: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite (got {value})")
    return value


class OptionType(str, Enum):
    CALL = "CALL"
    PUT = "PUT"


class ExerciseStyle(str, Enum):
    EUROPEAN = "EUROPEAN"
    AMERICAN = "AMERICAN"


class DayCountConvention(str, Enum):
    ACT_365F = "ACT_365F"
    ACT_360 = "ACT_360"

    @property
    def days_per_year(self) -> float:
        return 365.0 if self is DayCountConvention.ACT_365F else 360.0


class PricingMethod(str, Enum):
    ANALYTIC_BSM = "ANALYTIC_BSM"
    BUMP_AND_REVALUE = "BUMP_AND_REVALUE"


class ReliabilityFlag(str, Enum):
    OK = "OK"
    NEAR_EXPIRY = "NEAR_EXPIRY"
    LOW_VOL = "LOW_VOL"
    EXTREME_MONEYNESS = "EXTREME_MONEYNESS"
    STALE_MARKET_DATA = "STALE_MARKET_DATA"
    FALLBACK_BUMP_AND_REVALUE = "FALLBACK_BUMP_AND_REVALUE"
    FROZEN_SNAPSHOT = "FROZEN_SNAPSHOT"


class AlertSeverity(str, Enum):
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class GreeksUnitSpec(BaseModel):
    """Explicit canonical unit definitions published with every snapshot."""

    model_config = ConfigDict(frozen=True)

    price: str = "currency per 1 unit of underlying"
    pv: str = "currency (price * quantity * contract_multiplier)"
    delta: str = "unitless dV/dS per 1 unit of underlying"
    delta_shares: str = "underlying units (delta * quantity * contract_multiplier)"
    dollar_delta: str = "currency per 100% spot move (delta_shares * spot)"
    gamma: str = "d2V/dS2 per 1 currency unit move in spot"
    gamma_shares: str = "underlying units per 1 currency unit move in spot"
    dollar_gamma_1pct: str = "currency change in dollar_delta for a 1% spot move (gamma_shares * S^2 * 0.01)"
    vega: str = "currency per +1% (+0.01 absolute) implied volatility shift"
    theta: str = "currency per +1 calendar day (decay using instrument day-count basis)"
    rho: str = "currency per +1% (+0.01 absolute) continuous zero-rate shift"
    vanna: str = "dDelta/dVol per +1% (+0.01 absolute) implied volatility shift"
    charm: str = "dDelta/dt per +1 calendar day"
    volga: str = "change in 1%-vega per +1% (+0.01 absolute) implied volatility shift"


class OptionInstrument(BaseModel):
    model_config = ConfigDict(frozen=True)

    instrument_id: str = Field(min_length=1)
    underlying: str = Field(min_length=1)
    option_type: OptionType
    strike: float = Field(gt=0.0)
    expiry_utc: datetime
    currency: str = Field(default="USD", min_length=3, max_length=3)
    contract_multiplier: float = Field(default=100.0, gt=0.0)
    exercise_style: ExerciseStyle = Field(default=ExerciseStyle.EUROPEAN)
    day_count: DayCountConvention = Field(default=DayCountConvention.ACT_365F)

    @field_validator("strike", "contract_multiplier")
    @classmethod
    def _validate_finite(cls, v: float, info: Any) -> float:
        return _check_finite(v, info.field_name)

    @field_validator("expiry_utc")
    @classmethod
    def _validate_expiry_utc(cls, v: datetime) -> datetime:
        return ensure_utc(v)

    @field_validator("underlying", "currency")
    @classmethod
    def _normalize_upper(cls, v: str) -> str:
        cleaned = v.strip().upper()
        if not cleaned:
            raise ValueError("Identifier cannot be blank")
        return cleaned

    @property
    def expiry_date_str(self) -> str:
        return self.expiry_utc.strftime("%Y-%m-%d")

    def fingerprint(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class OptionPosition(BaseModel):
    model_config = ConfigDict(frozen=True)

    position_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    instrument: OptionInstrument
    quantity: float
    version: int = Field(default=1, ge=1)
    updated_at_utc: datetime

    @field_validator("quantity")
    @classmethod
    def _validate_quantity(cls, v: float) -> float:
        return _check_finite(v, "quantity")

    @field_validator("updated_at_utc")
    @classmethod
    def _validate_updated_at(cls, v: datetime) -> datetime:
        return ensure_utc(v)


class SpotTick(BaseModel):
    model_config = ConfigDict(frozen=True)

    tick_id: str = Field(min_length=1)
    underlying: str = Field(min_length=1)
    spot: float = Field(gt=0.0)
    bid: float | None = Field(default=None, gt=0.0)
    ask: float | None = Field(default=None, gt=0.0)
    sequence_no: int = Field(ge=1)
    event_time_utc: datetime
    received_time_utc: datetime
    source: str = Field(default="SYNTHETIC_FEED", min_length=1)

    @field_validator("spot")
    @classmethod
    def _validate_spot(cls, v: float) -> float:
        return _check_finite(v, "spot")

    @field_validator("bid", "ask")
    @classmethod
    def _validate_optional_quotes(cls, v: float | None, info: Any) -> float | None:
        if v is not None:
            _check_finite(v, info.field_name)
        return v

    @field_validator("underlying")
    @classmethod
    def _normalize_underlying(cls, v: str) -> str:
        cleaned = v.strip().upper()
        if not cleaned:
            raise ValueError("underlying cannot be blank")
        return cleaned

    @field_validator("event_time_utc", "received_time_utc")
    @classmethod
    def _validate_timestamps(cls, v: datetime) -> datetime:
        return ensure_utc(v)

    @model_validator(mode="after")
    def _validate_bid_ask(self) -> SpotTick:
        if self.bid is not None and self.ask is not None and self.bid > self.ask:
            raise ValueError(f"bid ({self.bid}) cannot exceed ask ({self.ask})")
        return self


class CurvePoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenor_years: float = Field(gt=0.0)
    zero_rate: float

    @field_validator("tenor_years", "zero_rate")
    @classmethod
    def _validate_finite(cls, v: float, info: Any) -> float:
        return _check_finite(v, info.field_name)


class YieldCurveSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    curve_version: str = Field(min_length=1)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    as_of_utc: datetime
    ttl_seconds: float | None = Field(default=None, gt=0.0)
    points: list[CurvePoint] = Field(min_length=1)
    dividend_yields: dict[str, float] = Field(default_factory=dict)

    @field_validator("currency")
    @classmethod
    def _normalize_currency(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("as_of_utc")
    @classmethod
    def _validate_as_of(cls, v: datetime) -> datetime:
        return ensure_utc(v)

    @field_validator("dividend_yields")
    @classmethod
    def _validate_div_yields(cls, v: dict[str, float]) -> dict[str, float]:
        normalized: dict[str, float] = {}
        for k, rate in v.items():
            normalized[k.strip().upper()] = _check_finite(rate, f"dividend_yield[{k}]")
        return normalized

    @model_validator(mode="after")
    def _validate_strictly_increasing_tenors(self) -> YieldCurveSnapshot:
        tenors = [p.tenor_years for p in self.points]
        if any(tenors[i] >= tenors[i + 1] for i in range(len(tenors) - 1)):
            raise ValueError("Curve points must have strictly increasing tenor_years")
        return self


class VolSurfaceSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    vol_version: str = Field(min_length=1)
    underlying: str = Field(min_length=1)
    as_of_utc: datetime
    ttl_seconds: float | None = Field(default=None, gt=0.0)
    expiries_years: list[float] = Field(min_length=1)
    strikes: list[float] = Field(min_length=1)
    vols: list[list[float]] = Field(min_length=1)

    @field_validator("underlying")
    @classmethod
    def _normalize_underlying(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("as_of_utc")
    @classmethod
    def _validate_as_of(cls, v: datetime) -> datetime:
        return ensure_utc(v)

    @model_validator(mode="after")
    def _validate_grid(self) -> VolSurfaceSnapshot:
        if any(not math.isfinite(e) or e <= 0.0 for e in self.expiries_years):
            raise ValueError("expiries_years must be strictly positive finite numbers")
        if any(self.expiries_years[i] >= self.expiries_years[i + 1] for i in range(len(self.expiries_years) - 1)):
            raise ValueError("expiries_years must be strictly increasing")
        if any(not math.isfinite(k) or k <= 0.0 for k in self.strikes):
            raise ValueError("strikes must be strictly positive finite numbers")
        if any(self.strikes[i] >= self.strikes[i + 1] for i in range(len(self.strikes) - 1)):
            raise ValueError("strikes must be strictly increasing")
        if len(self.vols) != len(self.expiries_years):
            raise ValueError("vols row count must match len(expiries_years)")
        for row_idx, row in enumerate(self.vols):
            if len(row) != len(self.strikes):
                raise ValueError(f"vols row {row_idx} length must match len(strikes)")
            for vol in row:
                if not math.isfinite(vol) or vol <= 0.0 or vol > 5.0:
                    raise ValueError(f"implied vol must be finite in (0, 5.0] (got {vol})")
        return self


class UnitGreeks(BaseModel):
    """Per-unit-of-underlying option price and sensitivities under canonical GreeksUnitSpec."""

    model_config = ConfigDict(frozen=True)

    price: float
    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float
    vanna: float = 0.0
    charm: float = 0.0
    volga: float = 0.0


class PositionGreeks(BaseModel):
    """Position-scaled sensitivities (incorporating signed quantity and contract multiplier)."""

    model_config = ConfigDict(frozen=True)

    pv: float
    delta_shares: float
    dollar_delta: float
    gamma_shares: float
    dollar_gamma_1pct: float
    vega: float
    theta: float
    rho: float
    vanna: float = 0.0
    charm: float = 0.0
    volga: float = 0.0


class PositionGreeksResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    position_id: str
    account_id: str
    instrument_id: str
    underlying: str
    expiry_date: str
    currency: str
    quantity: float
    contract_multiplier: float
    spot: float
    rate: float
    dividend_yield: float
    implied_vol: float
    time_to_expiry_years: float
    pricing_method: PricingMethod
    unit_greeks: UnitGreeks
    position_greeks: PositionGreeks
    flags: list[ReliabilityFlag] = Field(default_factory=list)
    market_version_key: str
    computed_at_utc: datetime
    is_frozen: bool = False


class AggregatedGreeksBucket(BaseModel):
    model_config = ConfigDict(frozen=True)

    bucket_key: str
    underlying: str
    expiry_date: str | None = None
    currency: str
    position_count: int
    pv: float
    delta_shares: float
    dollar_delta: float
    gamma_shares: float
    dollar_gamma_1pct: float
    vega: float
    theta: float
    rho: float
    vanna: float = 0.0
    charm: float = 0.0
    volga: float = 0.0
    unreliable_count: int = 0
    frozen_count: int = 0
    flags: list[ReliabilityFlag] = Field(default_factory=list)


class StaleDataAlert(BaseModel):
    model_config = ConfigDict(frozen=True)

    alert_id: str
    severity: AlertSeverity
    code: str
    resource: str
    age_seconds: float | None = None
    max_age_seconds: float | None = None
    message: str
    detected_at_utc: datetime


class PortfolioTotals(BaseModel):
    model_config = ConfigDict(frozen=True)

    position_count: int
    pv: float
    dollar_delta: float
    dollar_gamma_1pct: float
    vega: float
    theta: float
    rho: float
    unreliable_count: int = 0
    frozen_count: int = 0


class PortfolioSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    snapshot_id: str
    snapshot_seq: int
    valuation_time_utc: datetime
    engine_config_hash: str
    unit_spec: GreeksUnitSpec = Field(default_factory=GreeksUnitSpec)
    market_versions: dict[str, Any]
    totals: PortfolioTotals
    by_underlying: dict[str, AggregatedGreeksBucket]
    by_underlying_expiry: dict[str, AggregatedGreeksBucket]
    positions: dict[str, PositionGreeksResult]
    alerts: list[StaleDataAlert]
    has_frozen_positions: bool = False


class BucketDiff(BaseModel):
    model_config = ConfigDict(frozen=True)

    bucket_key: str
    max_abs_diff: float
    max_rel_diff: float
    field_diffs: dict[str, float]


class ReconciliationReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    reconciliation_id: str
    snapshot_seq: int
    valuation_time_utc: datetime
    passed: bool
    abs_tolerance: float
    rel_tolerance: float
    max_abs_diff: float
    max_rel_diff: float
    total_buckets_checked: int
    mismatched_buckets: list[BucketDiff]


class ScenarioShock(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario_id: str = Field(min_length=1)
    spot_shock_pct: float = Field(default=0.0, gt=-0.99, lt=5.0)
    per_underlying_spot_shock_pct: dict[str, float] = Field(default_factory=dict)
    vol_shift_abs: float = Field(default=0.0, gt=-0.99, lt=2.0)
    rate_shift_bps: float = Field(default=0.0, gt=-10000.0, lt=10000.0)
    time_step_days: float = Field(default=0.0, ge=0.0, le=3650.0)


class ScenarioResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario_id: str
    shock: ScenarioShock
    base_pv: float
    scenario_pv: float
    pnl: float
    totals: PortfolioTotals
    by_underlying: dict[str, AggregatedGreeksBucket]
    elapsed_ms: float
