"""Typed domain models and data contracts for Fixed Income Analytics Engine."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .conventions import (
    BusinessDayConvention,
    Compounding,
    Currency,
    DayCountConvention,
    InterpolationMethod,
    StubType,
    parse_enum,
)
from .errors import ValidationError

SCHEMA_VERSION = "1.0.0"
VALID_FREQUENCIES = (0, 1, 2, 4, 12)


def stable_id(prefix: str, payload: Dict[str, Any]) -> str:
    """Generate a deterministic, content-addressed identifier."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _parse_date(raw: Any, field_name: str) -> date:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise ValidationError(
                f"Field '{field_name}' must be an ISO-8601 date (YYYY-MM-DD), got '{raw}'",
                details={"field": field_name, "value": raw},
            ) from exc
    raise ValidationError(
        f"Field '{field_name}' must be a date or ISO string",
        details={"field": field_name, "value": repr(raw)},
    )


def _validate_finite(value: float, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(
            f"Field '{field_name}' must be numeric",
            details={"field": field_name, "value": repr(value)},
        )
    fval = float(value)
    if not math.isfinite(fval):
        raise ValidationError(
            f"Field '{field_name}' must be finite, got {value}",
            details={"field": field_name, "value": str(value)},
        )
    return fval


@dataclass(frozen=True)
class BondSpec:
    """Canonical specification of a fixed-rate or zero-coupon bond.

    Units:
    - notional: currency units (e.g. 100.0 or 1_000_000.0)
    - coupon_rate: annual decimal rate (e.g. 0.05 for 5.00% p.a.)
    - frequency: coupon payments per year (0 for zero-coupon, or 1, 2, 4, 12)
    """

    bond_id: str
    currency: Currency
    notional: float
    coupon_rate: float
    frequency: int
    issue_date: date
    maturity_date: date
    day_count: DayCountConvention = DayCountConvention.ACT_ACT_ICMA
    business_day_convention: BusinessDayConvention = BusinessDayConvention.MODIFIED_FOLLOWING
    calendar_name: str = "US_SYNTHETIC"
    yield_compounding: Optional[Compounding] = None
    first_coupon_date: Optional[date] = None
    penultimate_coupon_date: Optional[date] = None
    stub_type: StubType = StubType.SHORT_FIRST
    end_of_month: bool = False
    adjust_accrual_dates: bool = False

    def __post_init__(self) -> None:
        if not self.bond_id or not isinstance(self.bond_id, str):
            raise ValidationError("bond_id must be a non-empty string")
        _validate_finite(self.notional, "notional")
        _validate_finite(self.coupon_rate, "coupon_rate")
        if self.notional <= 0.0:
            raise ValidationError(
                "notional must be strictly positive",
                details={"bond_id": self.bond_id, "notional": self.notional},
            )
        if self.coupon_rate < 0.0 or self.coupon_rate > 1.0:
            raise ValidationError(
                "coupon_rate must be a decimal in [0.0, 1.0]",
                details={"bond_id": self.bond_id, "coupon_rate": self.coupon_rate},
            )
        if self.frequency not in VALID_FREQUENCIES:
            raise ValidationError(
                f"frequency must be one of {VALID_FREQUENCIES}, got {self.frequency}",
                details={"bond_id": self.bond_id, "frequency": self.frequency},
            )
        if self.frequency == 0 and self.coupon_rate != 0.0:
            raise ValidationError(
                "Zero-coupon bond (frequency=0) must have coupon_rate=0.0",
                details={"bond_id": self.bond_id, "coupon_rate": self.coupon_rate},
            )
        if self.maturity_date <= self.issue_date:
            raise ValidationError(
                "maturity_date must be strictly after issue_date",
                details={
                    "bond_id": self.bond_id,
                    "issue_date": self.issue_date.isoformat(),
                    "maturity_date": self.maturity_date.isoformat(),
                },
            )
        if self.first_coupon_date is not None:
            if not (self.issue_date < self.first_coupon_date <= self.maturity_date):
                raise ValidationError(
                    "first_coupon_date must be in (issue_date, maturity_date]",
                    details={
                        "bond_id": self.bond_id,
                        "first_coupon_date": self.first_coupon_date.isoformat(),
                    },
                )
        if self.penultimate_coupon_date is not None:
            if not (self.issue_date <= self.penultimate_coupon_date < self.maturity_date):
                raise ValidationError(
                    "penultimate_coupon_date must be in [issue_date, maturity_date)",
                    details={
                        "bond_id": self.bond_id,
                        "penultimate_coupon_date": self.penultimate_coupon_date.isoformat(),
                    },
                )
        if (
            self.first_coupon_date is not None
            and self.penultimate_coupon_date is not None
            and self.first_coupon_date > self.penultimate_coupon_date
        ):
            raise ValidationError(
                "first_coupon_date cannot be after penultimate_coupon_date",
                details={"bond_id": self.bond_id},
            )

    @property
    def is_zero_coupon(self) -> bool:
        return self.frequency == 0 or self.coupon_rate == 0.0

    @property
    def effective_yield_compounding(self) -> Compounding:
        if self.yield_compounding is not None:
            return self.yield_compounding
        if self.frequency == 1:
            return Compounding.ANNUAL
        if self.frequency == 2 or self.frequency == 0:
            return Compounding.SEMI_ANNUAL
        if self.frequency == 4:
            return Compounding.QUARTERLY
        return Compounding.MONTHLY

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bond_id": self.bond_id,
            "currency": self.currency.value,
            "notional": self.notional,
            "coupon_rate": self.coupon_rate,
            "frequency": self.frequency,
            "issue_date": self.issue_date.isoformat(),
            "maturity_date": self.maturity_date.isoformat(),
            "day_count": self.day_count.value,
            "business_day_convention": self.business_day_convention.value,
            "calendar_name": self.calendar_name,
            "yield_compounding": self.effective_yield_compounding.value,
            "first_coupon_date": self.first_coupon_date.isoformat() if self.first_coupon_date else None,
            "penultimate_coupon_date": (
                self.penultimate_coupon_date.isoformat() if self.penultimate_coupon_date else None
            ),
            "stub_type": self.stub_type.value,
            "end_of_month": self.end_of_month,
            "adjust_accrual_dates": self.adjust_accrual_dates,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BondSpec":
        required = [
            "bond_id",
            "currency",
            "notional",
            "coupon_rate",
            "frequency",
            "issue_date",
            "maturity_date",
        ]
        missing = [k for k in required if k not in data]
        if missing:
            raise ValidationError(
                f"Missing required BondSpec fields: {missing}",
                details={"missing_fields": missing},
            )
        return cls(
            bond_id=str(data["bond_id"]),
            currency=parse_enum(Currency, data["currency"], "currency"),
            notional=_validate_finite(data["notional"], "notional"),
            coupon_rate=_validate_finite(data["coupon_rate"], "coupon_rate"),
            frequency=int(data["frequency"]),
            issue_date=_parse_date(data["issue_date"], "issue_date"),
            maturity_date=_parse_date(data["maturity_date"], "maturity_date"),
            day_count=parse_enum(
                DayCountConvention, data.get("day_count", "ACT/ACT_ICMA"), "day_count"
            ),
            business_day_convention=parse_enum(
                BusinessDayConvention,
                data.get("business_day_convention", "MODIFIED_FOLLOWING"),
                "business_day_convention",
            ),
            calendar_name=str(data.get("calendar_name", "US_SYNTHETIC")),
            yield_compounding=(
                parse_enum(Compounding, data["yield_compounding"], "yield_compounding")
                if data.get("yield_compounding") is not None
                else None
            ),
            first_coupon_date=(
                _parse_date(data["first_coupon_date"], "first_coupon_date")
                if data.get("first_coupon_date") is not None
                else None
            ),
            penultimate_coupon_date=(
                _parse_date(data["penultimate_coupon_date"], "penultimate_coupon_date")
                if data.get("penultimate_coupon_date") is not None
                else None
            ),
            stub_type=parse_enum(StubType, data.get("stub_type", "SHORT_FIRST"), "stub_type"),
            end_of_month=bool(data.get("end_of_month", False)),
            adjust_accrual_dates=bool(data.get("adjust_accrual_dates", False)),
        )


@dataclass(frozen=True)
class CashFlow:
    """Single scheduled coupon and/or principal cash flow."""

    index: int
    accrual_start: date
    accrual_end: date
    payment_date: date
    accrual_factor: float
    coupon_amount: float
    principal_amount: float
    is_stub: bool = False
    stub_kind: str = "REGULAR"

    @property
    def total_amount(self) -> float:
        return self.coupon_amount + self.principal_amount

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "accrual_start": self.accrual_start.isoformat(),
            "accrual_end": self.accrual_end.isoformat(),
            "payment_date": self.payment_date.isoformat(),
            "accrual_factor": self.accrual_factor,
            "coupon_amount": self.coupon_amount,
            "principal_amount": self.principal_amount,
            "total_amount": self.total_amount,
            "is_stub": self.is_stub,
            "stub_kind": self.stub_kind,
        }


@dataclass(frozen=True)
class AccruedInterestResult:
    """Auditable accrued interest calculation at a valuation date."""

    valuation_date: date
    accrual_start: Optional[date]
    accrual_end: Optional[date]
    accrued_days: int
    period_days: int
    accrued_fraction: float
    accrued_interest: float
    accrued_pct_of_par: float
    is_on_coupon_date: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valuation_date": self.valuation_date.isoformat(),
            "accrual_start": self.accrual_start.isoformat() if self.accrual_start else None,
            "accrual_end": self.accrual_end.isoformat() if self.accrual_end else None,
            "accrued_days": self.accrued_days,
            "period_days": self.period_days,
            "accrued_fraction": self.accrued_fraction,
            "accrued_interest": self.accrued_interest,
            "accrued_pct_of_par": self.accrued_pct_of_par,
            "is_on_coupon_date": self.is_on_coupon_date,
        }


@dataclass(frozen=True)
class CashFlowSchedule:
    """Full bond cash-flow schedule with reconciliation totals."""

    bond_id: str
    currency: Currency
    cash_flows: Tuple[CashFlow, ...]
    total_coupons: float
    total_principal: float
    total_cash_flows: float
    schedule_id: str

    def future_cash_flows(self, valuation_date: date) -> Tuple[CashFlow, ...]:
        """Return cash flows with payment_date strictly after valuation_date."""
        return tuple(cf for cf in self.cash_flows if cf.payment_date > valuation_date)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schedule_id": self.schedule_id,
            "bond_id": self.bond_id,
            "currency": self.currency.value,
            "num_cash_flows": len(self.cash_flows),
            "total_coupons": self.total_coupons,
            "total_principal": self.total_principal,
            "total_cash_flows": self.total_cash_flows,
            "cash_flows": [cf.to_dict() for cf in self.cash_flows],
        }


@dataclass(frozen=True)
class DepositQuote:
    """Money-market deposit quote for short-end curve bootstrapping."""

    instrument_id: str
    currency: Currency
    start_date: date
    maturity_date: date
    rate: float  # Simple annual decimal rate
    day_count: DayCountConvention = DayCountConvention.ACT_360
    business_day_convention: BusinessDayConvention = BusinessDayConvention.MODIFIED_FOLLOWING
    calendar_name: str = "US_SYNTHETIC"

    def __post_init__(self) -> None:
        if not self.instrument_id:
            raise ValidationError("DepositQuote.instrument_id must be non-empty")
        _validate_finite(self.rate, "rate")
        if self.rate <= -0.10 or self.rate > 1.0:
            raise ValidationError(
                "Deposit rate out of valid range (-0.10, 1.0]",
                details={"instrument_id": self.instrument_id, "rate": self.rate},
            )
        if self.maturity_date <= self.start_date:
            raise ValidationError(
                "Deposit maturity_date must be strictly after start_date",
                details={
                    "instrument_id": self.instrument_id,
                    "start_date": self.start_date.isoformat(),
                    "maturity_date": self.maturity_date.isoformat(),
                },
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DepositQuote":
        required = ["instrument_id", "currency", "start_date", "maturity_date", "rate"]
        missing = [k for k in required if k not in data]
        if missing:
            raise ValidationError(
                f"Missing required DepositQuote fields: {missing}",
                details={"missing_fields": missing},
            )
        return cls(
            instrument_id=str(data["instrument_id"]),
            currency=parse_enum(Currency, data["currency"], "currency"),
            start_date=_parse_date(data["start_date"], "start_date"),
            maturity_date=_parse_date(data["maturity_date"], "maturity_date"),
            rate=_validate_finite(data["rate"], "rate"),
            day_count=parse_enum(DayCountConvention, data.get("day_count", "ACT/360"), "day_count"),
            business_day_convention=parse_enum(
                BusinessDayConvention,
                data.get("business_day_convention", "MODIFIED_FOLLOWING"),
                "business_day_convention",
            ),
            calendar_name=str(data.get("calendar_name", "US_SYNTHETIC")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "currency": self.currency.value,
            "start_date": self.start_date.isoformat(),
            "maturity_date": self.maturity_date.isoformat(),
            "rate": self.rate,
            "day_count": self.day_count.value,
            "business_day_convention": self.business_day_convention.value,
            "calendar_name": self.calendar_name,
        }


@dataclass(frozen=True)
class BondQuote:
    """Calibration bond market quote (clean price, dirty price, or YTM)."""

    bond: BondSpec
    quote_type: str  # "CLEAN_PRICE", "DIRTY_PRICE", or "YTM"
    quote_value: float

    def __post_init__(self) -> None:
        _validate_finite(self.quote_value, "quote_value")
        if self.quote_type not in ("CLEAN_PRICE", "DIRTY_PRICE", "YTM"):
            raise ValidationError(
                f"Unsupported quote_type '{self.quote_type}'",
                details={
                    "bond_id": self.bond.bond_id,
                    "quote_type": self.quote_type,
                    "supported": ["CLEAN_PRICE", "DIRTY_PRICE", "YTM"],
                },
            )
        if self.quote_type in ("CLEAN_PRICE", "DIRTY_PRICE") and self.quote_value <= 0.0:
            raise ValidationError(
                "Bond price quote must be strictly positive",
                details={"bond_id": self.bond.bond_id, "quote_value": self.quote_value},
            )
        if self.quote_type == "YTM" and (self.quote_value <= -0.10 or self.quote_value > 1.0):
            raise ValidationError(
                "Bond YTM quote out of valid range (-0.10, 1.0]",
                details={"bond_id": self.bond.bond_id, "quote_value": self.quote_value},
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BondQuote":
        if "bond" not in data or "quote_type" not in data or "quote_value" not in data:
            raise ValidationError("BondQuote requires 'bond', 'quote_type', and 'quote_value'")
        return cls(
            bond=BondSpec.from_dict(data["bond"]),
            quote_type=str(data["quote_type"]),
            quote_value=_validate_finite(data["quote_value"], "quote_value"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bond": self.bond.to_dict(),
            "quote_type": self.quote_type,
            "quote_value": self.quote_value,
        }


@dataclass(frozen=True)
class CurveNode:
    """Single calibrated discount factor and zero-rate node on a yield curve."""

    instrument_id: str
    instrument_type: str  # "ANCHOR", "DEPOSIT", "BOND", "MANUAL"
    maturity_date: date
    time_years: float
    discount_factor: float
    zero_rate_continuous: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "instrument_type": self.instrument_type,
            "maturity_date": self.maturity_date.isoformat(),
            "time_years": self.time_years,
            "discount_factor": self.discount_factor,
            "zero_rate_continuous": self.zero_rate_continuous,
        }


@dataclass(frozen=True)
class CalibrationResidual:
    """Repricing audit record for a single calibration instrument."""

    instrument_id: str
    instrument_type: str
    maturity_date: date
    target_value: float
    repriced_value: float
    abs_error: float
    error_bp: float
    convention: str
    iterations: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "instrument_type": self.instrument_type,
            "maturity_date": self.maturity_date.isoformat(),
            "target_value": self.target_value,
            "repriced_value": self.repriced_value,
            "abs_error": self.abs_error,
            "error_bp": self.error_bp,
            "convention": self.convention,
            "iterations": self.iterations,
        }


@dataclass(frozen=True)
class CashFlowValuation:
    """Valuation breakdown for a single future cash flow."""

    index: int
    payment_date: date
    time_years: float
    coupon_amount: float
    principal_amount: float
    total_amount: float
    discount_factor: float
    present_value: float
    pv_weight: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "payment_date": self.payment_date.isoformat(),
            "time_years": self.time_years,
            "coupon_amount": self.coupon_amount,
            "principal_amount": self.principal_amount,
            "total_amount": self.total_amount,
            "discount_factor": self.discount_factor,
            "present_value": self.present_value,
            "pv_weight": self.pv_weight,
        }


@dataclass(frozen=True)
class PricingResult:
    """Full bond pricing and risk sensitivity output contract."""

    pricing_id: str
    schema_version: str
    bond_id: str
    currency: Currency
    valuation_date: date
    notional: float
    dirty_price: float
    clean_price: float
    accrued_interest: float
    dirty_price_pct_par: float
    clean_price_pct_par: float
    ytm: float
    yield_compounding: Compounding
    macaulay_duration: float
    modified_duration: float
    convexity: float
    analytic_dv01: float
    numerical_yield_dv01: float
    dv01_relative_diff_pct: float
    curve_parallel_dv01: Optional[float]
    z_spread_bp: Optional[float]
    key_rate_dv01: Dict[str, float]
    cash_flow_valuations: Tuple[CashFlowValuation, ...]
    accrued_detail: AccruedInterestResult
    solver_iterations: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pricing_id": self.pricing_id,
            "schema_version": self.schema_version,
            "bond_id": self.bond_id,
            "currency": self.currency.value,
            "valuation_date": self.valuation_date.isoformat(),
            "notional": self.notional,
            "dirty_price": self.dirty_price,
            "clean_price": self.clean_price,
            "accrued_interest": self.accrued_interest,
            "dirty_price_pct_par": self.dirty_price_pct_par,
            "clean_price_pct_par": self.clean_price_pct_par,
            "ytm": self.ytm,
            "yield_compounding": self.yield_compounding.value,
            "macaulay_duration": self.macaulay_duration,
            "modified_duration": self.modified_duration,
            "convexity": self.convexity,
            "analytic_dv01": self.analytic_dv01,
            "numerical_yield_dv01": self.numerical_yield_dv01,
            "dv01_relative_diff_pct": self.dv01_relative_diff_pct,
            "curve_parallel_dv01": self.curve_parallel_dv01,
            "z_spread_bp": self.z_spread_bp,
            "key_rate_dv01": dict(self.key_rate_dv01),
            "cash_flow_valuations": [cfv.to_dict() for cfv in self.cash_flow_valuations],
            "accrued_detail": self.accrued_detail.to_dict(),
            "solver_iterations": self.solver_iterations,
        }


@dataclass(frozen=True)
class ScenarioSpec:
    """Definition of a yield-curve shift scenario."""

    scenario_id: str
    name: str
    shift_type: str  # "PARALLEL", "STEEPENER", "FLATTENER", "BUTTERFLY", "NODE_SHIFTS"
    magnitude_bp: float = 0.0
    pivot_years: float = 5.0
    node_shifts_bp: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "name": self.name,
            "shift_type": self.shift_type,
            "magnitude_bp": self.magnitude_bp,
            "pivot_years": self.pivot_years,
            "node_shifts_bp": dict(self.node_shifts_bp),
        }


@dataclass(frozen=True)
class CashFlowContribution:
    """Cash-flow-level P&L attribution under a curve scenario."""

    index: int
    payment_date: date
    time_years: float
    total_amount: float
    base_pv: float
    shocked_pv: float
    pv_change: float
    pnl_share_pct: float
    zero_rate_shift_bp: float
    first_order_approx: float
    second_order_approx: float
    higher_order_residual: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "payment_date": self.payment_date.isoformat(),
            "time_years": self.time_years,
            "total_amount": self.total_amount,
            "base_pv": self.base_pv,
            "shocked_pv": self.shocked_pv,
            "pv_change": self.pv_change,
            "pnl_share_pct": self.pnl_share_pct,
            "zero_rate_shift_bp": self.zero_rate_shift_bp,
            "first_order_approx": self.first_order_approx,
            "second_order_approx": self.second_order_approx,
            "higher_order_residual": self.higher_order_residual,
        }


@dataclass(frozen=True)
class ScenarioResult:
    """Full scenario repricing and cash-flow attribution report for one bond."""

    result_id: str
    scenario: ScenarioSpec
    bond_id: str
    currency: Currency
    base_dirty_price: float
    shocked_dirty_price: float
    total_pnl: float
    total_pnl_pct: float
    base_ytm: float
    shocked_ytm: float
    ytm_change_bp: float
    sum_cf_pnl: float
    reconciliation_error: float
    taylor_duration_pnl: float
    taylor_convexity_pnl: float
    taylor_residual: float
    contributions: Tuple[CashFlowContribution, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "result_id": self.result_id,
            "scenario": self.scenario.to_dict(),
            "bond_id": self.bond_id,
            "currency": self.currency.value,
            "base_dirty_price": self.base_dirty_price,
            "shocked_dirty_price": self.shocked_dirty_price,
            "total_pnl": self.total_pnl,
            "total_pnl_pct": self.total_pnl_pct,
            "base_ytm": self.base_ytm,
            "shocked_ytm": self.shocked_ytm,
            "ytm_change_bp": self.ytm_change_bp,
            "sum_cf_pnl": self.sum_cf_pnl,
            "reconciliation_error": self.reconciliation_error,
            "taylor_duration_pnl": self.taylor_duration_pnl,
            "taylor_convexity_pnl": self.taylor_convexity_pnl,
            "taylor_residual": self.taylor_residual,
            "contributions": [c.to_dict() for c in self.contributions],
        }


@dataclass(frozen=True)
class ValuationConfig:
    """Explicit, versioned configuration for curve calibration and valuation."""

    config_version: str = "1.0.0"
    valuation_date: date = date(2026, 3, 16)
    valuation_time_utc: str = "21:00:00Z"
    currency: Currency = Currency.USD
    curve_day_count: DayCountConvention = DayCountConvention.ACT_365F
    interpolation: InterpolationMethod = InterpolationMethod.LOG_LINEAR_DF
    dv01_bump_bp: float = 1.0
    solver_tolerance: float = 1e-12
    solver_max_iterations: int = 200
    repricing_tolerance_bp: float = 1.0
    dv01_agreement_tolerance_pct: float = 0.5
    allow_negative_zero_rates: bool = False

    def __post_init__(self) -> None:
        if self.dv01_bump_bp <= 0.0 or self.dv01_bump_bp > 100.0:
            raise ValidationError(
                "dv01_bump_bp must be in (0, 100]",
                details={"dv01_bump_bp": self.dv01_bump_bp},
            )
        if self.solver_tolerance <= 0.0:
            raise ValidationError("solver_tolerance must be strictly positive")
        if self.solver_max_iterations < 10:
            raise ValidationError("solver_max_iterations must be at least 10")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "config_version": self.config_version,
            "valuation_date": self.valuation_date.isoformat(),
            "valuation_time_utc": self.valuation_time_utc,
            "currency": self.currency.value,
            "curve_day_count": self.curve_day_count.value,
            "interpolation": self.interpolation.value,
            "dv01_bump_bp": self.dv01_bump_bp,
            "solver_tolerance": self.solver_tolerance,
            "solver_max_iterations": self.solver_max_iterations,
            "repricing_tolerance_bp": self.repricing_tolerance_bp,
            "dv01_agreement_tolerance_pct": self.dv01_agreement_tolerance_pct,
            "allow_negative_zero_rates": self.allow_negative_zero_rates,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ValuationConfig":
        return cls(
            config_version=str(data.get("config_version", "1.0.0")),
            valuation_date=_parse_date(data["valuation_date"], "valuation_date"),
            valuation_time_utc=str(data.get("valuation_time_utc", "21:00:00Z")),
            currency=parse_enum(Currency, data.get("currency", "USD"), "currency"),
            curve_day_count=parse_enum(
                DayCountConvention, data.get("curve_day_count", "ACT/365F"), "curve_day_count"
            ),
            interpolation=parse_enum(
                InterpolationMethod,
                data.get("interpolation", "LOG_LINEAR_DF"),
                "interpolation",
            ),
            dv01_bump_bp=_validate_finite(data.get("dv01_bump_bp", 1.0), "dv01_bump_bp"),
            solver_tolerance=_validate_finite(
                data.get("solver_tolerance", 1e-12), "solver_tolerance"
            ),
            solver_max_iterations=int(data.get("solver_max_iterations", 200)),
            repricing_tolerance_bp=_validate_finite(
                data.get("repricing_tolerance_bp", 1.0), "repricing_tolerance_bp"
            ),
            dv01_agreement_tolerance_pct=_validate_finite(
                data.get("dv01_agreement_tolerance_pct", 0.5), "dv01_agreement_tolerance_pct"
            ),
            allow_negative_zero_rates=bool(data.get("allow_negative_zero_rates", False)),
        )


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp for runtime logs/metadata."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
