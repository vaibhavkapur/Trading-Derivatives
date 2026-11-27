#!/usr/bin/env python3
"""Run reproducible acceptance checks, oracle comparisons, and record raw evidence in reports/."""

from __future__ import annotations

import json
import time
from pathlib import Path

from volatility_surface_engine import (
    ArbitrageViolationError,
    ExtrapolationError,
    OptionChainSnapshot,
    build_surface,
    clean_and_invert_quotes,
    evaluate_heldout_quotes,
    load_chain_snapshot,
    load_config,
    save_surface_snapshot,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "default_config.json"
FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"
REPORTS_DIR = PROJECT_ROOT / "reports"


def main() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    config = load_config(CONFIG_PATH)

    # 1. Reference oracle baseline comparison
    t0 = time.perf_counter()
    ref_chain = load_chain_snapshot(FIXTURES_DIR / "valid_chain_reference.json")
    expected_ref = json.loads((FIXTURES_DIR / "expected_reference.json").read_text(encoding="utf-8"))
    ref_surface = build_surface(ref_chain, config)
    ref_elapsed_ms = (time.perf_counter() - t0) * 1000.0

    oracle_comparisons = []
    max_oracle_iv_err = 0.0
    for oq in expected_ref["oracle_queries"]:
        res = ref_surface.query(expiry_years=oq["expiry_years"], log_moneyness=oq["log_moneyness"], model="svi")
        iv_err = abs(res.implied_vol - oq["expected_implied_vol"])
        max_oracle_iv_err = max(max_oracle_iv_err, iv_err)
        oracle_comparisons.append(
            {
                "expiry_years": oq["expiry_years"],
                "log_moneyness": oq["log_moneyness"],
                "expected_iv": oq["expected_implied_vol"],
                "calibrated_iv": res.implied_vol,
                "abs_err_iv": iv_err,
            }
        )

    baseline_report = {
        "reference_snapshot_id": ref_chain.snapshot_id,
        "surface_id": ref_surface.surface_id,
        "config_hash": ref_surface.snapshot.config_hash,
        "calibration_time_ms": round(ref_elapsed_ms, 2),
        "is_valid": ref_surface.is_valid,
        "overall_svi_weighted_rmse_iv": ref_surface.snapshot.diagnostics.overall_svi_weighted_rmse_iv,
        "overall_sabr_weighted_rmse_iv": ref_surface.snapshot.diagnostics.overall_sabr_weighted_rmse_iv,
        "max_oracle_iv_abs_err": max_oracle_iv_err,
        "tolerance_max_iv_abs_err": expected_ref["tolerances"]["max_iv_recovery_abs_err"],
        "passed_oracle_tolerance": max_oracle_iv_err <= expected_ref["tolerances"]["max_iv_recovery_abs_err"],
        "oracle_comparisons": oracle_comparisons,
        "forward_estimates": [f.to_dict() for f in ref_surface.snapshot.diagnostics.forward_estimates],
    }
    (REPORTS_DIR / "baseline_comparison.json").write_text(json.dumps(baseline_report, indent=2), encoding="utf-8")

    # 2. Held-out strike fit evaluation
    heldout_raw = json.loads((FIXTURES_DIR / "heldout_smile_chain.json").read_text(encoding="utf-8"))
    train_chain = OptionChainSnapshot.from_dict(heldout_raw["train_snapshot"])
    test_chain = OptionChainSnapshot.from_dict(heldout_raw["heldout_snapshot"])
    train_surface = build_surface(train_chain, config)
    test_iv_quotes, _, _, _, _ = clean_and_invert_quotes(test_chain, config)
    heldout_svi = evaluate_heldout_quotes(train_surface, test_iv_quotes, model="svi")
    heldout_sabr = evaluate_heldout_quotes(train_surface, test_iv_quotes, model="sabr")

    # 3. Edge-cases cleaning & calibration
    edge_chain = load_chain_snapshot(FIXTURES_DIR / "edge_cases_chain.json")
    edge_surface = build_surface(edge_chain, config)

    # 4. Determinism check (fixed seed)
    ref_surface_rerun = build_surface(ref_chain, config)
    deterministic_identical = (
        ref_surface.surface_id == ref_surface_rerun.surface_id
        and ref_surface.snapshot.to_dict() == ref_surface_rerun.snapshot.to_dict()
    )

    # 5. Arbitrage violation & strict mode check
    arb_chain = load_chain_snapshot(FIXTURES_DIR / "arbitrage_violation_chain.json")
    arb_surface = build_surface(arb_chain, config)
    strict_rejected = False
    try:
        arb_surface.query(expiry_years=30.0 / 365.0, log_moneyness=0.0, require_valid_surface=True)
    except ArbitrageViolationError:
        strict_rejected = True

    # 6. Extrapolation rejection check
    strike_extrap_rejected = False
    maturity_extrap_rejected = False
    try:
        ref_surface.query(expiry_years=90.0 / 365.0, log_moneyness=0.60)
    except ExtrapolationError:
        strike_extrap_rejected = True
    try:
        ref_surface.query(expiry_years=2.5, log_moneyness=0.0)
    except ExtrapolationError:
        maturity_extrap_rejected = True

    save_surface_snapshot(ref_surface, REPORTS_DIR / "reference_surface_snapshot.json")

    acceptance = {
        "schema_version": config.schema_version,
        "config_hash": config.content_hash(),
        "seed": config.seed,
        "runtime_metadata": ref_surface.snapshot.diagnostics.runtime_metadata,
        "criteria": {
            "bid_ask_weighted_fit_and_heldout_rmse": {
                "passed": (
                    train_surface.snapshot.diagnostics.overall_svi_weighted_rmse_iv < 0.002
                    and heldout_svi["heldout_rmse_iv"] < 0.002
                ),
                "reference_svi_weighted_rmse_iv": ref_surface.snapshot.diagnostics.overall_svi_weighted_rmse_iv,
                "reference_sabr_weighted_rmse_iv": ref_surface.snapshot.diagnostics.overall_sabr_weighted_rmse_iv,
                "train_svi_weighted_rmse_iv": train_surface.snapshot.diagnostics.overall_svi_weighted_rmse_iv,
                "svi_heldout_rmse_iv": heldout_svi["heldout_rmse_iv"],
                "svi_heldout_weighted_rmse_iv": heldout_svi["heldout_weighted_rmse_iv"],
                "sabr_heldout_rmse_iv": heldout_sabr["heldout_rmse_iv"],
                "sabr_heldout_weighted_rmse_iv": heldout_sabr["heldout_weighted_rmse_iv"],
                "heldout_quote_count": heldout_svi["evaluated_count"],
            },
            "zero_arbitrage_or_marked_invalid": {
                "passed": (
                    ref_surface.is_valid
                    and ref_surface.snapshot.arbitrage_check.total_violations == 0
                    and train_surface.is_valid
                    and train_surface.snapshot.arbitrage_check.total_violations == 0
                    and (not arb_surface.is_valid)
                    and arb_surface.snapshot.arbitrage_check.calendar_violation_count > 0
                    and strict_rejected
                ),
                "reference_valid": ref_surface.is_valid,
                "reference_violations": ref_surface.snapshot.arbitrage_check.total_violations,
                "reference_min_durrleman_density": ref_surface.snapshot.arbitrage_check.min_butterfly_density,
                "reference_min_call_convexity": ref_surface.snapshot.arbitrage_check.min_call_convexity,
                "reference_min_calendar_var_diff": ref_surface.snapshot.arbitrage_check.min_calendar_variance_diff,
                "arb_fixture_marked_invalid": not arb_surface.is_valid,
                "arb_fixture_calendar_violations": arb_surface.snapshot.arbitrage_check.calendar_violation_count,
                "strict_query_rejected_invalid_surface": strict_rejected,
            },
            "deterministic_calibration_fixed_seed": {
                "passed": deterministic_identical,
                "surface_id_run1": ref_surface.surface_id,
                "surface_id_run2": ref_surface_rerun.surface_id,
            },
            "edge_cases_cleaning": {
                "passed": (
                    edge_surface.is_valid
                    and edge_surface.snapshot.diagnostics.rejected_quotes >= 7
                ),
                "accepted_quotes": edge_surface.snapshot.diagnostics.accepted_quotes,
                "rejected_quotes": edge_surface.snapshot.diagnostics.rejected_quotes,
                "rejection_counts": edge_surface.snapshot.diagnostics.rejection_counts,
            },
            "extrapolation_rejection": {
                "passed": strike_extrap_rejected and maturity_extrap_rejected,
                "strike_extrapolation_rejected": strike_extrap_rejected,
                "maturity_extrapolation_rejected": maturity_extrap_rejected,
            },
        },
    }

    (REPORTS_DIR / "acceptance_results.json").write_text(json.dumps(acceptance, indent=2), encoding="utf-8")
    (REPORTS_DIR / "runtime_metadata.json").write_text(
        json.dumps(ref_surface.snapshot.diagnostics.runtime_metadata, indent=2), encoding="utf-8"
    )
    print(json.dumps(acceptance, indent=2))


if __name__ == "__main__":
    main()
