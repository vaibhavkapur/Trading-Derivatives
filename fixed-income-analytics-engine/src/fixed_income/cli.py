"""Command-line interface for reproducible calibration, pricing, scenarios, and replay."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import SCHEMA_VERSION
from .curve import bootstrap_curve
from .diagnostics import DiagnosticLog, compute_logical_fingerprint, runtime_metadata
from .errors import FixedIncomeError
from .io import load_json_file, load_market_fixture, write_json_report
from .pricing import price_bond_on_curve
from .scenarios import default_scenarios, run_scenario_suite


def execute_market_run(
    fixture_path: Path,
    *,
    run_label: str = "demo",
) -> Dict[str, Any]:
    """Execute full calibration -> pricing -> scenario attribution pipeline on a fixture."""
    diag = DiagnosticLog(run_label=run_label)
    diag.emit("INFO", "pipeline.start", fixture=fixture_path.name)

    config, deposits, bond_quotes, target_bonds, fixture_scenarios = load_market_fixture(fixture_path)
    diag.increment("inputs.deposits", len(deposits))
    diag.increment("inputs.calibration_bonds", len(bond_quotes))
    diag.increment("inputs.portfolio_bonds", len(target_bonds))

    curve = bootstrap_curve(
        valuation_date=config.valuation_date,
        deposits=deposits,
        bond_quotes=bond_quotes,
        currency=config.currency,
        curve_day_count=config.curve_day_count,
        interpolation=config.interpolation,
        solver_tolerance=config.solver_tolerance,
        solver_max_iterations=config.solver_max_iterations,
        repricing_tolerance_bp=config.repricing_tolerance_bp,
        allow_negative_zero_rates=config.allow_negative_zero_rates,
    )
    diag.increment("calibration.nodes", len(curve.nodes))
    diag.emit(
        "INFO",
        "curve.bootstrapped",
        curve_id=curve.curve_id,
        node_count=len(curve.nodes),
        max_error_bp=round(curve.max_calibration_error_bp(), 10),
    )

    pricing_results = []
    for bond in target_bonds:
        res = price_bond_on_curve(
            bond,
            curve,
            bump_bp=config.dv01_bump_bp,
            tol=config.solver_tolerance,
            max_iter=config.solver_max_iterations,
        )
        pricing_results.append(res)
        diag.increment("pricing.bonds_priced")
        diag.increment("pricing.solver_iterations", res.solver_iterations)
        diag.emit(
            "INFO",
            "bond.priced",
            bond_id=bond.bond_id,
            dirty_pct=round(res.dirty_price_pct_par, 6),
            clean_pct=round(res.clean_price_pct_par, 6),
            ytm_pct=round(res.ytm * 100.0, 6),
            dv01_diff_pct=round(res.dv01_relative_diff_pct, 8),
        )

    scenarios = fixture_scenarios if fixture_scenarios else default_scenarios()
    scenario_results = run_scenario_suite(
        target_bonds,
        curve,
        scenarios,
        tol=config.solver_tolerance,
        max_iter=config.solver_max_iterations,
    )
    diag.increment("scenarios.evaluations", len(scenario_results))
    diag.emit("INFO", "pipeline.complete", scenario_evaluations=len(scenario_results))

    payload: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "fixture_name": fixture_path.name,
        "config": config.to_dict(),
        "curve": curve.to_dict(),
        "pricing_results": [r.to_dict() for r in pricing_results],
        "scenario_results": [s.to_dict() for s in scenario_results],
    }
    payload["logical_fingerprint"] = compute_logical_fingerprint(payload)
    payload["diagnostics"] = diag.to_dict()
    payload["runtime_metadata"] = runtime_metadata()
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fi-analytics",
        description="Fixed Income Analytics Engine CLI (offline research prototype)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run", help="Run calibration, pricing, and scenario suite on a fixture")
    run_cmd.add_argument("--fixture", type=Path, required=True, help="Path to market fixture JSON")
    run_cmd.add_argument("--output", type=Path, default=None, help="Optional output JSON report path")
    run_cmd.add_argument("--label", type=str, default="cli_run", help="Run label for structured logs")

    replay_cmd = sub.add_parser(
        "replay", help="Rerun a fixture and verify logical fingerprint matches a baseline report"
    )
    replay_cmd.add_argument("--fixture", type=Path, required=True, help="Path to market fixture JSON")
    replay_cmd.add_argument("--baseline", type=Path, required=True, help="Path to previous report JSON")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "run":
            result = execute_market_run(args.fixture, run_label=args.label)
            if args.output is not None:
                write_json_report(args.output, result)
            summary = {
                "status": "OK",
                "curve_id": result["curve"]["curve_id"],
                "logical_fingerprint": result["logical_fingerprint"],
                "max_calibration_error_bp": result["curve"]["max_calibration_error_bp"],
                "bonds_priced": len(result["pricing_results"]),
                "scenario_evaluations": len(result["scenario_results"]),
                "output_path": str(args.output) if args.output else None,
            }
            print(json.dumps(summary, indent=2))
            return 0

        if args.command == "replay":
            baseline = load_json_file(args.baseline, label="baseline report")
            rerun = execute_market_run(args.fixture, run_label="replay")
            base_fp = baseline.get("logical_fingerprint")
            rerun_fp = rerun["logical_fingerprint"]
            matched = base_fp == rerun_fp
            report = {
                "status": "MATCH" if matched else "MISMATCH",
                "baseline_fingerprint": base_fp,
                "rerun_fingerprint": rerun_fp,
                "matched": matched,
            }
            print(json.dumps(report, indent=2))
            return 0 if matched else 2

        return 1

    except FixedIncomeError as exc:
        print(json.dumps({"status": "ERROR", **exc.to_dict()}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
