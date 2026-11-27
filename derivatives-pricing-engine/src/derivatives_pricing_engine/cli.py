"""Command-line interface for pricing, demos, replay, and benchmarks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from .analytics.binomial import binomial_convergence_study
from .analytics.day_count import year_fraction
from .analytics.monte_carlo import monte_carlo_coverage_study, price_monte_carlo
from .analytics.pde_fd import pde_convergence_study
from .config import load_config
from .contracts import ExerciseStyle, MarketSnapshot, OptionContract, OptionType, PricingMethod, PricingRequest
from .errors import PricingEngineError
from .io import collect_runtime_environment, read_json, utc_now_iso, write_json
from .service import PricingEngine


def _build_demo_payload(engine: PricingEngine) -> dict[str, object]:
    """Run deterministic European & American end-to-end pricing scenarios."""
    euro_call = OptionContract(
        contract_id="DEMO-EUR-CALL-100",
        underlying="SYNTH-EQ-01",
        currency="USD",
        option_type=OptionType.CALL,
        exercise_style=ExerciseStyle.EUROPEAN,
        strike=100.0,
        expiry_timestamp="2027-01-15T16:00:00Z",
        contract_multiplier=100.0,
    )
    amer_put = OptionContract(
        contract_id="DEMO-AMR-PUT-100",
        underlying="SYNTH-EQ-01",
        currency="USD",
        option_type=OptionType.PUT,
        exercise_style=ExerciseStyle.AMERICAN,
        strike=100.0,
        expiry_timestamp="2027-01-15T16:00:00Z",
        contract_multiplier=100.0,
    )
    market_div = MarketSnapshot(
        snapshot_id="SNAP-DEMO-DIV-01",
        valuation_timestamp="2026-01-15T16:00:00Z",
        spot=100.0,
        risk_free_rate=0.05,
        dividend_yield=0.02,
        volatility=0.20,
    )
    market_nodiv = MarketSnapshot(
        snapshot_id="SNAP-DEMO-NODIV-01",
        valuation_timestamp="2026-01-15T16:00:00Z",
        spot=100.0,
        risk_free_rate=0.05,
        dividend_yield=0.0,
        volatility=0.20,
    )

    euro_comparison = engine.compare_methods(euro_call, market_div)
    amer_comparison = engine.compare_methods(amer_put, market_nodiv)

    return {
        "generated_at_utc": utc_now_iso(),
        "environment": collect_runtime_environment(),
        "config_version": engine.config.config_version,
        "scenarios": {
            "european_call_cross_method": euro_comparison,
            "american_put_vs_european": amer_comparison,
        },
        "engine_metrics": engine.metrics.snapshot(),
    }


def _run_benchmarks(engine: PricingEngine, output_dir: Path) -> dict[str, str]:
    """Run convergence and Monte Carlo CI coverage benchmarks and write raw JSON artifacts."""
    cfg_bench = engine.config.benchmarks
    bin_steps = [int(s) for s in cfg_bench["binomial_resolutions"]]
    pde_res = [(int(r["spot_steps"]), int(r["time_steps"])) for r in cfg_bench["pde_resolutions"]]
    mc_paths_sweep = [int(p) for p in cfg_bench["mc_paths_sweep"]]

    # Reference 1-year ATM option (S=100, K=100, r=0.05, q=0.0, sigma=0.20, T=1.0)
    spot, strike, rate, div_y, vol, tau = 100.0, 100.0, 0.05, 0.0, 0.20, 1.0

    euro_bin = binomial_convergence_study(
        spot=spot, strike=strike, time_to_maturity=tau, risk_free_rate=rate,
        dividend_yield=div_y, volatility=vol, option_type=OptionType.PUT,
        exercise_style=ExerciseStyle.EUROPEAN, step_counts=bin_steps,
    )
    amer_bin = binomial_convergence_study(
        spot=spot, strike=strike, time_to_maturity=tau, risk_free_rate=rate,
        dividend_yield=div_y, volatility=vol, option_type=OptionType.PUT,
        exercise_style=ExerciseStyle.AMERICAN, step_counts=bin_steps,
    )
    euro_pde = pde_convergence_study(
        spot=spot, strike=strike, time_to_maturity=tau, risk_free_rate=rate,
        dividend_yield=div_y, volatility=vol, option_type=OptionType.PUT,
        exercise_style=ExerciseStyle.EUROPEAN, resolutions=pde_res, scheme="crank_nicolson",
    )
    amer_pde = pde_convergence_study(
        spot=spot, strike=strike, time_to_maturity=tau, risk_free_rate=rate,
        dividend_yield=div_y, volatility=vol, option_type=OptionType.PUT,
        exercise_style=ExerciseStyle.AMERICAN, resolutions=pde_res, scheme="crank_nicolson",
    )

    convergence_payload = {
        "generated_at_utc": utc_now_iso(),
        "environment": collect_runtime_environment(),
        "config_version": engine.config.config_version,
        "parameters": {
            "spot": spot,
            "strike": strike,
            "risk_free_rate": rate,
            "dividend_yield": div_y,
            "volatility": vol,
            "time_to_maturity": tau,
            "option_type": "put",
        },
        "studies": {
            "european_put_binomial": euro_bin,
            "american_put_binomial": amer_bin,
            "european_put_pde_crank_nicolson": euro_pde,
            "american_put_pde_crank_nicolson": amer_pde,
        },
    }

    # Monte Carlo sweep + coverage study
    mc_sweep_rows = []
    for n_paths in mc_paths_sweep:
        mc_out = price_monte_carlo(
            spot=spot, strike=strike, time_to_maturity=tau, risk_free_rate=rate,
            dividend_yield=0.02, volatility=vol, option_type=OptionType.CALL,
            paths=n_paths, seed=42, antithetic=True, confidence_level=0.95,
        )
        mc_sweep_rows.append(
            {
                "paths": mc_out.paths,
                "seed": mc_out.seed,
                "price": mc_out.price,
                "standard_error": mc_out.standard_error,
                "ci_low": mc_out.ci_low,
                "ci_high": mc_out.ci_high,
                "ci_half_width": mc_out.ci_half_width,
                "variance_reduction_ratio": mc_out.variance_reduction_ratio,
                "runtime_ms": mc_out.runtime_ms,
            }
        )

    coverage = monte_carlo_coverage_study(
        spot=spot, strike=strike, time_to_maturity=tau, risk_free_rate=rate,
        dividend_yield=0.02, volatility=vol, option_type=OptionType.CALL,
        trials=int(cfg_bench["mc_coverage_trials"]),
        paths_per_trial=int(cfg_bench["mc_coverage_paths"]),
        seed_base=int(cfg_bench["mc_coverage_seed_base"]),
        antithetic=True,
        confidence_level=0.95,
    )

    mc_payload = {
        "generated_at_utc": utc_now_iso(),
        "environment": collect_runtime_environment(),
        "config_version": engine.config.config_version,
        "parameters": {
            "spot": spot,
            "strike": strike,
            "risk_free_rate": rate,
            "dividend_yield": 0.02,
            "volatility": vol,
            "time_to_maturity": tau,
            "option_type": "call",
        },
        "path_sweep": mc_sweep_rows,
        "coverage_study": coverage,
    }

    demo_payload = _build_demo_payload(engine)

    demo_path = write_json(output_dir / "baseline_demo.json", demo_payload)
    conv_path = write_json(output_dir / "convergence_report.json", convergence_payload)
    mc_path = write_json(output_dir / "mc_coverage_report.json", mc_payload)

    return {
        "baseline_demo": str(demo_path),
        "convergence_report": str(conv_path),
        "mc_coverage_report": str(mc_path),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="dpe", description="Derivatives Pricing Engine CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_price = sub.add_parser("price", help="Price a single request JSON file")
    p_price.add_argument("--input", required=True, help="Path to PricingRequest JSON file")
    p_price.add_argument("--config", default=None, help="Optional path to engine config JSON")
    p_price.add_argument("--output", default=None, help="Optional path to write JSON result envelope")

    p_demo = sub.add_parser("demo", help="Run reproducible end-to-end European and American pricing demo")
    p_demo.add_argument("--config", default=None, help="Optional path to engine config JSON")
    p_demo.add_argument("--output", default=None, help="Optional path to write demo JSON output")

    p_replay = sub.add_parser("replay", help="Replay a saved request/result JSON envelope and verify determinism")
    p_replay.add_argument("--input", required=True, help="Path to saved envelope JSON")
    p_replay.add_argument("--config", default=None, help="Optional path to engine config JSON")

    p_bench = sub.add_parser("benchmark", help="Run convergence and Monte Carlo coverage benchmarks")
    p_bench.add_argument("--config", default=None, help="Optional path to engine config JSON")
    p_bench.add_argument("--output-dir", default="reports", help="Directory to write versioned JSON reports")

    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
        engine = PricingEngine(cfg)

        if args.command == "price":
            raw = read_json(args.input)
            req = PricingRequest.from_dict(raw)
            res = engine.price(req)
            envelope = {"request": req.to_dict(), "result": res.to_dict()}
            if args.output:
                write_json(args.output, envelope)
            print(json.dumps(envelope, indent=2, sort_keys=True))
            return 0

        if args.command == "demo":
            payload = _build_demo_payload(engine)
            if args.output:
                write_json(args.output, payload)
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0

        if args.command == "replay":
            raw = read_json(args.input)
            summary = engine.replay_envelope(raw)
            print(json.dumps(summary, indent=2, sort_keys=True))
            if summary["exact_reproducible"] is False:
                return 2
            return 0

        if args.command == "benchmark":
            paths = _run_benchmarks(engine, Path(args.output_dir))
            print(json.dumps({"status": "ok", "written_reports": paths}, indent=2, sort_keys=True))
            return 0

        return 1
    except PricingEngineError as exc:
        print(json.dumps({"status": "error", "error": exc.to_dict()}, indent=2), file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover
        print(json.dumps({"status": "error", "error": {"error_code": "UNEXPECTED_ERROR", "message": str(exc)}}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
