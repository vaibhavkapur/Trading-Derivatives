#!/usr/bin/env python3
"""Reproducible end-to-end demo script for the Volatility Surface Engine."""

from __future__ import annotations

import json
from pathlib import Path

from volatility_surface_engine import (
    ExtrapolationError,
    OptionChainSnapshot,
    build_surface,
    clean_and_invert_quotes,
    evaluate_heldout_quotes,
    export_surface_html,
    load_chain_snapshot,
    load_config,
    load_surface_snapshot,
    save_surface_snapshot,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "default_config.json"
FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"
REPORTS_DIR = PROJECT_ROOT / "reports"


def main() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    config = load_config(CONFIG_PATH)

    # 1. Calibrate reference surface
    ref_chain = load_chain_snapshot(FIXTURES_DIR / "valid_chain_reference.json")
    surface = build_surface(ref_chain, config)

    # Save versioned snapshot and verify replay determinism
    snap_path = save_surface_snapshot(surface, REPORTS_DIR / "demo_snapshot.json")
    reloaded = load_surface_snapshot(snap_path)
    rerun = build_surface(ref_chain, config)
    deterministic_match = (
        surface.surface_id == rerun.surface_id == reloaded.surface_id
        and surface.is_valid == rerun.is_valid
    )

    # Export interactive Plotly 3D HTML surface
    html_path = export_surface_html(surface, REPORTS_DIR / "demo_surface.html")

    # 2. Sample in-domain queries (including interpolated maturity) & verify extrapolation rejection
    in_domain_query = surface.query(expiry_years=60.0 / 365.0, log_moneyness=0.0, model="svi")
    extrap_rejected = False
    try:
        surface.query(expiry_years=60.0 / 365.0, log_moneyness=0.80, model="svi")
    except ExtrapolationError:
        extrap_rejected = True

    # 3. Held-out strike evaluation
    heldout_raw = json.loads((FIXTURES_DIR / "heldout_smile_chain.json").read_text(encoding="utf-8"))
    train_chain = OptionChainSnapshot.from_dict(heldout_raw["train_snapshot"])
    test_chain = OptionChainSnapshot.from_dict(heldout_raw["heldout_snapshot"])
    train_surface = build_surface(train_chain, config)
    test_iv_quotes, _, _, _, _ = clean_and_invert_quotes(test_chain, config)
    heldout_svi = evaluate_heldout_quotes(train_surface, test_iv_quotes, model="svi")
    heldout_sabr = evaluate_heldout_quotes(train_surface, test_iv_quotes, model="sabr")

    # 4. Arbitrage violation detection demo
    arb_chain = load_chain_snapshot(FIXTURES_DIR / "arbitrage_violation_chain.json")
    arb_surface = build_surface(arb_chain, config)

    demo_summary = {
        "reference_surface": {
            "surface_id": surface.surface_id,
            "is_valid": surface.is_valid,
            "deterministic_replay_match": deterministic_match,
            "accepted_quotes": surface.snapshot.diagnostics.accepted_quotes,
            "rejected_quotes": surface.snapshot.diagnostics.rejected_quotes,
            "overall_svi_weighted_rmse_iv": surface.snapshot.diagnostics.overall_svi_weighted_rmse_iv,
            "overall_sabr_weighted_rmse_iv": surface.snapshot.diagnostics.overall_sabr_weighted_rmse_iv,
            "butterfly_violations": surface.snapshot.arbitrage_check.butterfly_violation_count,
            "calendar_violations": surface.snapshot.arbitrage_check.calendar_violation_count,
            "snapshot_path": str(snap_path.relative_to(PROJECT_ROOT)),
            "html_path": str(html_path.relative_to(PROJECT_ROOT)),
        },
        "interpolated_query_60d_atm": in_domain_query.to_dict(),
        "extrapolation_rejected_by_default": extrap_rejected,
        "heldout_evaluation": {
            "train_surface_id": train_surface.surface_id,
            "train_surface_valid": train_surface.is_valid,
            "train_svi_weighted_rmse_iv": train_surface.snapshot.diagnostics.overall_svi_weighted_rmse_iv,
            "svi_heldout_rmse_iv": heldout_svi["heldout_rmse_iv"],
            "svi_heldout_weighted_rmse_iv": heldout_svi["heldout_weighted_rmse_iv"],
            "sabr_heldout_rmse_iv": heldout_sabr["heldout_rmse_iv"],
            "sabr_heldout_weighted_rmse_iv": heldout_sabr["heldout_weighted_rmse_iv"],
            "evaluated_count": heldout_svi["evaluated_count"],
        },
        "arbitrage_violation_scenario": {
            "surface_id": arb_surface.surface_id,
            "is_valid": arb_surface.is_valid,
            "calendar_violations": arb_surface.snapshot.arbitrage_check.calendar_violation_count,
            "butterfly_violations": arb_surface.snapshot.arbitrage_check.butterfly_violation_count,
            "min_calendar_variance_diff": arb_surface.snapshot.arbitrage_check.min_calendar_variance_diff,
        },
        "runtime_metadata": surface.snapshot.diagnostics.runtime_metadata,
        "config_hash": surface.snapshot.config_hash,
    }

    out_path = REPORTS_DIR / "demo_results.json"
    out_path.write_text(json.dumps(demo_summary, indent=2), encoding="utf-8")
    print(json.dumps(demo_summary, indent=2))


if __name__ == "__main__":
    main()
