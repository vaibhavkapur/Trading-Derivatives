"""Curve shift scenario engine and cash-flow-level P&L attribution."""

from __future__ import annotations

from typing import List, Sequence

from .curve import YieldCurve
from .errors import ValidationError
from .models import (
    BondSpec,
    CashFlowContribution,
    ScenarioResult,
    ScenarioSpec,
    stable_id,
)
from .pricing import compute_analytic_sensitivities, solve_ytm
from .schedule import build_schedule


def default_scenarios(pivot_years: float = 5.0) -> List[ScenarioSpec]:
    """Standard deterministic rate shock scenarios for reporting and demos."""
    return [
        ScenarioSpec(
            scenario_id="par_up_10bp",
            name="Parallel +10bp",
            shift_type="PARALLEL",
            magnitude_bp=10.0,
        ),
        ScenarioSpec(
            scenario_id="par_dn_10bp",
            name="Parallel -10bp",
            shift_type="PARALLEL",
            magnitude_bp=-10.0,
        ),
        ScenarioSpec(
            scenario_id="par_up_100bp",
            name="Parallel +100bp",
            shift_type="PARALLEL",
            magnitude_bp=100.0,
        ),
        ScenarioSpec(
            scenario_id="par_dn_100bp",
            name="Parallel -100bp",
            shift_type="PARALLEL",
            magnitude_bp=-100.0,
        ),
        ScenarioSpec(
            scenario_id="steepener_50bp",
            name="Bull/Bear Steepener 50bp",
            shift_type="STEEPENER",
            magnitude_bp=50.0,
            pivot_years=pivot_years,
        ),
        ScenarioSpec(
            scenario_id="flattener_50bp",
            name="Flattener 50bp",
            shift_type="FLATTENER",
            magnitude_bp=50.0,
            pivot_years=pivot_years,
        ),
        ScenarioSpec(
            scenario_id="butterfly_25bp",
            name="Butterfly Belly +25bp / Wings -25bp",
            shift_type="BUTTERFLY",
            magnitude_bp=25.0,
            pivot_years=pivot_years,
        ),
    ]


def run_scenario(
    bond: BondSpec,
    base_curve: YieldCurve,
    scenario: ScenarioSpec,
    *,
    tol: float = 1e-12,
    max_iter: int = 200,
) -> ScenarioResult:
    """Apply a yield-curve shift scenario to a bond and explain cash-flow P&L contributions."""
    if bond.currency != base_curve.currency:
        raise ValidationError(
            f"Bond '{bond.bond_id}' currency {bond.currency.value} does not match "
            f"curve currency {base_curve.currency.value}"
        )

    val_date = base_curve.valuation_date
    sched = build_schedule(bond)
    future_cfs = sched.future_cash_flows(val_date)
    if not future_cfs:
        raise ValidationError(
            f"Bond '{bond.bond_id}' has no future cash flows after {val_date.isoformat()}"
        )

    shocked_curve = base_curve.apply_scenario(scenario)

    raw_rows = []
    base_dirty = 0.0
    shocked_dirty = 0.0

    for cf in future_cfs:
        t = base_curve.time_from_valuation(cf.payment_date)
        base_df = base_curve.discount_factor(t)
        shocked_df = shocked_curve.discount_factor(t)
        base_pv = cf.total_amount * base_df
        shocked_pv = cf.total_amount * shocked_df
        pv_change = shocked_pv - base_pv

        base_z = base_curve.zero_rate(t) if t > 0.0 else 0.0
        shocked_z = shocked_curve.zero_rate(t) if t > 0.0 else 0.0
        dz = shocked_z - base_z

        first_order = -t * dz * base_pv
        second_order = 0.5 * (t * t) * (dz * dz) * base_pv
        higher_order = pv_change - first_order - second_order

        base_dirty += base_pv
        shocked_dirty += shocked_pv
        raw_rows.append(
            (cf, t, base_pv, shocked_pv, pv_change, dz * 10_000.0, first_order, second_order, higher_order)
        )

    total_pnl = shocked_dirty - base_dirty
    total_pnl_pct = (total_pnl / base_dirty) * 100.0 if base_dirty != 0.0 else 0.0

    contributions: List[CashFlowContribution] = []
    for cf, t, base_pv, shocked_pv, pv_change, dz_bp, d1, d2, res in raw_rows:
        share_pct = (pv_change / total_pnl) * 100.0 if abs(total_pnl) > 1e-15 else 0.0
        contributions.append(
            CashFlowContribution(
                index=cf.index,
                payment_date=cf.payment_date,
                time_years=t,
                total_amount=cf.total_amount,
                base_pv=base_pv,
                shocked_pv=shocked_pv,
                pv_change=pv_change,
                pnl_share_pct=share_pct,
                zero_rate_shift_bp=dz_bp,
                first_order_approx=d1,
                second_order_approx=d2,
                higher_order_residual=res,
            )
        )

    sum_cf_pnl = sum(c.pv_change for c in contributions)
    reconciliation_error = abs(total_pnl - sum_cf_pnl)

    comp = bond.effective_yield_compounding
    base_ytm, _ = solve_ytm(bond, base_dirty, val_date, schedule=sched, compounding=comp, tol=tol, max_iter=max_iter)
    shocked_ytm, _ = solve_ytm(
        bond, shocked_dirty, val_date, schedule=sched, compounding=comp, tol=tol, max_iter=max_iter
    )
    dy = shocked_ytm - base_ytm
    sens = compute_analytic_sensitivities(bond, base_ytm, val_date, schedule=sched, compounding=comp)

    taylor_dur_pnl = -sens["modified_duration"] * base_dirty * dy
    taylor_conv_pnl = 0.5 * sens["convexity"] * base_dirty * (dy * dy)
    taylor_residual = total_pnl - (taylor_dur_pnl + taylor_conv_pnl)

    result_id = stable_id(
        "scen",
        {
            "scenario_id": scenario.scenario_id,
            "bond_id": bond.bond_id,
            "base_curve_id": base_curve.curve_id,
            "shocked_curve_id": shocked_curve.curve_id,
        },
    )

    return ScenarioResult(
        result_id=result_id,
        scenario=scenario,
        bond_id=bond.bond_id,
        currency=bond.currency,
        base_dirty_price=base_dirty,
        shocked_dirty_price=shocked_dirty,
        total_pnl=total_pnl,
        total_pnl_pct=total_pnl_pct,
        base_ytm=base_ytm,
        shocked_ytm=shocked_ytm,
        ytm_change_bp=dy * 10_000.0,
        sum_cf_pnl=sum_cf_pnl,
        reconciliation_error=reconciliation_error,
        taylor_duration_pnl=taylor_dur_pnl,
        taylor_convexity_pnl=taylor_conv_pnl,
        taylor_residual=taylor_residual,
        contributions=tuple(contributions),
    )


def run_scenario_suite(
    bonds: Sequence[BondSpec],
    base_curve: YieldCurve,
    scenarios: Sequence[ScenarioSpec],
    *,
    tol: float = 1e-12,
    max_iter: int = 200,
) -> List[ScenarioResult]:
    """Run all scenarios across a portfolio of bonds."""
    results: List[ScenarioResult] = []
    for bond in bonds:
        for scenario in scenarios:
            results.append(run_scenario(bond, base_curve, scenario, tol=tol, max_iter=max_iter))
    return results
