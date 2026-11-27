"""Command-line interface for the Volatility Surface Engine."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from volatility_surface_engine.models import CalibrationConfig, VSEError
from volatility_surface_engine.surface import (
    build_surface,
    load_surface_snapshot,
    save_surface_snapshot,
)
from volatility_surface_engine.validation import load_chain_snapshot, load_config
from volatility_surface_engine.viz import export_surface_html


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vse",
        description="Volatility Surface Engine — arbitrage-aware SVI/SABR implied-volatility surface CLI.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # calibrate
    p_cal = sub.add_parser("calibrate", help="Calibrate a volatility surface from an option chain JSON file.")
    p_cal.add_argument("--input", required=True, help="Path to input OptionChainSnapshot JSON file.")
    p_cal.add_argument("--config", default=None, help="Optional path to CalibrationConfig JSON file.")
    p_cal.add_argument("--output", default=None, help="Optional path to save versioned SurfaceSnapshot JSON.")
    p_cal.add_argument("--html", default=None, help="Optional path to export interactive Plotly 3D HTML.")
    p_cal.add_argument("--seed", type=int, default=None, help="Optional seed override for deterministic calibration.")

    # query
    p_q = sub.add_parser("query", help="Query implied volatility from a saved SurfaceSnapshot JSON file.")
    p_q.add_argument("--snapshot", required=True, help="Path to saved SurfaceSnapshot JSON file.")
    p_q.add_argument("--expiry-years", type=float, required=True, help="Target time to expiry in years.")
    grp = p_q.add_mutually_exclusive_group(required=True)
    grp.add_argument("--strike", type=float, help="Target strike price.")
    grp.add_argument("--log-moneyness", type=float, help="Target log-moneyness k = ln(K/F).")
    p_q.add_argument("--model", choices=["svi", "sabr"], default="svi", help="Model to query (default: svi).")
    p_q.add_argument(
        "--allow-extrapolation",
        action="store_true",
        help="Explicitly allow out-of-domain extrapolation (rejected by default).",
    )
    p_q.add_argument(
        "--require-valid",
        action="store_true",
        help="Reject queries if the surface is marked invalid due to static arbitrage.",
    )

    # validate
    p_val = sub.add_parser("validate", help="Validate an option chain JSON file against data contracts.")
    p_val.add_argument("--input", required=True, help="Path to input OptionChainSnapshot JSON file.")

    # replay
    p_rep = sub.add_parser("replay", help="Re-run calibration and verify exact determinism against a saved snapshot.")
    p_rep.add_argument("--input", required=True, help="Path to input OptionChainSnapshot JSON file.")
    p_rep.add_argument("--snapshot", required=True, help="Path to previously saved SurfaceSnapshot JSON file.")
    p_rep.add_argument("--config", default=None, help="Optional path to CalibrationConfig JSON file.")

    return parser


def _load_effective_config(config_path: Optional[str], seed_override: Optional[int] = None) -> CalibrationConfig:
    if config_path is not None:
        cfg = load_config(config_path)
    else:
        cfg = CalibrationConfig()
    if seed_override is not None:
        from dataclasses import replace

        cfg = replace(cfg, seed=seed_override)
    return cfg


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "validate":
            snap = load_chain_snapshot(args.input)
            out = {
                "status": "valid",
                "snapshot_id": snap.snapshot_id,
                "underlying": snap.underlying,
                "expiries": len(snap.expiries),
                "quotes": len(snap.quotes),
                "schema_version": snap.schema_version,
            }
            print(json.dumps(out, indent=2))
            return 0

        if args.command == "calibrate":
            cfg = _load_effective_config(args.config, args.seed)
            chain = load_chain_snapshot(args.input)
            surface = build_surface(chain, cfg)
            if args.output:
                save_surface_snapshot(surface, args.output)
            if args.html:
                export_surface_html(surface, args.html)
            summary = {
                "surface_id": surface.surface_id,
                "input_snapshot_id": surface.snapshot.input_snapshot_id,
                "underlying": surface.snapshot.underlying,
                "is_valid": surface.is_valid,
                "slices_calibrated": len(surface.slices),
                "accepted_quotes": surface.snapshot.diagnostics.accepted_quotes,
                "rejected_quotes": surface.snapshot.diagnostics.rejected_quotes,
                "rejection_counts": surface.snapshot.diagnostics.rejection_counts,
                "overall_svi_weighted_rmse_iv": surface.snapshot.diagnostics.overall_svi_weighted_rmse_iv,
                "overall_sabr_weighted_rmse_iv": surface.snapshot.diagnostics.overall_sabr_weighted_rmse_iv,
                "butterfly_violations": surface.snapshot.arbitrage_check.butterfly_violation_count,
                "calendar_violations": surface.snapshot.arbitrage_check.calendar_violation_count,
                "output_path": str(Path(args.output)) if args.output else None,
                "html_path": str(Path(args.html)) if args.html else None,
            }
            print(json.dumps(summary, indent=2))
            return 0

        if args.command == "query":
            surface = load_surface_snapshot(args.snapshot)
            res = surface.query(
                expiry_years=args.expiry_years,
                strike=args.strike,
                log_moneyness=args.log_moneyness,
                model=args.model,
                allow_extrapolation=args.allow_extrapolation,
                require_valid_surface=args.require_valid,
            )
            print(json.dumps(res.to_dict(), indent=2))
            return 0

        if args.command == "replay":
            cfg = _load_effective_config(args.config)
            chain = load_chain_snapshot(args.input)
            prior = load_surface_snapshot(args.snapshot)
            rerun = build_surface(chain, cfg)
            matches = (
                prior.surface_id == rerun.surface_id
                and prior.is_valid == rerun.is_valid
                and prior.snapshot.config_hash == rerun.snapshot.config_hash
            )
            out = {
                "replay_match": matches,
                "prior_surface_id": prior.surface_id,
                "rerun_surface_id": rerun.surface_id,
                "prior_config_hash": prior.snapshot.config_hash,
                "rerun_config_hash": rerun.snapshot.config_hash,
            }
            print(json.dumps(out, indent=2))
            return 0 if matches else 3

    except VSEError as exc:
        err_payload = {"error": exc.code, "message": exc.message, "details": exc.details}
        print(json.dumps(err_payload, indent=2), file=sys.stderr)
        return 2
    except Exception as exc:
        err_payload = {"error": "UNEXPECTED_ERROR", "message": str(exc)}
        print(json.dumps(err_payload, indent=2), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
