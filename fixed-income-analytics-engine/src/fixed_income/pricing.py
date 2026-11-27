"""Bond pricing, YTM solving, Z-spread, duration, convexity, and DV01 analytics."""

from __future__ import annotations

import math
from datetime import date
from typing import Dict, List, Optional, Tuple

from .conventions import Compounding, DayCountConvention
from .curve import YieldCurve
from .daycount import year_fraction
from .errors import ConvergenceError, UnsupportedConventionError, ValidationError
from .models import (
    SCHEMA_VERSION,
    BondSpec,
    CashFlow,
    CashFlowSchedule,
    CashFlowValuation,
    PricingResult,
    stable_id,
)
from .schedule import build_schedule, compute_accrued_interest
from .solvers import brentq, newton_safeguarded


def cash_flow_yield_times(
    bond: BondSpec,
    valuation_date: date,
    future_cfs: Tuple[CashFlow, ...],
    accrued_fraction: float,
) -> List[float]:
    """Compute time in years to each future cash flow for yield/duration formulas.

    Conventions:
    - For ACT/ACT_ICMA coupon bonds: exact ICMA/Street period-based cumulative
      accrual times: t_1 = active_cf.accrual_factor - accrued_fraction, then
      t_k = t_{k-1} + cf_k.accrual_factor. On a coupon date for a regular bond
      of frequency m, this yields exact multiples 1/m, 2/m, ..., n/m so a par
      bond prices identically to par when YTM == coupon_rate.
    - For zero-coupon bonds or non-ICMA day counts (30/360_US, 30E/360,
      ACT/365F, ACT/360): direct year_fraction(valuation_date, payment_date,
      bond.day_count), using accrual_end when unadjusted accrual aligns with
      payment convention.
    """
    if not future_cfs:
        return []

    if bond.day_count == DayCountConvention.ACT_ACT_ICMA and not bond.is_zero_coupon:
        times: List[float] = []
        # First future cash flow remaining accrual fraction
        first_cf = future_cfs[0]
        if first_cf.accrual_start <= valuation_date < first_cf.accrual_end:
            t_cum = max(first_cf.accrual_factor - accrued_fraction, 1e-12)
        else:
            t_cum = year_fraction(valuation_date, first_cf.payment_date, bond.day_count)
        times.append(t_cum)
        for cf in future_cfs[1:]:
            t_cum += cf.accrual_factor
            times.append(t_cum)
        return times

    return [
        year_fraction(valuation_date, cf.payment_date, bond.day_count)
        for cf in future_cfs
    ]


def _discount_and_derivatives(
    t: float,
    y: float,
    compounding: Compounding,
) -> Tuple[float, float, float]:
    """Return (df, d(df)/dy, d2(df)/dy2) for a cash flow at time t."""
    if compounding == Compounding.CONTINUOUS:
        df = math.exp(-y * t)
        d1 = -t * df
        d2 = (t * t) * df
        return df, d1, d2

    if compounding == Compounding.SIMPLE:
        denom = 1.0 + y * t
        if denom <= 0.0:
            raise ValidationError(
                "Yield produces non-positive simple discount denominator",
                details={"y": y, "t": t},
            )
        df = 1.0 / denom
        d1 = -t / (denom * denom)
        d2 = 2.0 * t * t / (denom * denom * denom)
        return df, d1, d2

    m = compounding.periods_per_year
    base = 1.0 + y / m
    if base <= 0.0:
        raise ValidationError(
            "Yield produces non-positive compounding base (1 + y/m <= 0)",
            details={"y": y, "m": m},
        )
    exp = m * t
    df = base ** (-exp)
    d1 = -t * (base ** (-exp - 1.0))
    d2 = t * (t + 1.0 / m) * (base ** (-exp - 2.0))
    return df, d1, d2


def price_and_yield_derivatives(
    times: List[float],
    amounts: List[float],
    ytm: float,
    compounding: Compounding,
) -> Tuple[float, float, float, float]:
    """Compute (dirty_price, dP/dy, d2P/dy2, weighted_time_pv_sum) analytically."""
    price = 0.0
    dp_dy = 0.0
    d2p_dy2 = 0.0
    t_pv_sum = 0.0
    for t, amt in zip(times, amounts):
        df, d1, d2 = _discount_and_derivatives(t, ytm, compounding)
        pv = amt * df
        price += pv
        dp_dy += amt * d1
        d2p_dy2 += amt * d2
        t_pv_sum += t * pv
    return price, dp_dy, d2p_dy2, t_pv_sum


def price_bond_from_yield(
    bond: BondSpec,
    ytm: float,
    valuation_date: date,
    *,
    schedule: Optional[CashFlowSchedule] = None,
    compounding: Optional[Compounding] = None,
) -> Tuple[float, float, float]:
    """Compute (dirty_price, clean_price, accrued_interest) from YTM in currency units."""
    sched = schedule or build_schedule(bond)
    accrued = compute_accrued_interest(bond, valuation_date, schedule=sched)
    future_cfs = sched.future_cash_flows(valuation_date)
    if not future_cfs:
        raise ValidationError(
            f"Bond '{bond.bond_id}' has no future cash flows after {valuation_date.isoformat()}",
            details={"bond_id": bond.bond_id, "valuation_date": valuation_date.isoformat()},
        )
    comp = compounding or bond.effective_yield_compounding
    times = cash_flow_yield_times(bond, valuation_date, future_cfs, accrued.accrued_fraction)
    amounts = [cf.total_amount for cf in future_cfs]
    dirty, _d1, _d2, _tpv = price_and_yield_derivatives(times, amounts, ytm, comp)
    clean = dirty - accrued.accrued_interest
    return dirty, clean, accrued.accrued_interest


def solve_ytm(
    bond: BondSpec,
    target_dirty_price: float,
    valuation_date: date,
    *,
    schedule: Optional[CashFlowSchedule] = None,
    compounding: Optional[Compounding] = None,
    tol: float = 1e-12,
    max_iter: int = 200,
) -> Tuple[float, int]:
    """Solve YTM from dirty price using safeguarded Newton-Raphson with bracket expansion."""
    if not math.isfinite(target_dirty_price) or target_dirty_price <= 0.0:
        raise ValidationError(
            "target_dirty_price must be finite and strictly positive",
            details={"bond_id": bond.bond_id, "target_dirty_price": target_dirty_price},
        )
    sched = schedule or build_schedule(bond)
    accrued = compute_accrued_interest(bond, valuation_date, schedule=sched)
    future_cfs = sched.future_cash_flows(valuation_date)
    if not future_cfs:
        raise ValidationError(
            f"Bond '{bond.bond_id}' has no future cash flows after {valuation_date.isoformat()}"
        )
    comp = compounding or bond.effective_yield_compounding
    times = cash_flow_yield_times(bond, valuation_date, future_cfs, accrued.accrued_fraction)
    amounts = [cf.total_amount for cf in future_cfs]

    # Lower bound must keep (1 + y/m) > 0 (or 1 + y*t > 0 for SIMPLE)
    if comp == Compounding.CONTINUOUS:
        low_y = -0.50
    elif comp == Compounding.SIMPLE:
        max_t = max(times)
        low_y = max(-0.50, -0.95 / max_t)
    else:
        m = comp.periods_per_year
        low_y = max(-0.50, -0.95 * m)

    high_y = 2.0

    def f(y: float) -> float:
        p, _, _, _ = price_and_yield_derivatives(times, amounts, y, comp)
        return p - target_dirty_price

    def df(y: float) -> float:
        _, d1, _, _ = price_and_yield_derivatives(times, amounts, y, comp)
        return d1

    # Expand upper bound if price is extremely distressed
    while f(high_y) > 0.0 and high_y < 50.0:
        high_y *= 2.0

    x0 = bond.coupon_rate if bond.coupon_rate > 0.0 else 0.04
    return newton_safeguarded(
        f, df, low_y, high_y, x0, tol=tol, max_iter=max_iter, label=f"ytm:{bond.bond_id}"
    )


def compute_analytic_sensitivities(
    bond: BondSpec,
    ytm: float,
    valuation_date: date,
    *,
    schedule: Optional[CashFlowSchedule] = None,
    compounding: Optional[Compounding] = None,
    bump_bp: float = 1.0,
) -> Dict[str, float]:
    """Compute exact analytic duration, convexity, DV01, and finite-difference DV01 comparison.

    Returns a dictionary containing:
    - dirty_price
    - macaulay_duration (years)
    - modified_duration (years / per 100% yield change)
    - convexity (years^2)
    - analytic_dv01 (currency units per +1bp yield increase; positive for standard bonds)
    - numerical_yield_dv01 (central finite-difference DV01 scaled to 1bp)
    - one_sided_up_dv01 (forward difference DV01 for boundary diagnostics)
    - effective_convexity (central finite-difference second derivative / P)
    - dv01_relative_diff_pct (percentage difference between analytic and central FD DV01)
    """
    if bump_bp <= 0.0:
        raise ValidationError("bump_bp must be strictly positive", details={"bump_bp": bump_bp})

    sched = schedule or build_schedule(bond)
    accrued = compute_accrued_interest(bond, valuation_date, schedule=sched)
    future_cfs = sched.future_cash_flows(valuation_date)
    if not future_cfs:
        raise ValidationError(f"Bond '{bond.bond_id}' has no future cash flows")

    comp = compounding or bond.effective_yield_compounding
    times = cash_flow_yield_times(bond, valuation_date, future_cfs, accrued.accrued_fraction)
    amounts = [cf.total_amount for cf in future_cfs]

    dirty, dp_dy, d2p_dy2, t_pv_sum = price_and_yield_derivatives(times, amounts, ytm, comp)
    if dirty <= 0.0:
        raise ValidationError("Computed dirty price must be positive")

    macaulay = t_pv_sum / dirty
    modified = -dp_dy / dirty
    convexity = d2p_dy2 / dirty
    analytic_dv01 = -dp_dy * 1e-4

    dy = bump_bp * 1e-4
    p_up, _, _, _ = price_and_yield_derivatives(times, amounts, ytm + dy, comp)
    p_down, _, _, _ = price_and_yield_derivatives(times, amounts, ytm - dy, comp)

    # Central finite difference scaled to 1bp
    numerical_dv01 = (p_down - p_up) / (2.0 * bump_bp)
    one_sided_up_dv01 = (dirty - p_up) / bump_bp
    effective_convexity = (p_up + p_down - 2.0 * dirty) / (dirty * dy * dy)

    denom = max(abs(analytic_dv01), 1e-12)
    rel_diff_pct = abs(analytic_dv01 - numerical_dv01) / denom * 100.0

    return {
        "dirty_price": dirty,
        "macaulay_duration": macaulay,
        "modified_duration": modified,
        "convexity": convexity,
        "analytic_dv01": analytic_dv01,
        "numerical_yield_dv01": numerical_dv01,
        "one_sided_up_dv01": one_sided_up_dv01,
        "effective_convexity": effective_convexity,
        "dv01_relative_diff_pct": rel_diff_pct,
    }


def solve_z_spread_bp(
    bond: BondSpec,
    target_dirty_price: float,
    curve: YieldCurve,
    *,
    schedule: Optional[CashFlowSchedule] = None,
    tol: float = 1e-12,
    max_iter: int = 200,
) -> float:
    """Solve constant continuous Z-spread (in basis points) over the zero curve."""
    sched = schedule or build_schedule(bond)
    future_cfs = sched.future_cash_flows(curve.valuation_date)
    if not future_cfs:
        raise ValidationError(f"Bond '{bond.bond_id}' has no future cash flows")

    cf_data = [
        (
            curve.time_from_valuation(cf.payment_date),
            curve.discount_factor_on(cf.payment_date),
            cf.total_amount,
        )
        for cf in future_cfs
    ]

    def objective(z_spread: float) -> float:
        pv = sum(amt * df * math.exp(-z_spread * t) for t, df, amt in cf_data)
        return pv - target_dirty_price

    spread, _ = brentq(
        objective,
        -0.50,
        2.00,
        tol=tol,
        max_iter=max_iter,
        label=f"zspread:{bond.bond_id}",
    )
    return spread * 10_000.0


def price_bond_on_curve(
    bond: BondSpec,
    curve: YieldCurve,
    *,
    market_clean_price_pct: Optional[float] = None,
    bump_bp: float = 1.0,
    compute_key_rate_dv01: bool = True,
    tol: float = 1e-12,
    max_iter: int = 200,
) -> PricingResult:
    """High-level Pricing API: price a bond against a calibrated YieldCurve.

    Computes:
    - Clean/dirty price and accrued interest
    - Implied YTM (with solver iteration diagnostics)
    - Analytic Macaulay duration, Modified duration, Convexity, Analytic DV01
    - Central finite-difference yield DV01 and relative agreement (%)
    - Curve parallel-shift DV01 (central finite difference across curve nodes)
    - Key-rate DV01 per curve node
    - Z-spread (0.0 bp when valued directly on curve; solved when market_clean_price_pct supplied)
    - Per-cash-flow present value and PV weights
    """
    if bond.currency != curve.currency:
        raise ValidationError(
            f"Bond '{bond.bond_id}' currency {bond.currency.value} does not match "
            f"curve currency {curve.currency.value}",
            details={"bond_id": bond.bond_id, "bond_ccy": bond.currency.value, "curve_ccy": curve.currency.value},
        )

    val_date = curve.valuation_date
    sched = build_schedule(bond)
    accrued = compute_accrued_interest(bond, val_date, schedule=sched)
    future_cfs = sched.future_cash_flows(val_date)
    if not future_cfs:
        raise ValidationError(
            f"Bond '{bond.bond_id}' has no future cash flows after {val_date.isoformat()}",
            details={"bond_id": bond.bond_id, "valuation_date": val_date.isoformat()},
        )

    raw_valuations: List[Tuple[CashFlow, float, float, float]] = []
    dirty_price = 0.0
    for cf in future_cfs:
        t_curve = curve.time_from_valuation(cf.payment_date)
        df = curve.discount_factor(t_curve)
        pv = cf.total_amount * df
        dirty_price += pv
        raw_valuations.append((cf, t_curve, df, pv))

    clean_price = dirty_price - accrued.accrued_interest
    scale = bond.notional / 100.0
    dirty_pct = dirty_price / scale
    clean_pct = clean_price / scale

    cf_valuations = tuple(
        CashFlowValuation(
            index=cf.index,
            payment_date=cf.payment_date,
            time_years=t_curve,
            coupon_amount=cf.coupon_amount,
            principal_amount=cf.principal_amount,
            total_amount=cf.total_amount,
            discount_factor=df,
            present_value=pv,
            pv_weight=pv / dirty_price if dirty_price > 0.0 else 0.0,
        )
        for cf, t_curve, df, pv in raw_valuations
    )

    comp = bond.effective_yield_compounding
    ytm, solver_iters = solve_ytm(
        bond, dirty_price, val_date, schedule=sched, compounding=comp, tol=tol, max_iter=max_iter
    )

    sens = compute_analytic_sensitivities(
        bond, ytm, val_date, schedule=sched, compounding=comp, bump_bp=bump_bp
    )

    # Curve parallel-shift central finite difference DV01
    up_shifts = {n.instrument_id: bump_bp for n in curve.nodes[1:]}
    down_shifts = {n.instrument_id: -bump_bp for n in curve.nodes[1:]}
    curve_up = curve.with_node_zero_shifts(up_shifts, label="par_up")
    curve_down = curve.with_node_zero_shifts(down_shifts, label="par_down")
    pv_up = sum(cf.total_amount * curve_up.discount_factor_on(cf.payment_date) for cf in future_cfs)
    pv_down = sum(cf.total_amount * curve_down.discount_factor_on(cf.payment_date) for cf in future_cfs)
    curve_par_dv01 = (pv_down - pv_up) / (2.0 * bump_bp)

    # Key-rate DV01 per curve maturity node
    kr_dv01: Dict[str, float] = {}
    if compute_key_rate_dv01:
        for node in curve.nodes[1:]:
            c_up = curve.with_node_zero_shifts({node.instrument_id: bump_bp}, label=f"kr_up_{node.instrument_id}")
            c_dn = curve.with_node_zero_shifts({node.instrument_id: -bump_bp}, label=f"kr_dn_{node.instrument_id}")
            p_u = sum(cf.total_amount * c_up.discount_factor_on(cf.payment_date) for cf in future_cfs)
            p_d = sum(cf.total_amount * c_dn.discount_factor_on(cf.payment_date) for cf in future_cfs)
            kr_dv01[node.instrument_id] = (p_d - p_u) / (2.0 * bump_bp)

    if market_clean_price_pct is not None:
        target_dirty = market_clean_price_pct * scale + accrued.accrued_interest
        z_spread = solve_z_spread_bp(bond, target_dirty, curve, schedule=sched, tol=tol, max_iter=max_iter)
    else:
        z_spread = 0.0

    pricing_id = stable_id(
        "price",
        {
            "bond_id": bond.bond_id,
            "curve_id": curve.curve_id,
            "valuation_date": val_date.isoformat(),
            "dirty_price": round(dirty_price, 10),
        },
    )

    return PricingResult(
        pricing_id=pricing_id,
        schema_version=SCHEMA_VERSION,
        bond_id=bond.bond_id,
        currency=bond.currency,
        valuation_date=val_date,
        notional=bond.notional,
        dirty_price=dirty_price,
        clean_price=clean_price,
        accrued_interest=accrued.accrued_interest,
        dirty_price_pct_par=dirty_pct,
        clean_price_pct_par=clean_pct,
        ytm=ytm,
        yield_compounding=comp,
        macaulay_duration=sens["macaulay_duration"],
        modified_duration=sens["modified_duration"],
        convexity=sens["convexity"],
        analytic_dv01=sens["analytic_dv01"],
        numerical_yield_dv01=sens["numerical_yield_dv01"],
        dv01_relative_diff_pct=sens["dv01_relative_diff_pct"],
        curve_parallel_dv01=curve_par_dv01,
        z_spread_bp=z_spread,
        key_rate_dv01=kr_dv01,
        cash_flow_valuations=cf_valuations,
        accrued_detail=accrued,
        solver_iterations=solver_iters,
    )


def price_bond_by_yield_api(
    bond: BondSpec,
    ytm: float,
    valuation_date: date,
    *,
    compounding: Optional[Compounding] = None,
    bump_bp: float = 1.0,
) -> PricingResult:
    """High-level Pricing API: price a bond directly from a quoted YTM (curve-free)."""
    sched = build_schedule(bond)
    accrued = compute_accrued_interest(bond, valuation_date, schedule=sched)
    future_cfs = sched.future_cash_flows(valuation_date)
    if not future_cfs:
        raise ValidationError(
            f"Bond '{bond.bond_id}' has no future cash flows after {valuation_date.isoformat()}"
        )
    comp = compounding or bond.effective_yield_compounding
    times = cash_flow_yield_times(bond, valuation_date, future_cfs, accrued.accrued_fraction)
    sens = compute_analytic_sensitivities(
        bond, ytm, valuation_date, schedule=sched, compounding=comp, bump_bp=bump_bp
    )
    dirty = sens["dirty_price"]
    clean = dirty - accrued.accrued_interest
    scale = bond.notional / 100.0

    cf_valuations = []
    for cf, t in zip(future_cfs, times):
        df, _, _ = _discount_and_derivatives(t, ytm, comp)
        pv = cf.total_amount * df
        cf_valuations.append(
            CashFlowValuation(
                index=cf.index,
                payment_date=cf.payment_date,
                time_years=t,
                coupon_amount=cf.coupon_amount,
                principal_amount=cf.principal_amount,
                total_amount=cf.total_amount,
                discount_factor=df,
                present_value=pv,
                pv_weight=pv / dirty if dirty > 0.0 else 0.0,
            )
        )

    pricing_id = stable_id(
        "price_ytm",
        {
            "bond_id": bond.bond_id,
            "valuation_date": valuation_date.isoformat(),
            "ytm": round(ytm, 12),
            "compounding": comp.value,
        },
    )

    return PricingResult(
        pricing_id=pricing_id,
        schema_version=SCHEMA_VERSION,
        bond_id=bond.bond_id,
        currency=bond.currency,
        valuation_date=valuation_date,
        notional=bond.notional,
        dirty_price=dirty,
        clean_price=clean,
        accrued_interest=accrued.accrued_interest,
        dirty_price_pct_par=dirty / scale,
        clean_price_pct_par=clean / scale,
        ytm=ytm,
        yield_compounding=comp,
        macaulay_duration=sens["macaulay_duration"],
        modified_duration=sens["modified_duration"],
        convexity=sens["convexity"],
        analytic_dv01=sens["analytic_dv01"],
        numerical_yield_dv01=sens["numerical_yield_dv01"],
        dv01_relative_diff_pct=sens["dv01_relative_diff_pct"],
        curve_parallel_dv01=None,
        z_spread_bp=None,
        key_rate_dv01={},
        cash_flow_valuations=tuple(cf_valuations),
        accrued_detail=accrued,
        solver_iterations=0,
    )
