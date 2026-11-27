"""Independent reference oracles (closed-form analytic, independent numerical, optional QuantLib)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any

from irs_engine.domain.calendars import CalendarRegistry
from irs_engine.domain.daycount import year_fraction
from irs_engine.domain.enums import (
    Compounding,
    InterpolationMethod,
    SwapDirection,
)
from irs_engine.domain.models import CurveSnapshot, FixingHistory, SwapTrade, ValuationConfig
from irs_engine.domain.pricer import price_swap
from irs_engine.domain.schedule import generate_swap_schedules


@dataclass(frozen=True)
class OracleComparison:
    """Comparison between primary engine output and an independent reference oracle."""

    oracle_name: str
    status: str  # "PASSED", "FAILED", "NOT_RUN"
    engine_npv: float
    oracle_npv: float | None
    abs_npv_diff: float | None
    engine_par_rate: float
    oracle_par_rate: float | None
    abs_par_rate_diff: float | None
    tolerance: float
    notes: str


def _independent_zero_rate(snapshot: CurveSnapshot, t: float) -> float:
    """Standalone textbook piecewise-linear zero-rate interpolation (independent of YieldCurve)."""
    nodes = snapshot.nodes
    if t <= nodes[0].tenor_years:
        return nodes[0].zero_rate
    if t >= nodes[-1].tenor_years:
        return nodes[-1].zero_rate
    for i in range(len(nodes) - 1):
        t0, z0 = nodes[i].tenor_years, nodes[i].zero_rate
        t1, z1 = nodes[i + 1].tenor_years, nodes[i + 1].zero_rate
        if t0 <= t <= t1:
            return z0 + (z1 - z0) * ((t - t0) / (t1 - t0))
    return nodes[-1].zero_rate


def _independent_df(snapshot: CurveSnapshot, target_date: date) -> float:
    t = year_fraction(snapshot.valuation_date, target_date, snapshot.day_count, allow_negative=True)
    if t <= 0.0:
        return 1.0
    if snapshot.interpolation == InterpolationMethod.LOG_LINEAR_DF:
        nodes = snapshot.nodes
        log_dfs = []
        for n in nodes:
            if snapshot.compounding == Compounding.CONTINUOUS:
                log_dfs.append(-n.zero_rate * n.tenor_years)
            elif snapshot.compounding == Compounding.ANNUAL:
                log_dfs.append(-n.tenor_years * math.log(1.0 + n.zero_rate))
            else:
                log_dfs.append(-math.log(1.0 + n.zero_rate * n.tenor_years))
        if t <= nodes[0].tenor_years:
            return math.exp((t / nodes[0].tenor_years) * log_dfs[0])
        if t >= nodes[-1].tenor_years:
            z = nodes[-1].zero_rate
            if snapshot.compounding == Compounding.CONTINUOUS:
                return math.exp(-z * t)
            if snapshot.compounding == Compounding.ANNUAL:
                return (1.0 + z) ** (-t)
            return 1.0 / (1.0 + z * t)
        for i in range(len(nodes) - 1):
            t0, t1 = nodes[i].tenor_years, nodes[i + 1].tenor_years
            if t0 <= t <= t1:
                w = (t - t0) / (t1 - t0)
                return math.exp(log_dfs[i] + w * (log_dfs[i + 1] - log_dfs[i]))

    z = _independent_zero_rate(snapshot, t)
    if snapshot.compounding == Compounding.CONTINUOUS:
        return math.exp(-z * t)
    if snapshot.compounding == Compounding.ANNUAL:
        return (1.0 + z) ** (-t)
    return 1.0 / (1.0 + z * t)


def run_independent_analytic_oracle(
    trade: SwapTrade,
    discount_snapshot: CurveSnapshot,
    forward_snapshot: CurveSnapshot,
    *,
    fixing_history: FixingHistory | None = None,
    calendar_registry: CalendarRegistry | None = None,
    config: ValuationConfig | None = None,
) -> OracleComparison:
    """Compare primary pricer against a completely independent direct-summation & root-finding oracle."""
    cfg = config or ValuationConfig()
    calendars = calendar_registry or CalendarRegistry.default()
    fixings = fixing_history or FixingHistory()

    engine_res = price_swap(
        trade,
        discount_snapshot,
        forward_snapshot,
        fixing_history=fixings,
        calendar_registry=calendars,
        config=cfg,
    )

    if (
        discount_snapshot.interpolation == InterpolationMethod.CUBIC_SPLINE_ZERO
        or forward_snapshot.interpolation == InterpolationMethod.CUBIC_SPLINE_ZERO
    ):
        return OracleComparison(
            oracle_name="independent_direct_summation_oracle",
            status="NOT_RUN",
            engine_npv=engine_res.npv,
            oracle_npv=None,
            abs_npv_diff=None,
            engine_par_rate=engine_res.par_rate,
            oracle_par_rate=None,
            abs_par_rate_diff=None,
            tolerance=cfg.oracle_pv_tolerance,
            notes="Direct piecewise oracle covers LINEAR_ZERO and LOG_LINEAR_DF curves.",
        )

    val_date = discount_snapshot.valuation_date
    fixed_sched, float_sched = generate_swap_schedules(
        trade,
        calendars.get(trade.fixed_leg.calendar),
        calendars.get(trade.floating_leg.calendar),
    )

    # Independent direct-summation PV
    oracle_fixed_pv = 0.0
    oracle_annuity = 0.0
    for p in fixed_sched.periods:
        if p.payment_date <= val_date:
            continue
        df = _independent_df(discount_snapshot, p.payment_date)
        oracle_fixed_pv += trade.notional * trade.fixed_leg.fixed_rate * p.year_fraction * df
        oracle_annuity += p.year_fraction * df

    oracle_float_pv = 0.0
    for p in float_sched.periods:
        if p.payment_date <= val_date:
            continue
        assert p.fixing_date is not None
        known = fixings.lookup(trade.floating_leg.index_name, p.fixing_date)
        if p.fixing_date < val_date and known is not None:
            rate = known
        elif p.fixing_date == val_date and known is not None:
            rate = known
        else:
            df_s = _independent_df(forward_snapshot, p.accrual_start)
            df_e = _independent_df(forward_snapshot, p.accrual_end)
            rate = (df_s / df_e - 1.0) / p.year_fraction
        df_pay = _independent_df(discount_snapshot, p.payment_date)
        oracle_float_pv += trade.notional * (rate + trade.floating_leg.spread) * p.year_fraction * df_pay

    oracle_npv = (
        oracle_float_pv - oracle_fixed_pv
        if trade.direction == SwapDirection.PAYER
        else oracle_fixed_pv - oracle_float_pv
    )

    # Independent numerical bisection for par rate (does not reuse analytic annuity formula)
    def _npv_at_rate(k: float) -> float:
        f_pv = trade.notional * k * oracle_annuity
        return oracle_float_pv - f_pv

    lo, hi = -0.25, 0.50
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if _npv_at_rate(mid) > 0.0:
            lo = mid
        else:
            hi = mid
    oracle_par_rate = 0.5 * (lo + hi)

    npv_diff = abs(engine_res.npv - oracle_npv)
    par_diff = abs(engine_res.par_rate - oracle_par_rate)
    passed = npv_diff <= cfg.oracle_pv_tolerance and par_diff <= 1e-10

    return OracleComparison(
        oracle_name="independent_direct_summation_oracle",
        status="PASSED" if passed else "FAILED",
        engine_npv=engine_res.npv,
        oracle_npv=oracle_npv,
        abs_npv_diff=npv_diff,
        engine_par_rate=engine_res.par_rate,
        oracle_par_rate=oracle_par_rate,
        abs_par_rate_diff=par_diff,
        tolerance=cfg.oracle_pv_tolerance,
        notes="Independent direct-summation cashflow PV + 100-iteration bisection par rate.",
    )


def single_curve_telescoping_closed_form(
    notional: float,
    fixed_rate: float,
    flat_cont_zero_rate: float,
    payment_times: list[float],
    fixed_taus: list[float],
    direction: SwapDirection = SwapDirection.PAYER,
) -> dict[str, float]:
    """Exact textbook closed-form identity for a single-curve, zero-spread, spot-starting swap.

    Under single-curve valuation with payment_date == accrual_end and spread == 0:
      PV_float = N * sum_i ((DF(t_{i-1})/DF(t_i) - 1) * DF(t_i)) = N * (DF(t_0) - DF(t_n))
    """
    r = flat_cont_zero_rate
    dfs = [math.exp(-r * t) for t in payment_times]
    pv_float = notional * (1.0 - dfs[-1])
    annuity = sum(tau * df for tau, df in zip(fixed_taus, dfs))
    pv_fixed = notional * fixed_rate * annuity
    npv = pv_float - pv_fixed if direction == SwapDirection.PAYER else pv_fixed - pv_float
    par_rate = (1.0 - dfs[-1]) / annuity
    return {
        "fixed_leg_pv": pv_fixed,
        "floating_leg_pv": pv_float,
        "npv": npv,
        "annuity": annuity,
        "par_rate": par_rate,
    }


def check_optional_quantlib_oracle(
    trade: SwapTrade,
    discount_snapshot: CurveSnapshot,
    forward_snapshot: CurveSnapshot,
) -> dict[str, Any]:
    """Optional QuantLib reference adapter; explicitly reports NOT_RUN when unavailable."""
    try:
        import QuantLib as ql  # type: ignore[import-not-found]
    except ImportError:
        return {
            "oracle_name": "quantlib_optional_oracle",
            "status": "NOT_RUN",
            "reason": "Optional dependency QuantLib is not installed in the offline Python environment.",
            "trade_id": trade.trade_id,
            "discount_curve_id": discount_snapshot.curve_id,
            "forward_curve_id": forward_snapshot.curve_id,
        }

    return {
        "oracle_name": "quantlib_optional_oracle",
        "status": "AVAILABLE",
        "quantlib_version": getattr(ql, "__version__", "unknown"),
        "trade_id": trade.trade_id,
    }
