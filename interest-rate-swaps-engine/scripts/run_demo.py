#!/usr/bin/env python3
"""Reproducible end-to-end demo for the Interest Rate Swaps Engine."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from irs_engine.domain.calendars import CalendarRegistry
from irs_engine.domain.enums import SwapDirection
from irs_engine.domain.oracle import check_optional_quantlib_oracle, run_independent_analytic_oracle
from irs_engine.domain.pricer import price_swap
from irs_engine.domain.scenarios import run_deterministic_scenarios, run_stochastic_scenarios
from irs_engine.domain.sensitivities import compute_sensitivities
from irs_engine.io import load_valuation_bundle

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    calendars = CalendarRegistry.default()
    bundle_path = ROOT / "data" / "fixtures" / "valid" / "usd_5y_vanilla_swap.json"
    scenarios_path = ROOT / "config" / "scenarios.json"

    trade, disc, fwd, fixings, cfg, _meta = load_valuation_bundle(bundle_path)
    scen_cfg = json.loads(scenarios_path.read_text(encoding="utf-8"))

    print("=== Interest Rate Swaps Engine — Reproducible Demo ===")
    print(f"Trade ID:        {trade.trade_id} ({trade.direction.value}, {trade.currency} {trade.notional:,.0f})")
    print(f"Dates:           {trade.effective_date} -> {trade.maturity_date} (Val: {disc.valuation_date})")
    print(f"Curves:          Discount={disc.curve_id} | Forward={fwd.curve_id}")

    val = price_swap(trade, disc, fwd, fixing_history=fixings, calendar_registry=calendars, config=cfg)
    print("\n--- 1. Dual-Curve Valuation ---")
    print(f"Valuation ID:    {val.valuation_id}")
    print(f"Fixed Leg PV:    {val.fixed_leg_pv:,.2f} {val.currency}")
    print(f"Floating Leg PV: {val.floating_leg_pv:,.2f} {val.currency}")
    print(f"Swap NPV:        {val.npv:,.2f} {val.currency}")
    print(f"Par Fixed Rate:  {val.par_rate * 100:.6f}%")
    print(f"Fixed DV01:      {val.fixed_dv01:,.2f} {val.currency}/bp")

    # Payer / Receiver symmetry check
    opp_dir = SwapDirection.RECEIVER if trade.direction == SwapDirection.PAYER else SwapDirection.PAYER
    opp_trade = dataclasses.replace(trade, direction=opp_dir)
    opp_val = price_swap(opp_trade, disc, fwd, fixing_history=fixings, calendar_registry=calendars, config=cfg)
    print(f"Symmetry Check:  PV(PAYER) + PV(RECEIVER) = {val.npv + opp_val.npv:.3e} {val.currency}")

    # At-market par swap check
    par_fixed = dataclasses.replace(trade.fixed_leg, fixed_rate=val.par_rate)
    par_trade = dataclasses.replace(trade, trade_id=f"{trade.trade_id}-PAR", fixed_leg=par_fixed)
    par_val = price_swap(par_trade, disc, fwd, fixing_history=fixings, calendar_registry=calendars, config=cfg)
    print(f"At-Market Par:   NPV at par rate = {par_val.npv:.3e} {val.currency}")

    # Sensitivities & KR01 reconciliation
    sens = compute_sensitivities(
        trade, disc, fwd, fixing_history=fixings, calendar_registry=calendars, config=cfg
    )
    print("\n--- 2. Parallel PV01 & Key-Rate Sensitivities ---")
    print(f"Discount PV01:   {sens.discount_pv01:,.4f} | Sum(Disc KR01): {sens.discount_kr01_sum:,.4f}")
    print(f"Forward PV01:    {sens.forward_pv01:,.4f} | Sum(Fwd KR01):  {sens.forward_kr01_sum:,.4f}")
    print(f"Parallel PV01:   {sens.parallel_pv01:,.4f} | Sum(All KR01):  {sens.total_kr01_sum:,.4f}")
    print(
        f"KR01 Reconcile:  diff={sens.total_reconciliation_diff:+.6f} "
        f"(rel={sens.total_reconciliation_rel_diff:.4%}) | within_tol={sens.reconciliation_within_tolerance}"
    )

    # Independent reference oracle comparison
    oracle = run_independent_analytic_oracle(
        trade, disc, fwd, fixing_history=fixings, calendar_registry=calendars, config=cfg
    )
    ql = check_optional_quantlib_oracle(trade, disc, fwd)
    print("\n--- 3. Independent Reference Comparison ---")
    print(
        f"Analytic Oracle: status={oracle.status} | abs_npv_diff={oracle.abs_npv_diff:.3e} "
        f"| abs_par_diff={oracle.abs_par_rate_diff:.3e}"
    )
    print(f"QuantLib Oracle: status={ql['status']} ({ql.get('reason', 'available')})")

    # Deterministic & stochastic scenarios
    det = run_deterministic_scenarios(
        trade,
        disc,
        fwd,
        scen_cfg["deterministic_scenarios"],
        fixing_history=fixings,
        calendar_registry=calendars,
        config=cfg,
    )
    sc = scen_cfg["stochastic_config"]
    stoch = run_stochastic_scenarios(
        trade,
        disc,
        fwd,
        seed=sc["seed"],
        num_paths=sc["num_paths"],
        level_vol_bps=sc["level_vol_bps"],
        slope_vol_bps=sc["slope_vol_bps"],
        curvature_vol_bps=sc["curvature_vol_bps"],
        basis_vol_bps=sc["basis_vol_bps"],
        fixing_history=fixings,
        calendar_registry=calendars,
        config=cfg,
    )
    print("\n--- 4. Scenario Snapshots ---")
    for snap in det:
        print(
            f"  {snap.scenario_id:<24} NPV={snap.npv:>12,.2f}  PnL={snap.pnl:>+12,.2f}  "
            f"dPar={snap.par_rate_change_bps:>+7.2f} bps"
        )
    print(
        f"\nStochastic (seed={stoch.seed}, paths={stoch.num_paths}): "
        f"Mean PnL={stoch.mean_pnl:+,.2f} | Std={stoch.std_pnl:,.2f} | "
        f"95% VaR={stoch.var_95:,.2f} | 95% ES={stoch.expected_shortfall_95:,.2f}"
    )


if __name__ == "__main__":
    main()
