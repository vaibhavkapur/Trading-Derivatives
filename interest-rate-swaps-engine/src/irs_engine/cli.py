"""Command-line interface for Interest Rate Swaps Engine."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from irs_engine.diagnostics import collect_runtime_metadata
from irs_engine.domain.calendars import CalendarRegistry
from irs_engine.domain.errors import IRSEngineError
from irs_engine.domain.oracle import check_optional_quantlib_oracle, run_independent_analytic_oracle
from irs_engine.domain.pricer import price_swap
from irs_engine.domain.scenarios import run_deterministic_scenarios, run_stochastic_scenarios
from irs_engine.domain.schedule import generate_swap_schedules
from irs_engine.domain.sensitivities import compute_sensitivities
from irs_engine.io import load_valuation_bundle, to_plain_dict


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="irs-engine",
        description="Dual-curve vanilla interest rate swaps valuation, sensitivity, and scenario CLI.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for cmd in ("price", "schedule", "sensitivities", "oracle"):
        p = sub.add_parser(cmd, help=f"Run {cmd} on a valuation JSON bundle.")
        p.add_argument("bundle", type=Path, help="Path to valuation bundle JSON file.")
        p.add_argument("--calendars", type=Path, default=None, help="Optional calendars JSON path.")
        p.add_argument("--output", type=Path, default=None, help="Optional output JSON file path.")

    p_scen = sub.add_parser("scenarios", help="Run deterministic and stochastic scenarios.")
    p_scen.add_argument("bundle", type=Path, help="Path to valuation bundle JSON file.")
    p_scen.add_argument(
        "--scenarios-config",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "config" / "scenarios.json",
        help="Path to scenarios config JSON.",
    )
    p_scen.add_argument("--calendars", type=Path, default=None, help="Optional calendars JSON path.")
    p_scen.add_argument("--output", type=Path, default=None, help="Optional output JSON file path.")

    p_serve = sub.add_parser("serve", help="Run the FastAPI pricing server via Uvicorn.")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "serve":
        import uvicorn

        uvicorn.run("irs_engine.api.app:app", host=args.host, port=args.port, reload=False)
        return 0

    try:
        calendars = (
            CalendarRegistry.from_json_file(args.calendars)
            if args.calendars is not None
            else CalendarRegistry.default()
        )
        trade, disc, fwd, fixings, cfg, _meta = load_valuation_bundle(args.bundle)

        if args.command == "schedule":
            fixed_s, float_s = generate_swap_schedules(
                trade,
                calendars.get(trade.fixed_leg.calendar),
                calendars.get(trade.floating_leg.calendar),
            )
            payload = {
                "trade_id": trade.trade_id,
                "fixed_schedule": to_plain_dict(fixed_s),
                "floating_schedule": to_plain_dict(float_s),
            }
        elif args.command == "price":
            res = price_swap(
                trade, disc, fwd, fixing_history=fixings, calendar_registry=calendars, config=cfg
            )
            payload = to_plain_dict(res)
        elif args.command == "sensitivities":
            res = compute_sensitivities(
                trade, disc, fwd, fixing_history=fixings, calendar_registry=calendars, config=cfg
            )
            payload = to_plain_dict(res)
        elif args.command == "oracle":
            analytic = run_independent_analytic_oracle(
                trade, disc, fwd, fixing_history=fixings, calendar_registry=calendars, config=cfg
            )
            ql = check_optional_quantlib_oracle(trade, disc, fwd)
            payload = {
                "analytic_oracle": to_plain_dict(analytic),
                "quantlib_oracle": ql,
                "runtime": collect_runtime_metadata(),
            }
        elif args.command == "scenarios":
            scen_raw = json.loads(args.scenarios_config.read_text(encoding="utf-8"))
            det = run_deterministic_scenarios(
                trade,
                disc,
                fwd,
                scen_raw.get("deterministic_scenarios", []),
                fixing_history=fixings,
                calendar_registry=calendars,
                config=cfg,
            )
            sc = scen_raw.get("stochastic_config", {})
            stoch = run_stochastic_scenarios(
                trade,
                disc,
                fwd,
                seed=int(sc.get("seed", 42)),
                num_paths=int(sc.get("num_paths", 250)),
                level_vol_bps=float(sc.get("level_vol_bps", 65.0)),
                slope_vol_bps=float(sc.get("slope_vol_bps", 30.0)),
                curvature_vol_bps=float(sc.get("curvature_vol_bps", 15.0)),
                basis_vol_bps=float(sc.get("basis_vol_bps", 8.0)),
                fixing_history=fixings,
                calendar_registry=calendars,
                config=cfg,
            )
            payload = {
                "trade_id": trade.trade_id,
                "deterministic_scenarios": to_plain_dict(det),
                "stochastic_summary": to_plain_dict(stoch),
                "runtime": collect_runtime_metadata(),
            }
        else:
            parser.error(f"Unknown command: {args.command}")
            return 2

        rendered = json.dumps(payload, indent=2, sort_keys=True)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        else:
            print(rendered)
        return 0
    except IRSEngineError as exc:
        print(json.dumps(exc.to_dict(), indent=2, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
