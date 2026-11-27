"""Immutable typed domain models for vanilla interest rate swap valuation and risk."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from irs_engine.domain.enums import (
    BusinessDayConvention,
    Compounding,
    CurveRole,
    DayCountConvention,
    FiniteDifferenceScheme,
    FixingFallbackPolicy,
    FixingSource,
    Frequency,
    InterpolationMethod,
    StubType,
    SwapDirection,
    ValuationDateFixingPolicy,
)
from irs_engine.domain.errors import CurveError, ValidationError

SUPPORTED_CURRENCIES = frozenset({"USD", "EUR", "GBP"})


@dataclass(frozen=True)
class FixedLegSpec:
    """Contractual specification of the fixed leg."""

    fixed_rate: float  # Decimal rate, e.g. 0.0425 for 4.25%
    frequency: Frequency
    day_count: DayCountConvention
    business_day_convention: BusinessDayConvention = BusinessDayConvention.MODIFIED_FOLLOWING
    calendar: str = "USD_NY"
    stub_type: StubType = StubType.FRONT_SHORT
    end_of_month: bool = False
    payment_lag_days: int = 0

    def __post_init__(self) -> None:
        if self.payment_lag_days < 0:
            raise ValidationError(
                "Fixed leg payment_lag_days must be non-negative.",
                details={"payment_lag_days": self.payment_lag_days},
            )


@dataclass(frozen=True)
class FloatingLegSpec:
    """Contractual specification of the floating leg."""

    index_name: str  # e.g. "USD-SOFR-3M"
    frequency: Frequency
    day_count: DayCountConvention
    spread: float = 0.0  # Decimal spread, e.g. 0.0010 for 10 bps
    business_day_convention: BusinessDayConvention = BusinessDayConvention.MODIFIED_FOLLOWING
    calendar: str = "USD_NY"
    stub_type: StubType = StubType.FRONT_SHORT
    end_of_month: bool = False
    fixing_lag_days: int = 2
    payment_lag_days: int = 0

    def __post_init__(self) -> None:
        if not self.index_name or not self.index_name.strip():
            raise ValidationError("Floating leg index_name must be non-empty.")
        if self.fixing_lag_days < 0:
            raise ValidationError(
                "Floating leg fixing_lag_days must be non-negative.",
                details={"fixing_lag_days": self.fixing_lag_days},
            )
        if self.payment_lag_days < 0:
            raise ValidationError(
                "Floating leg payment_lag_days must be non-negative.",
                details={"payment_lag_days": self.payment_lag_days},
            )


@dataclass(frozen=True)
class SwapTrade:
    """Single-currency vanilla fixed-floating interest rate swap."""

    trade_id: str
    currency: str
    notional: float  # Positive currency units, e.g. 10_000_000.0
    direction: SwapDirection
    effective_date: date
    maturity_date: date
    fixed_leg: FixedLegSpec
    floating_leg: FloatingLegSpec
    schema_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if not self.trade_id or not self.trade_id.strip():
            raise ValidationError("trade_id must be a non-empty string.")
        if self.currency not in SUPPORTED_CURRENCIES:
            raise ValidationError(
                f"Unsupported currency '{self.currency}'. Single-currency swaps support: {sorted(SUPPORTED_CURRENCIES)}.",
                details={"currency": self.currency, "supported": sorted(SUPPORTED_CURRENCIES)},
            )
        if self.notional <= 0.0:
            raise ValidationError(
                "notional must be strictly positive.",
                details={"notional": self.notional},
            )
        if self.maturity_date <= self.effective_date:
            raise ValidationError(
                "maturity_date must be strictly after effective_date.",
                details={
                    "effective_date": self.effective_date.isoformat(),
                    "maturity_date": self.maturity_date.isoformat(),
                },
            )


@dataclass(frozen=True)
class CurveNode:
    """Single tenor pillar on a yield curve."""

    tenor_years: float  # Strictly positive year fraction (e.g., 0.25, 1.0, 5.0)
    zero_rate: float  # Decimal zero rate (e.g., 0.042 for 4.20%, negative allowed)
    label: str = ""

    def __post_init__(self) -> None:
        if self.tenor_years <= 0.0:
            raise CurveError(
                "CurveNode tenor_years must be strictly positive.",
                details={"tenor_years": self.tenor_years},
            )
        if self.zero_rate <= -0.50 or self.zero_rate >= 1.0:
            raise CurveError(
                "CurveNode zero_rate is outside realistic bounds (-50%, +100%).",
                details={"tenor_years": self.tenor_years, "zero_rate": self.zero_rate},
            )


@dataclass(frozen=True)
class CurveSnapshot:
    """Explicit zero-rate yield curve snapshot with role, currency, and conventions."""

    curve_id: str
    role: CurveRole
    currency: str
    valuation_date: date
    nodes: tuple[CurveNode, ...]
    day_count: DayCountConvention = DayCountConvention.ACT_365F
    compounding: Compounding = Compounding.CONTINUOUS
    interpolation: InterpolationMethod = InterpolationMethod.LINEAR_ZERO
    index_name: str | None = None  # Optional index tag for FORWARD curves
    schema_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if not self.curve_id or not self.curve_id.strip():
            raise CurveError("curve_id must be a non-empty string.")
        if self.currency not in SUPPORTED_CURRENCIES:
            raise CurveError(
                f"Unsupported curve currency '{self.currency}'.",
                details={"currency": self.currency},
            )
        if len(self.nodes) == 0:
            raise CurveError(
                "CurveSnapshot must contain at least one CurveNode.",
                details={"curve_id": self.curve_id},
            )
        tenors = [n.tenor_years for n in self.nodes]
        for prev, curr in zip(tenors, tenors[1:]):
            if curr <= prev:
                raise CurveError(
                    "CurveSnapshot nodes must have strictly increasing tenor_years.",
                    details={"curve_id": self.curve_id, "tenors": tenors},
                )


@dataclass(frozen=True)
class FixingEntry:
    """Single historical index fixing observation."""

    index_name: str
    fixing_date: date
    rate: float  # Decimal rate, e.g. 0.0435


@dataclass(frozen=True)
class FixingHistory:
    """Historical index fixings indexed by (index_name, fixing_date)."""

    entries: tuple[FixingEntry, ...] = field(default_factory=tuple)
    schema_version: str = "1.0.0"

    def lookup(self, index_name: str, fixing_date: date) -> float | None:
        for entry in self.entries:
            if entry.index_name == index_name and entry.fixing_date == fixing_date:
                return entry.rate
        return None


@dataclass(frozen=True)
class ValuationConfig:
    """Explicit runtime configuration for valuation, sensitivities, and policies."""

    default_bump_bps: float = 1.0
    finite_difference_scheme: FiniteDifferenceScheme = FiniteDifferenceScheme.CENTRAL
    missing_fixing_policy: FixingFallbackPolicy = FixingFallbackPolicy.ERROR
    valuation_date_fixing_policy: ValuationDateFixingPolicy = ValuationDateFixingPolicy.ALLOW_PROJECT
    par_swap_pv_tolerance: float = 1e-6
    symmetry_pv_tolerance: float = 1e-9
    kr01_reconciliation_rel_tolerance: float = 0.005
    kr01_reconciliation_abs_tolerance: float = 0.5
    oracle_pv_tolerance: float = 1e-6

    def __post_init__(self) -> None:
        if self.default_bump_bps <= 0.0:
            raise ValidationError(
                "default_bump_bps must be strictly positive.",
                details={"default_bump_bps": self.default_bump_bps},
            )


@dataclass(frozen=True)
class SchedulePeriod:
    """Single accrual and payment period generated by the schedule engine."""

    period_index: int
    unadjusted_start: date
    unadjusted_end: date
    accrual_start: date
    accrual_end: date
    payment_date: date
    year_fraction: float
    is_stub: bool = False
    stub_kind: str = "REGULAR"
    fixing_date: date | None = None


@dataclass(frozen=True)
class LegSchedule:
    """Complete payment/reset schedule for one swap leg."""

    leg_type: str  # "FIXED" or "FLOATING"
    periods: tuple[SchedulePeriod, ...]


@dataclass(frozen=True)
class CashflowRow:
    """Projected or fixed single cashflow valuation detail."""

    period_index: int
    leg_type: str  # "FIXED" or "FLOATING"
    accrual_start: date
    accrual_end: date
    payment_date: date
    fixing_date: date | None
    year_fraction: float
    rate: float  # Fixed coupon rate or index fixing/projected rate
    spread: float  # 0.0 for fixed leg
    all_in_rate: float
    cashflow_amount: float  # Unsigned positive leg cashflow (notional * all_in_rate * tau)
    discount_factor: float
    present_value: float
    status: str  # "ACTIVE" or "SETTLED"
    fixing_source: FixingSource | None = None
    is_stub: bool = False


@dataclass(frozen=True)
class SwapValuationResult:
    """Complete deterministic valuation snapshot for a vanilla interest rate swap."""

    valuation_id: str
    trade_id: str
    valuation_date: date
    currency: str
    direction: SwapDirection
    notional: float
    discount_curve_id: str
    forward_curve_id: str
    fixed_leg_pv: float
    floating_leg_pv: float
    npv: float  # Signed from trade direction: PAYER = float - fixed; RECEIVER = fixed - float
    annuity: float  # Sum(tau_i * DF_i) on active fixed leg periods
    fixed_dv01: float  # Notional * annuity * 1e-4
    par_rate: float  # Exact fixed rate making NPV == 0
    par_spread: float  # Exact floating spread making NPV == 0 at current fixed_rate
    fixed_cashflows: tuple[CashflowRow, ...]
    floating_cashflows: tuple[CashflowRow, ...]
    diagnostics: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "1.0.0"


@dataclass(frozen=True)
class KeyRateSensitivity:
    """Key-rate sensitivity (KR01) at a single curve pillar."""

    curve_role: CurveRole
    curve_id: str
    tenor_years: float
    label: str
    kr01: float  # Currency PV change per +1 bp zero-rate bump at this pillar


@dataclass(frozen=True)
class SensitivityResult:
    """Par rate, parallel PV01, convexity, and key-rate sensitivities with reconciliation."""

    valuation_id: str
    trade_id: str
    valuation_date: date
    currency: str
    direction: SwapDirection
    bump_bps: float
    scheme: FiniteDifferenceScheme
    base_npv: float
    par_rate: float
    discount_pv01: float
    forward_pv01: float
    parallel_pv01: float
    parallel_convexity: float  # Second-order sensitivity per bp^2
    discount_kr01: tuple[KeyRateSensitivity, ...]
    forward_kr01: tuple[KeyRateSensitivity, ...]
    discount_kr01_sum: float
    forward_kr01_sum: float
    total_kr01_sum: float
    discount_reconciliation_diff: float
    forward_reconciliation_diff: float
    total_reconciliation_diff: float
    total_reconciliation_rel_diff: float
    reconciliation_within_tolerance: bool
    schema_version: str = "1.0.0"


@dataclass(frozen=True)
class ScenarioSnapshot:
    """Single scenario valuation outcome compared to base valuation."""

    scenario_id: str
    description: str
    npv: float
    pnl: float  # scenario_npv - base_npv
    par_rate: float
    par_rate_change_bps: float
    fixed_leg_pv: float
    floating_leg_pv: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StochasticSummary:
    """Summary statistics across seeded Monte Carlo yield-curve shock scenarios."""

    seed: int
    num_paths: int
    base_npv: float
    mean_npv: float
    std_npv: float
    mean_pnl: float
    std_pnl: float
    min_pnl: float
    p05_pnl: float
    p50_pnl: float
    p95_pnl: float
    max_pnl: float
    var_95: float  # Positive loss magnitude at 5th percentile of PnL
    expected_shortfall_95: float
    sample_paths: tuple[dict[str, float], ...] = field(default_factory=tuple)
