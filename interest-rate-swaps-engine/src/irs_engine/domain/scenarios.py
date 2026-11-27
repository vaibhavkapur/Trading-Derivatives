"""Deterministic curve shock scenarios and seeded stochastic Monte Carlo engine."""

from __future__ import annotations

import math
import random
from typing import Any

from irs_engine.domain.calendars import CalendarRegistry
from irs_engine.domain.curves import YieldCurve
from irs_engine.domain.errors import ValidationError
from irs_engine.domain.models import (
    CurveSnapshot,
    FixingHistory,
    ScenarioSnapshot,
    StochasticSummary,
    SwapTrade,
    ValuationConfig,
)
from irs_engine.domain.pricer import price_swap


def _compute_twist_shifts(
    tenors: list[float],
    short_shift_bps: float,
    long_shift_bps: float,
    short_tenor: float = 0.5,
    long_tenor: float = 10.0,
) -> list[float]:
    if long_tenor <= short_tenor:
        raise ValidationError("long_tenor must be strictly greater than short_tenor for TWIST scenario.")
    shifts: list[float] = []
    for t in tenors:
        if t <= short_tenor:
            shifts.append(short_shift_bps)
        elif t >= long_tenor:
            shifts.append(long_shift_bps)
        else:
            w = (t - short_tenor) / (long_tenor - short_tenor)
            shifts.append(short_shift_bps + w * (long_shift_bps - short_shift_bps))
    return shifts


def _compute_butterfly_shifts(
    tenors: list[float],
    wing_shift_bps: float,
    belly_shift_bps: float,
    belly_tenor: float = 5.0,
) -> list[float]:
    if belly_tenor <= 0.0:
        raise ValidationError("belly_tenor must be strictly positive for BUTTERFLY scenario.")
    shifts: list[float] = []
    max_t = max(tenors) if tenors else belly_tenor
    for t in tenors:
        if t <= belly_tenor:
            w = t / belly_tenor
        else:
            span = max(max_t - belly_tenor, 1.0)
            w = max(0.0, 1.0 - (t - belly_tenor) / span)
        shifts.append(wing_shift_bps + w * (belly_shift_bps - wing_shift_bps))
    return shifts


def apply_scenario_spec(
    discount_curve: YieldCurve,
    forward_curve: YieldCurve,
    spec: dict[str, Any],
) -> tuple[YieldCurve, YieldCurve]:
    """Build shocked (discount, forward) curves from a declarative scenario dict."""
    kind = str(spec.get("kind", "")).upper()
    sid = str(spec.get("scenario_id", "custom"))

    if kind == "PARALLEL":
        d_bps = float(spec.get("discount_shift_bps", 0.0))
        f_bps = float(spec.get("forward_shift_bps", 0.0))
        return (
            discount_curve.bumped_parallel(d_bps) if d_bps != 0.0 else discount_curve,
            forward_curve.bumped_parallel(f_bps) if f_bps != 0.0 else forward_curve,
        )

    apply_to = str(spec.get("apply_to", "BOTH")).upper()
    if apply_to not in {"BOTH", "DISCOUNT", "FORWARD"}:
        raise ValidationError(f"Invalid apply_to value '{apply_to}' in scenario '{sid}'.")

    if kind == "TWIST":
        s_bps = float(spec.get("short_shift_bps", 0.0))
        l_bps = float(spec.get("long_shift_bps", 0.0))
        s_t = float(spec.get("short_tenor", 0.5))
        l_t = float(spec.get("long_tenor", 10.0))
        d_curve = (
            discount_curve.bumped_vector(
                _compute_twist_shifts([n.tenor_years for n in discount_curve.snapshot.nodes], s_bps, l_bps, s_t, l_t),
                suffix=sid,
            )
            if apply_to in {"BOTH", "DISCOUNT"}
            else discount_curve
        )
        f_curve = (
            forward_curve.bumped_vector(
                _compute_twist_shifts([n.tenor_years for n in forward_curve.snapshot.nodes], s_bps, l_bps, s_t, l_t),
                suffix=sid,
            )
            if apply_to in {"BOTH", "FORWARD"}
            else forward_curve
        )
        return d_curve, f_curve

    if kind == "BUTTERFLY":
        w_bps = float(spec.get("wing_shift_bps", 0.0))
        b_bps = float(spec.get("belly_shift_bps", 0.0))
        b_t = float(spec.get("belly_tenor", 5.0))
        d_curve = (
            discount_curve.bumped_vector(
                _compute_butterfly_shifts([n.tenor_years for n in discount_curve.snapshot.nodes], w_bps, b_bps, b_t),
                suffix=sid,
            )
            if apply_to in {"BOTH", "DISCOUNT"}
            else discount_curve
        )
        f_curve = (
            forward_curve.bumped_vector(
                _compute_butterfly_shifts([n.tenor_years for n in forward_curve.snapshot.nodes], w_bps, b_bps, b_t),
                suffix=sid,
            )
            if apply_to in {"BOTH", "FORWARD"}
            else forward_curve
        )
        return d_curve, f_curve

    if kind == "CUSTOM_PILLARS":
        d_vec = spec.get("discount_shifts_bps")
        f_vec = spec.get("forward_shifts_bps")
        d_curve = (
            discount_curve.bumped_vector([float(x) for x in d_vec], suffix=sid)
            if d_vec is not None
            else discount_curve
        )
        f_curve = (
            forward_curve.bumped_vector([float(x) for x in f_vec], suffix=sid)
            if f_vec is not None
            else forward_curve
        )
        return d_curve, f_curve

    raise ValidationError(
        f"Unsupported scenario kind '{kind}' for scenario '{sid}'.",
        details={"scenario_id": sid, "kind": kind},
    )


def run_deterministic_scenarios(
    trade: SwapTrade,
    discount_curve: YieldCurve | CurveSnapshot,
    forward_curve: YieldCurve | CurveSnapshot,
    scenario_specs: list[dict[str, Any]],
    *,
    fixing_history: FixingHistory | None = None,
    calendar_registry: CalendarRegistry | None = None,
    config: ValuationConfig | None = None,
) -> tuple[ScenarioSnapshot, ...]:
    """Evaluate a list of deterministic curve shock scenarios against base valuation."""
    disc = discount_curve if isinstance(discount_curve, YieldCurve) else YieldCurve(discount_curve)
    fwd = forward_curve if isinstance(forward_curve, YieldCurve) else YieldCurve(forward_curve)
    fixings = fixing_history or FixingHistory()
    calendars = calendar_registry or CalendarRegistry.default()
    cfg = config or ValuationConfig()

    base = price_swap(
        trade, disc, fwd, fixing_history=fixings, calendar_registry=calendars, config=cfg
    )

    results: list[ScenarioSnapshot] = []
    for spec in scenario_specs:
        sid = str(spec.get("scenario_id", "unnamed"))
        desc = str(spec.get("description", sid))
        s_disc, s_fwd = apply_scenario_spec(disc, fwd, spec)
        s_val = price_swap(
            trade, s_disc, s_fwd, fixing_history=fixings, calendar_registry=calendars, config=cfg
        )
        results.append(
            ScenarioSnapshot(
                scenario_id=sid,
                description=desc,
                npv=s_val.npv,
                pnl=s_val.npv - base.npv,
                par_rate=s_val.par_rate,
                par_rate_change_bps=(s_val.par_rate - base.par_rate) * 1e4,
                fixed_leg_pv=s_val.fixed_leg_pv,
                floating_leg_pv=s_val.floating_leg_pv,
                metadata={"spec": spec},
            )
        )
    return tuple(results)


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (k - lo) * (sorted_vals[hi] - sorted_vals[lo])


def run_stochastic_scenarios(
    trade: SwapTrade,
    discount_curve: YieldCurve | CurveSnapshot,
    forward_curve: YieldCurve | CurveSnapshot,
    *,
    seed: int = 42,
    num_paths: int = 250,
    level_vol_bps: float = 65.0,
    slope_vol_bps: float = 30.0,
    curvature_vol_bps: float = 15.0,
    basis_vol_bps: float = 8.0,
    fixing_history: FixingHistory | None = None,
    calendar_registry: CalendarRegistry | None = None,
    config: ValuationConfig | None = None,
) -> StochasticSummary:
    """Run deterministic fixed-seed 3-factor PCA + basis Monte Carlo yield-curve shocks.

    Uses Python's deterministic Mersenne Twister (`random.Random(seed)`) so identical
    seeds and inputs yield bit-for-bit reproducible scenario distributions.
    """
    if num_paths <= 0:
        raise ValidationError("num_paths must be strictly positive.", details={"num_paths": num_paths})

    disc = discount_curve if isinstance(discount_curve, YieldCurve) else YieldCurve(discount_curve)
    fwd = forward_curve if isinstance(forward_curve, YieldCurve) else YieldCurve(forward_curve)
    fixings = fixing_history or FixingHistory()
    calendars = calendar_registry or CalendarRegistry.default()
    cfg = config or ValuationConfig()

    base = price_swap(
        trade, disc, fwd, fixing_history=fixings, calendar_registry=calendars, config=cfg
    )
    rng = random.Random(seed)

    d_tenors = [n.tenor_years for n in disc.snapshot.nodes]
    f_tenors = [n.tenor_years for n in fwd.snapshot.nodes]

    def _factor_loadings(t: float) -> tuple[float, float, float]:
        # Smooth Nelson-Siegel-like level, slope, and curvature factor loadings
        lam = 0.5
        lt = max(lam * t, 1e-8)
        slope_load = (1.0 - math.exp(-lt)) / lt
        curv_load = slope_load - math.exp(-lt)
        # Center slope around ~2Y so +slope steepens
        return 1.0, (0.5 - slope_load) * 2.0, curv_load * 2.0

    d_loads = [_factor_loadings(t) for t in d_tenors]
    f_loads = [_factor_loadings(t) for t in f_tenors]

    npvs: list[float] = []
    pnls: list[float] = []
    samples: list[dict[str, float]] = []

    for i in range(num_paths):
        z_lvl = rng.gauss(0.0, level_vol_bps)
        z_slp = rng.gauss(0.0, slope_vol_bps)
        z_crv = rng.gauss(0.0, curvature_vol_bps)
        z_bas = rng.gauss(0.0, basis_vol_bps)

        d_shocks = [l1 * z_lvl + l2 * z_slp + l3 * z_crv for (l1, l2, l3) in d_loads]
        f_shocks = [l1 * z_lvl + l2 * z_slp + l3 * z_crv + z_bas for (l1, l2, l3) in f_loads]

        s_disc = disc.bumped_vector(d_shocks, suffix=f"mc_{i}")
        s_fwd = fwd.bumped_vector(f_shocks, suffix=f"mc_{i}")
        res = price_swap(
            trade, s_disc, s_fwd, fixing_history=fixings, calendar_registry=calendars, config=cfg
        )
        pnl = res.npv - base.npv
        npvs.append(res.npv)
        pnls.append(pnl)
        if i < 5:
            samples.append(
                {
                    "path": float(i),
                    "level_bps": round(z_lvl, 4),
                    "slope_bps": round(z_slp, 4),
                    "curvature_bps": round(z_crv, 4),
                    "basis_bps": round(z_bas, 4),
                    "npv": round(res.npv, 6),
                    "pnl": round(pnl, 6),
                }
            )

    mean_npv = sum(npvs) / num_paths
    mean_pnl = sum(pnls) / num_paths
    var_npv = sum((x - mean_npv) ** 2 for x in npvs) / max(num_paths - 1, 1)
    var_pnl = sum((x - mean_pnl) ** 2 for x in pnls) / max(num_paths - 1, 1)

    sorted_pnls = sorted(pnls)
    p05 = _percentile(sorted_pnls, 0.05)
    p50 = _percentile(sorted_pnls, 0.50)
    p95 = _percentile(sorted_pnls, 0.95)
    tail = [x for x in sorted_pnls if x <= p05]
    es_95 = -(sum(tail) / len(tail)) if tail else 0.0

    return StochasticSummary(
        seed=seed,
        num_paths=num_paths,
        base_npv=base.npv,
        mean_npv=mean_npv,
        std_npv=math.sqrt(var_npv),
        mean_pnl=mean_pnl,
        std_pnl=math.sqrt(var_pnl),
        min_pnl=sorted_pnls[0],
        p05_pnl=p05,
        p50_pnl=p50,
        p95_pnl=p95,
        max_pnl=sorted_pnls[-1],
        var_95=-p05,
        expected_shortfall_95=es_95,
        sample_paths=tuple(samples),
    )
