"""Command-line interface for options_hedging_simulator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import List, Optional

from .analytics import comparison_dataframe, run_zero_cost_convergence, summarize_batch
from .errors import HedgingSimulatorError
from .io import load_comparison_config, load_simulation_config, verify_replay
from .reporting import write_batch_report, write_comparison_report, write_convergence_report
from .simulator import compare_policies, simulate_batch


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="options-hedging-sim",
        description="Discrete options delta-hedging simulator with exact ledger reconciliation.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_val = sub.add_parser("validate", help="Validate a single or comparison JSON config.")
    p_val.add_argument("--config", required=True, help="Path to JSON config file.")
    p_val.add_argument("--comparison", action="store_true", help="Validate as a multi-policy ComparisonConfig.")

    p_run = sub.add_parser("run", help="Run a single-policy hedging simulation.")
    p_run.add_argument("--config", required=True, help="Path to SimulationConfig JSON.")
    p_run.add_argument("--output-dir", required=True, help="Directory for output reports.")
    p_run.add_argument("--no-plots", action="store_true", help="Disable Matplotlib PNG generation.")

    p_cmp = sub.add_parser("compare", help="Compare multiple policies across shared seeded paths.")
    p_cmp.add_argument("--config", required=True, help="Path to ComparisonConfig JSON.")
    p_cmp.add_argument("--output-dir", required=True, help="Directory for comparison outputs.")
    p_cmp.add_argument("--no-plots", action="store_true", help="Disable Matplotlib PNG generation.")

    p_conv = sub.add_parser("convergence", help="Run zero-cost convergence study as dt shrinks.")
    p_conv.add_argument("--config", required=True, help="Path to base SimulationConfig JSON.")
    p_conv.add_argument(
        "--steps",
        default="12,24,63,126,252",
        help="Comma-separated list of rebalancing step counts (default: 12,24,63,126,252).",
    )
    p_conv.add_argument("--output-dir", required=True, help="Directory for convergence report.")
    p_conv.add_argument("--no-plots", action="store_true", help="Disable Matplotlib PNG generation.")

    p_rep = sub.add_parser("replay", help="Replay a previous run directory and verify exact fill identity.")
    p_rep.add_argument("--run-dir", required=True, help="Directory containing config.json and summary.json.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "validate":
            if args.comparison:
                cfg = load_comparison_config(args.config)
                print(
                    json.dumps(
                        {
                            "status": "valid",
                            "kind": "comparison",
                            "scenario_name": cfg.scenario_name,
                            "config_hash": cfg.config_hash(),
                            "policies": [p.name for p in cfg.policies],
                        },
                        indent=2,
                    )
                )
            else:
                cfg = load_simulation_config(args.config)
                print(
                    json.dumps(
                        {
                            "status": "valid",
                            "kind": "simulation",
                            "scenario_name": cfg.scenario_name,
                            "config_hash": cfg.config_hash(),
                            "policy": cfg.policy.name,
                        },
                        indent=2,
                    )
                )
            return 0

        if args.command == "run":
            cfg_path = Path(args.config).resolve()
            cfg = load_simulation_config(cfg_path)
            batch = simulate_batch(cfg, base_dir=cfg_path.parent.parent)
            artifacts = write_batch_report(batch, args.output_dir, generate_plots=not args.no_plots)
            metrics = summarize_batch(batch)
            print(
                json.dumps(
                    {
                        "run_id": batch.run_id,
                        "fills_digest": batch.fills_digest,
                        "hedge_error_mean": round(metrics.hedge_error_mean, 4),
                        "hedge_error_std": round(metrics.hedge_error_std, 4),
                        "mean_total_cost": round(metrics.mean_total_cost, 4),
                        "max_abs_ledger_residual": metrics.max_abs_ledger_residual,
                        "artifacts": {k: str(v) for k, v in artifacts.items()},
                    },
                    indent=2,
                )
            )
            return 0

        if args.command == "compare":
            cfg_path = Path(args.config).resolve()
            cmp_cfg = load_comparison_config(cfg_path)
            batches = compare_policies(cmp_cfg, base_dir=cfg_path.parent.parent)
            artifacts = write_comparison_report(cmp_cfg, batches, args.output_dir, generate_plots=not args.no_plots)
            df = comparison_dataframe(batches)
            cols = [
                "policy_name",
                "hedge_error_mean",
                "hedge_error_std",
                "hedge_error_p05",
                "hedge_error_p95",
                "mean_turnover_multiple",
                "mean_total_cost",
                "max_abs_ledger_residual",
            ]
            print(df[cols].to_string(index=False))
            print(f"\nSaved comparison outputs to: {Path(args.output_dir).resolve()}")
            print(f"Artifacts: {', '.join(str(v.name) for v in artifacts.values())}")
            return 0

        if args.command == "convergence":
            cfg_path = Path(args.config).resolve()
            cfg = load_simulation_config(cfg_path)
            step_counts = [int(x.strip()) for x in args.steps.split(",") if x.strip()]
            report = run_zero_cost_convergence(cfg, step_counts)
            artifacts = write_convergence_report(report, args.output_dir, generate_plots=not args.no_plots)
            print(
                json.dumps(
                    {
                        "scenario_name": report.scenario_name,
                        "empirical_loglog_slope_vs_steps": round(report.empirical_loglog_slope_vs_steps, 4),
                        "theoretical_slope_vs_steps": report.theoretical_slope_vs_steps,
                        "monotone_std_reduction": report.monotone_std_reduction,
                        "artifacts": {k: str(v) for k, v in artifacts.items()},
                    },
                    indent=2,
                )
            )
            return 0

        if args.command == "replay":
            run_dir = Path(args.run_dir).resolve()
            _, info = verify_replay(run_dir)
            print(json.dumps(info, indent=2))
            return 0

        parser.error(f"Unknown command: {args.command}")
        return 2
    except HedgingSimulatorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
