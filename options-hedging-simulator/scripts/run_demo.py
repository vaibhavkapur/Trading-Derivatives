#!/usr/bin/env python3
"""End-to-end reproducible demo and benchmark runner for options-hedging-simulator."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from options_hedging_simulator.analytics import comparison_dataframe, run_zero_cost_convergence, summarize_batch
from options_hedging_simulator.io import load_comparison_config, load_simulation_config, verify_replay
from options_hedging_simulator.reporting import (
    write_batch_report,
    write_comparison_report,
    write_convergence_report,
)
from options_hedging_simulator.simulator import compare_policies, simulate_batch


def main() -> int:
    reports_root = PROJECT_ROOT / "reports"

    # 1. Baseline single-policy GBM run + exact replay verification
    print("[1/4] Running baseline GBM scenario...")
    base_cfg = load_simulation_config(PROJECT_ROOT / "config" / "baseline_gbm.json")
    base_batch = simulate_batch(base_cfg, base_dir=PROJECT_ROOT)
    base_dir = reports_root / "baseline"
    write_batch_report(base_batch, base_dir, generate_plots=True)
    matched, replay_info = verify_replay(base_dir, base_dir=PROJECT_ROOT)
    base_m = summarize_batch(base_batch)
    print(
        f"  run_id={base_batch.run_id} mean_err={base_m.hedge_error_mean:.2f} "
        f"std={base_m.hedge_error_std:.2f} mean_cost={base_m.mean_total_cost:.2f} "
        f"ledger_residual={base_m.max_abs_ledger_residual} replay_ok={matched} ({replay_info['total_fills']} fills)"
    )

    # 2. Holdout GBM multi-policy comparison
    print("\n[2/4] Running holdout GBM policy comparison (500 paths, seed=900101)...")
    gbm_cmp_cfg = load_comparison_config(PROJECT_ROOT / "config" / "holdout_comparison.json")
    gbm_batches = compare_policies(gbm_cmp_cfg, base_dir=PROJECT_ROOT)
    write_comparison_report(gbm_cmp_cfg, gbm_batches, reports_root / "holdout_gbm", generate_plots=True)
    gbm_df = comparison_dataframe(gbm_batches)
    print(
        gbm_df[
            [
                "policy_name",
                "hedge_error_mean",
                "hedge_error_std",
                "hedge_error_p05",
                "hedge_error_p95",
                "mean_turnover_multiple",
                "mean_total_cost",
                "max_abs_ledger_residual",
            ]
        ].to_string(index=False)
    )

    # 3. Holdout Stressed Merton Jump + Gap Shock comparison (physical settlement)
    print("\n[3/4] Running holdout stressed jump + gap-shock comparison (500 paths, seed=900202)...")
    jump_cmp_cfg = load_comparison_config(PROJECT_ROOT / "config" / "stress_jump_comparison.json")
    jump_batches = compare_policies(jump_cmp_cfg, base_dir=PROJECT_ROOT)
    write_comparison_report(jump_cmp_cfg, jump_batches, reports_root / "holdout_stress_jump", generate_plots=True)
    jump_df = comparison_dataframe(jump_batches)
    print(
        jump_df[
            [
                "policy_name",
                "hedge_error_mean",
                "hedge_error_std",
                "hedge_error_p05",
                "hedge_error_cvar05",
                "mean_total_cost",
                "total_gap_shock_events",
                "max_abs_ledger_residual",
            ]
        ].to_string(index=False)
    )

    # 4. Zero-cost convergence study as rebalancing interval shrinks
    print("\n[4/4] Running zero-cost convergence study (N = 12, 24, 63, 126, 252)...")
    conv_cfg = load_simulation_config(PROJECT_ROOT / "config" / "convergence_study.json")
    conv_report = run_zero_cost_convergence(conv_cfg, [12, 24, 63, 126, 252])
    write_convergence_report(conv_report, reports_root / "convergence", generate_plots=True)
    print(
        f"  empirical_loglog_slope={conv_report.empirical_loglog_slope_vs_steps:.4f} "
        f"(theoretical=-0.5000), monotone_reduction={conv_report.monotone_std_reduction}"
    )
    for pt in conv_report.points:
        print(
            f"    N={pt.num_steps:3d}  dt={pt.dt_years:.5f}  std={pt.hedge_error_std:8.2f}  "
            f"std%prem={pt.hedge_error_std_pct_premium:6.2f}%  residual={pt.max_abs_ledger_residual}"
        )

    print(f"\nAll reports saved under: {reports_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
