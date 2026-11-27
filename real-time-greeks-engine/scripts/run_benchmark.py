#!/usr/bin/env python3
"""Benchmark analytic vs finite-difference agreement, incremental update p50/p99 latency, and parallel scenarios."""

from __future__ import annotations

import json
import math
import platform
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from real_time_greeks_engine.config import load_config
from real_time_greeks_engine.engine import RealTimeGreeksEngine
from real_time_greeks_engine.models import (
    CurvePoint,
    DayCountConvention,
    OptionInstrument,
    OptionPosition,
    OptionType,
    SpotTick,
    VolSurfaceSnapshot,
    YieldCurveSnapshot,
)
from real_time_greeks_engine.pricing import (
    analytic_bsm_greeks,
    bsm_price,
    bump_and_revalue_greeks,
)

ROOT = Path(__file__).resolve().parent.parent


def _benchmark_analytic_vs_fd(cfg) -> dict:
    cases = [
        ("ATM_1Y_CALL", OptionType.CALL, 100.0, 100.0, 1.0, 0.05, 0.0, 0.20),
        ("OTM_6M_PUT_DIV", OptionType.PUT, 100.0, 95.0, 0.5, 0.04, 0.02, 0.25),
        ("ITM_3M_CALL_HIGHVOL", OptionType.CALL, 450.0, 420.0, 0.25, 0.045, 0.015, 0.30),
        ("OTM_2Y_CALL", OptionType.CALL, 5000.0, 5400.0, 2.0, 0.048, 0.014, 0.18),
        ("ITM_9M_PUT", OptionType.PUT, 200.0, 220.0, 0.75, 0.052, 0.005, 0.32),
    ]
    checked_fields = ("price", "delta", "gamma", "vega", "theta", "rho", "vanna", "charm", "volga")
    results = []
    max_first_order_rel_err = 0.0
    max_all_rel_err = 0.0

    for name, opt_type, s, k, t, r, q, v in cases:
        an = analytic_bsm_greeks(opt_type, s, k, t, r, q, v, DayCountConvention.ACT_365F)

        def _p(sp: float, tte: float, rt: float, vol: float) -> float:
            return bsm_price(opt_type, sp, k, tte, rt, q, vol)

        fd = bump_and_revalue_greeks(_p, s, t, r, v, DayCountConvention.ACT_365F, cfg.pricing)
        errs = {}
        for f in checked_fields:
            av = getattr(an, f)
            fv = getattr(fd, f)
            denom = max(abs(av), 1e-8)
            rel_err = abs(av - fv) / denom
            errs[f] = {"analytic": round(av, 10), "fd": round(fv, 10), "rel_err": rel_err}
            max_all_rel_err = max(max_all_rel_err, rel_err)
            if f in ("price", "delta", "gamma", "vega", "theta", "rho"):
                max_first_order_rel_err = max(max_first_order_rel_err, rel_err)
        results.append({"case": name, "errors": errs})

    return {
        "threshold_rel_err": 1e-4,
        "max_primary_greeks_rel_err": max_first_order_rel_err,
        "max_all_greeks_rel_err": max_all_rel_err,
        "passed": max_all_rel_err <= 1e-4,
        "cases": results,
    }


def _benchmark_incremental_updates(cfg, *, num_underlyings: int = 10, options_per_underlying: int = 20, num_ticks: int = 1000) -> dict:
    rng = random.Random(42)
    base_time = datetime(2026, 6, 1, 14, 30, 0, tzinfo=timezone.utc)
    engine = RealTimeGreeksEngine(cfg, valuation_time_utc=base_time)

    underlyings = [f"UND_{i:02d}" for i in range(num_underlyings)]
    engine.upsert_yield_curve(
        YieldCurveSnapshot(
            curve_version="USD-BENCH-001",
            currency="USD",
            as_of_utc=base_time,
            ttl_seconds=3600.0,
            points=[
                CurvePoint(tenor_years=0.25, zero_rate=0.049),
                CurvePoint(tenor_years=1.0, zero_rate=0.050),
                CurvePoint(tenor_years=2.0, zero_rate=0.048),
            ],
            dividend_yields={u: 0.01 for u in underlyings},
        )
    )

    spots: dict[str, float] = {}
    for u in underlyings:
        s0 = 100.0 + rng.uniform(-20.0, 200.0)
        spots[u] = s0
        engine.upsert_vol_surface(
            VolSurfaceSnapshot(
                vol_version=f"VOL-{u}-001",
                underlying=u,
                as_of_utc=base_time,
                ttl_seconds=3600.0,
                expiries_years=[0.25, 0.5, 1.0],
                strikes=[s0 * 0.8, s0, s0 * 1.2],
                vols=[
                    [0.28, 0.24, 0.22],
                    [0.27, 0.23, 0.215],
                    [0.26, 0.225, 0.21],
                ],
            )
        )
        engine.ingest_spot_tick(
            SpotTick(
                tick_id=f"INIT-{u}",
                underlying=u,
                spot=s0,
                sequence_no=1,
                event_time_utc=base_time,
                received_time_utc=base_time,
            )
        )

        for j in range(options_per_underlying):
            expiry_days = 90 + (j % 4) * 90
            strike = round(s0 * (0.85 + 0.075 * (j % 5)), 2)
            opt_type = OptionType.CALL if j % 2 == 0 else OptionType.PUT
            qty = float((1 if j % 3 != 0 else -1) * (5 + j))
            engine.upsert_position(
                OptionPosition(
                    position_id=f"POS-{u}-{j:03d}",
                    account_id="BENCH-BOOK",
                    quantity=qty,
                    version=1,
                    updated_at_utc=base_time,
                    instrument=OptionInstrument(
                        instrument_id=f"INST-{u}-{j:03d}",
                        underlying=u,
                        option_type=opt_type,
                        strike=strike,
                        expiry_utc=base_time + timedelta(days=expiry_days),
                        currency="USD",
                        contract_multiplier=100.0,
                    ),
                )
            )

    # Measure tick-by-tick incremental update latencies cleanly
    seq_by_u = {u: 1 for u in underlyings}
    tick_latencies_ms: list[float] = []
    for t_idx in range(num_ticks):
        u = underlyings[t_idx % num_underlyings]
        seq_by_u[u] += 1
        spots[u] = max(1.0, spots[u] * math.exp(rng.gauss(0.0, 0.002)))
        tick = SpotTick(
            tick_id=f"BENCH-{u}-{seq_by_u[u]}",
            underlying=u,
            spot=round(spots[u], 4),
            sequence_no=seq_by_u[u],
            event_time_utc=base_time,
            received_time_utc=base_time,
        )
        t0 = time.perf_counter()
        engine.ingest_spot_tick(tick)
        tick_latencies_ms.append((time.perf_counter() - t0) * 1000.0)

    recon = engine.reconcile()

    # Parallel scenario benchmark (24 shocks across 200 options)
    shocks = engine.generate_stochastic_scenarios(24, seed=42)
    t_s0 = time.perf_counter()
    serial_res = engine.run_scenarios(shocks, max_workers=1)
    serial_ms = (time.perf_counter() - t_s0) * 1000.0

    t_p0 = time.perf_counter()
    parallel_res = engine.run_scenarios(shocks, max_workers=4)
    parallel_ms = (time.perf_counter() - t_p0) * 1000.0

    max_scenario_diff = max(abs(a.scenario_pv - b.scenario_pv) for a, b in zip(serial_res, parallel_res))

    tick_latencies_ms.sort()

    def _pct(vals: list[float], p: float) -> float:
        rank = (p / 100.0) * (len(vals) - 1)
        lo = int(rank)
        hi = min(lo + 1, len(vals) - 1)
        return vals[lo] + (rank - lo) * (vals[hi] - vals[lo])

    return {
        "portfolio_positions": num_underlyings * options_per_underlying,
        "num_underlyings": num_underlyings,
        "options_per_underlying": options_per_underlying,
        "ticks_measured": num_ticks,
        "spot_tick_update_latency_ms": {
            "p50": round(_pct(tick_latencies_ms, 50.0), 4),
            "p95": round(_pct(tick_latencies_ms, 95.0), 4),
            "p99": round(_pct(tick_latencies_ms, 99.0), 4),
            "max": round(tick_latencies_ms[-1], 4),
            "mean": round(sum(tick_latencies_ms) / len(tick_latencies_ms), 4),
        },
        "reconciliation_after_1000_ticks": {
            "passed": recon.passed,
            "max_abs_diff": recon.max_abs_diff,
            "max_rel_diff": recon.max_rel_diff,
            "total_buckets_checked": recon.total_buckets_checked,
        },
        "scenario_recomputation_24x200": {
            "serial_ms": round(serial_ms, 3),
            "parallel_4_workers_ms": round(parallel_ms, 3),
            "serial_vs_parallel_max_pv_diff": max_scenario_diff,
        },
        "engine_diagnostics": engine.diagnostics(),
    }


def main() -> int:
    cfg = load_config(ROOT / "config" / "default.json")
    fd_report = _benchmark_analytic_vs_fd(cfg)
    perf_report = _benchmark_incremental_updates(cfg)

    output = {
        "metadata": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
            "engine_id": cfg.engine_id,
            "engine_config_hash": cfg.config_hash(),
            "seed": 42,
            "data_provenance": "Synthetic benchmark portfolio (no real market or customer data).",
        },
        "analytic_vs_finite_difference": fd_report,
        "incremental_update_benchmark": perf_report,
    }

    out_path = ROOT / "reports" / "benchmark_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote benchmark results to {out_path}")
    print("Analytic vs FD max rel error:", fd_report["max_all_greeks_rel_err"])
    print("Spot tick latency (ms):", perf_report["spot_tick_update_latency_ms"])
    print("Reconciliation passed:", perf_report["reconciliation_after_1000_ticks"]["passed"])
    return 0 if (fd_report["passed"] and perf_report["reconciliation_after_1000_ticks"]["passed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
