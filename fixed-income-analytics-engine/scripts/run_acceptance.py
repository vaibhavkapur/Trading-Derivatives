#!/usr/bin/env python3
"""Run full acceptance verification suite and emit machine-readable & Markdown evidence."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from fixed_income.cli import execute_market_run
from fixed_income.conventions import Currency, DayCountConvention
from fixed_income.curve import bootstrap_curve
from fixed_income.diagnostics import runtime_metadata
from fixed_income.io import load_market_fixture, write_json_report
from fixed_income.models import BondSpec, DepositQuote
from fixed_income.pricing import (
    compute_analytic_sensitivities,
    price_bond_by_yield_api,
    price_bond_on_curve,
)
from fixed_income.schedule import build_schedule, compute_accrued_interest


def run_oracle_comparisons() -> Dict[str, Any]:
    """Compare engine outputs against independent hand-calculated analytical oracles."""
    fixtures_dir = PROJECT_ROOT / "data" / "fixtures"
    oracles = json.loads((fixtures_dir / "reference_hand_calculated.json").read_text())["oracles"]

    comparisons: List[Dict[str, Any]] = []

    # 1. 3M Deposit DF oracle
    dep_o = oracles["deposit_3m_act360"]
    dep = DepositQuote.from_dict(dep_o["deposit"])
    from datetime import date

    c_dep = bootstrap_curve(
        date.fromisoformat(dep_o["valuation_date"]),
        [dep],
        [],
        currency=Currency.USD,
        curve_day_count=DayCountConvention.ACT_365F,
    )
    df_obs = c_dep.nodes[1].discount_factor
    comparisons.append(
        {
            "oracle_id": "deposit_3m_act360_df",
            "expected": dep_o["expected_df"],
            "observed": df_obs,
            "abs_error": abs(df_obs - dep_o["expected_df"]),
            "tolerance": dep_o["tolerance"],
            "passed": abs(df_obs - dep_o["expected_df"]) <= dep_o["tolerance"],
        }
    )

    # 2. 5Y Zero-coupon bond oracle
    zo = oracles["zero_coupon_5y_semiannual_ytm"]
    z_res = price_bond_by_yield_api(
        BondSpec.from_dict(zo["bond"]), zo["ytm"], date.fromisoformat(zo["valuation_date"])
    )
    for metric, obs in [
        ("dirty_price", z_res.dirty_price),
        ("macaulay_duration", z_res.macaulay_duration),
        ("modified_duration", z_res.modified_duration),
        ("convexity", z_res.convexity),
        ("analytic_dv01", z_res.analytic_dv01),
    ]:
        exp = zo[f"expected_{metric}"]
        comparisons.append(
            {
                "oracle_id": f"zero_5y_{metric}",
                "expected": exp,
                "observed": obs,
                "abs_error": abs(obs - exp),
                "tolerance": zo["tolerance"],
                "passed": abs(obs - exp) <= zo["tolerance"],
            }
        )

    # 3. 2Y Par bond oracle
    po = oracles["par_bond_2y_semiannual"]
    p_res = price_bond_by_yield_api(
        BondSpec.from_dict(po["bond"]), po["ytm"], date.fromisoformat(po["valuation_date"])
    )
    for metric, obs in [
        ("dirty_price", p_res.dirty_price),
        ("macaulay_duration", p_res.macaulay_duration),
        ("modified_duration", p_res.modified_duration),
        ("analytic_dv01", p_res.analytic_dv01),
    ]:
        exp = po[f"expected_{metric}"]
        comparisons.append(
            {
                "oracle_id": f"par_2y_{metric}",
                "expected": exp,
                "observed": obs,
                "abs_error": abs(obs - exp),
                "tolerance": po["tolerance"],
                "passed": abs(obs - exp) <= po["tolerance"],
            }
        )

    return {
        "oracle_source": "data/fixtures/reference_hand_calculated.json",
        "all_passed": all(c["passed"] for c in comparisons),
        "comparisons": comparisons,
    }


def main() -> int:
    fixtures_dir = PROJECT_ROOT / "data" / "fixtures"
    reports_dir = PROJECT_ROOT / "reports"

    # 1. Run full unittest suite
    loader = unittest.TestLoader()
    suite = loader.discover(str(PROJECT_ROOT / "tests"), pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    test_result = runner.run(suite)

    # 2. Run main synthetic fixture end-to-end twice to prove deterministic reproducibility
    valid_fixture = fixtures_dir / "valid_curve_and_bonds.json"
    run_a = execute_market_run(valid_fixture, run_label="acceptance_run_a")
    run_b = execute_market_run(valid_fixture, run_label="acceptance_run_b")
    reproducible = run_a["logical_fingerprint"] == run_b["logical_fingerprint"]

    # 3. Evaluate acceptance metrics across valid + boundary bonds
    max_calib_err_bp = run_a["curve"]["max_calibration_error_bp"]
    max_dv01_diff_pct = max(r["dv01_relative_diff_pct"] for r in run_a["pricing_results"])
    max_scen_recon_err = max(s["reconciliation_error"] for s in run_a["scenario_results"])

    # Cash-flow schedule reconciliation across all valid + boundary bonds
    config, deposits, bond_quotes, portfolio, _ = load_market_fixture(valid_fixture)
    curve = bootstrap_curve(
        config.valuation_date,
        deposits,
        bond_quotes,
        currency=config.currency,
        curve_day_count=config.curve_day_count,
        interpolation=config.interpolation,
    )
    boundary_raw = json.loads((fixtures_dir / "boundary_irregular_and_holidays.json").read_text())
    boundary_bonds = [BondSpec.from_dict(c["bond"]) for c in boundary_raw["boundary_bonds"]]

    all_bonds = list(portfolio) + boundary_bonds
    max_cf_recon_err = 0.0
    max_boundary_dv01_diff_pct = 0.0
    for bond in all_bonds:
        sched = build_schedule(bond)
        err = abs((sched.total_coupons + sched.total_principal) - sched.total_cash_flows)
        max_cf_recon_err = max(max_cf_recon_err, err)
        pr = price_bond_on_curve(bond, curve, bump_bp=config.dv01_bump_bp)
        max_boundary_dv01_diff_pct = max(max_boundary_dv01_diff_pct, pr.dv01_relative_diff_pct)

    # 4. Oracle baseline comparison
    oracle_report = run_oracle_comparisons()
    write_json_report(
        reports_dir / "baseline_comparison.json",
        {
            "runtime_metadata": runtime_metadata(),
            "oracle_comparison": oracle_report,
        },
    )

    # 5. Aggregate acceptance results JSON
    meta = runtime_metadata()
    quantlib_status = (
        "AVAILABLE" if meta["optional_packages"].get("QuantLib") != "not_installed" else "NOT_RUN_UNAVAILABLE"
    )
    acceptance_payload = {
        "runtime_metadata": meta,
        "logical_fingerprint": run_a["logical_fingerprint"],
        "test_suite": {
            "tests_run": test_result.testsRun,
            "failures": len(test_result.failures),
            "errors": len(test_result.errors),
            "passed": test_result.wasSuccessful(),
        },
        "criteria": {
            "reprice_calibration_within_1bp": {
                "target": "<= 1.0 bp",
                "measured_max_error_bp": max_calib_err_bp,
                "passed": max_calib_err_bp <= 1.0,
            },
            "cash_flow_totals_reconcile": {
                "target": "<= 1e-9",
                "measured_schedule_max_error": max_cf_recon_err,
                "measured_scenario_pnl_max_error": max_scen_recon_err,
                "passed": max_cf_recon_err <= 1e-9 and max_scen_recon_err <= 1e-9,
            },
            "dv01_agrees_with_finite_differences_within_0_5_pct": {
                "target": "<= 0.5%",
                "measured_portfolio_max_diff_pct": max_dv01_diff_pct,
                "measured_all_bonds_max_diff_pct": max_boundary_dv01_diff_pct,
                "passed": max_boundary_dv01_diff_pct <= 0.5,
            },
            "oracle_reference_comparison": {
                "target": "all hand-calculated closed-form oracles within tolerance",
                "all_passed": oracle_report["all_passed"],
                "passed": oracle_report["all_passed"],
            },
            "deterministic_reproducibility": {
                "target": "identical logical_fingerprint across repeated runs",
                "run_a_fingerprint": run_a["logical_fingerprint"],
                "run_b_fingerprint": run_b["logical_fingerprint"],
                "passed": reproducible,
            },
            "optional_quantlib_cross_check": {
                "status": quantlib_status,
                "note": "Optional QuantLib external cross-check not run because QuantLib is not installed in the offline environment; independent closed-form analytical oracles are verified instead.",
            },
        },
    }
    write_json_report(reports_dir / "acceptance_results.json", acceptance_payload)
    write_json_report(reports_dir / "demo_output.json", run_a)

    all_ok = (
        test_result.wasSuccessful()
        and acceptance_payload["criteria"]["reprice_calibration_within_1bp"]["passed"]
        and acceptance_payload["criteria"]["cash_flow_totals_reconcile"]["passed"]
        and acceptance_payload["criteria"]["dv01_agrees_with_finite_differences_within_0_5_pct"]["passed"]
        and acceptance_payload["criteria"]["oracle_reference_comparison"]["passed"]
        and acceptance_payload["criteria"]["deterministic_reproducibility"]["passed"]
    )
    print(f"\nAcceptance suite overall status: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
