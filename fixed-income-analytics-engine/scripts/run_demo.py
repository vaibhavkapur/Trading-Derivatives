#!/usr/bin/env python3
"""Reproducible end-to-end demo script for the Fixed Income Analytics Engine."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from fixed_income.cli import execute_market_run
from fixed_income.io import write_json_report


def main() -> int:
    fixture = PROJECT_ROOT / "data" / "fixtures" / "valid_curve_and_bonds.json"
    output_path = PROJECT_ROOT / "reports" / "demo_output.json"

    result = execute_market_run(fixture, run_label="demo_walkthrough")
    write_json_report(output_path, result)

    print("=== Fixed Income Analytics Engine — Demo Walkthrough ===")
    print(f"Valuation Date : {result['config']['valuation_date']} ({result['config']['valuation_time_utc']})")
    print(f"Currency       : {result['config']['currency']}")
    print(f"Curve ID       : {result['curve']['curve_id']}")
    print(f"Logical Hash   : {result['logical_fingerprint']}")
    print(f"Max Calib Err  : {result['curve']['max_calibration_error_bp']:.10f} bp")
    print()
    print("Bootstrapped Curve Nodes:")
    for node in result["curve"]["nodes"][1:]:
        print(
            f"  {node['instrument_id']:<18} mat={node['maturity_date']} "
            f"t={node['time_years']:6.3f}y DF={node['discount_factor']:.8f} "
            f"zero={node['zero_rate_continuous']*100:.4f}%"
        )

    print("\nPortfolio Bond Valuations:")
    for pr in result["pricing_results"]:
        print(
            f"  {pr['bond_id']:<20} clean={pr['clean_price_pct_par']:8.4f}% "
            f"dirty={pr['dirty_price_pct_par']:8.4f}% ytm={pr['ytm']*100:6.4f}% "
            f"modDur={pr['modified_duration']:6.4f} conv={pr['convexity']:7.3f} "
            f"dv01={pr['analytic_dv01']:8.2f} (fd_diff={pr['dv01_relative_diff_pct']:.6f}%)"
        )

    print("\nSample Scenario Attribution (PORT_LONG_10Y, Parallel +100bp):")
    sample = next(
        s
        for s in result["scenario_results"]
        if s["bond_id"] == "PORT_LONG_10Y" and s["scenario"]["scenario_id"] == "par_up_100bp"
    )
    print(
        f"  Total PnL={sample['total_pnl']:,.2f} ({sample['total_pnl_pct']:.4f}%) | "
        f"Taylor(Dur+Conv)={sample['taylor_duration_pnl'] + sample['taylor_convexity_pnl']:,.2f} | "
        f"CF Reconcile Err={sample['reconciliation_error']:.2e}"
    )
    print(f"\nSaved raw report to: {output_path.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
