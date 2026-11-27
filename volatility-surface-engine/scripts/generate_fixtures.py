#!/usr/bin/env python3
"""Generate deterministic synthetic fixtures and independent oracle expectations for testing and evaluation."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

from volatility_surface_engine.black76 import black76_price
from volatility_surface_engine.models import SCHEMA_VERSION, SVIParameters
from volatility_surface_engine.svi import durrleman_density, svi_derivatives, svi_implied_vol, svi_total_variance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"

VALUATION_DT = datetime(2026, 1, 15, 16, 0, 0, tzinfo=timezone.utc)
VALUATION_TS = "2026-01-15T16:00:00Z"


def _expiry_ts(days: int) -> str:
    dt = VALUATION_DT + timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_svi_chain(
    snapshot_id: str,
    spot: float,
    rate: float,
    div_yield: float,
    expiry_specs: List[Dict[str, Any]],
    k_grid: List[float],
    spread_bps_atm: float = 40.0,
    noise_iv_std: float = 0.0,
    seed: int = 42,
    include_both_sides_near_atm: bool = True,
) -> Dict[str, Any]:
    rng = np.random.default_rng(seed)
    expiries: List[Dict[str, Any]] = []
    quotes: List[Dict[str, Any]] = []
    q_counter = 1

    for spec in expiry_specs:
        days = int(spec["days"])
        t_years = days / 365.0
        exp_ts = _expiry_ts(days)
        df = math.exp(-rate * t_years)
        fwd = spot * math.exp((rate - div_yield) * t_years)
        params = SVIParameters(**spec["svi"])

        expiries.append(
            {
                "expiry_timestamp": exp_ts,
                "time_to_expiry_years": round(t_years, 10),
                "forward": round(fwd, 8),
                "discount_factor": round(df, 10),
            }
        )

        for k in k_grid:
            strike = round(fwd * math.exp(k), 4)
            true_k = math.log(strike / fwd)
            true_iv = float(svi_implied_vol(np.array([true_k]), t_years, params)[0])
            obs_iv = max(true_iv + float(rng.normal(0.0, noise_iv_std)), 0.01) if noise_iv_std > 0.0 else true_iv

            # Wing-dependent relative bid-ask half-spread
            rel_half_spread = (spread_bps_atm / 10000.0) * (1.0 + 3.5 * abs(true_k))
            opt_types = ["put", "call"] if (include_both_sides_near_atm and abs(true_k) <= 0.12) else (
                ["put"] if strike < fwd else ["call"]
            )
            for opt_type in opt_types:
                mid_price = black76_price(fwd, strike, t_years, obs_iv, df, opt_type)
                half_sp = max(1e-4, mid_price * rel_half_spread)
                bid = max(1e-4, round(mid_price - half_sp, 6))
                ask = round(max(bid + 2e-4, mid_price + half_sp), 6)
                quotes.append(
                    {
                        "quote_id": f"{snapshot_id}-q{q_counter:04d}",
                        "expiry_timestamp": exp_ts,
                        "strike": strike,
                        "option_type": opt_type,
                        "bid": bid,
                        "ask": ask,
                        "volume": int(100 + 400 * math.exp(-8.0 * true_k * true_k)),
                        "open_interest": int(500 + 2500 * math.exp(-6.0 * true_k * true_k)),
                    }
                )
                q_counter += 1

    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "underlying": "SYNTH-EQX",
        "valuation_timestamp": VALUATION_TS,
        "spot": spot,
        "conventions": {
            "currency": "USD",
            "day_count": "ACT/365F",
            "exercise_style": "european",
            "price_unit": "currency_per_unit",
            "vol_unit": "annualized_decimal",
            "time_unit": "year_fraction",
        },
        "expiries": expiries,
        "quotes": quotes,
    }


def generate_all_fixtures() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Valid arbitrage-free reference chain + independent analytical oracle
    ref_specs = [
        {"days": 30, "svi": {"a": 0.0020, "b": 0.022, "rho": -0.40, "m": 0.01, "sigma": 0.14}},
        {"days": 90, "svi": {"a": 0.0072, "b": 0.052, "rho": -0.38, "m": 0.01, "sigma": 0.18}},
        {"days": 180, "svi": {"a": 0.0165, "b": 0.092, "rho": -0.35, "m": 0.01, "sigma": 0.22}},
        {"days": 365, "svi": {"a": 0.0370, "b": 0.155, "rho": -0.32, "m": 0.01, "sigma": 0.28}},
    ]

    # Verify ground-truth reference specs are strictly butterfly- and calendar-arbitrage-free
    k_check = np.linspace(-0.30, 0.30, 121)
    prev_w = None
    for spec in ref_specs:
        p = SVIParameters(**spec["svi"])
        w, dw, d2w = svi_derivatives(k_check, p)
        g = durrleman_density(w, dw, d2w, k_check)
        assert float(np.min(w)) > 0.0, f"Reference SVI non-positive variance for {spec}"
        assert float(np.min(g)) > 0.0, f"Reference SVI negative Durrleman density for {spec}: {np.min(g)}"
        if prev_w is not None:
            assert float(np.min(w - prev_w)) > 0.0, f"Reference SVI calendar violation at {spec}"
        prev_w = w

    ref_k_grid = [-0.25, -0.20, -0.15, -0.10, -0.05, 0.0, 0.05, 0.10, 0.15, 0.20, 0.25]
    ref_chain = _make_svi_chain(
        snapshot_id="ref-synth-eqx-20260115",
        spot=100.0,
        rate=0.045,
        div_yield=0.015,
        expiry_specs=ref_specs,
        k_grid=ref_k_grid,
        spread_bps_atm=25.0,
        noise_iv_std=0.0,
        seed=42,
    )
    (FIXTURES_DIR / "valid_chain_reference.json").write_text(
        json.dumps(ref_chain, indent=2), encoding="utf-8"
    )

    # Independent oracle expectations (hand/analytic Black-76 & SVI queries)
    oracle_queries: List[Dict[str, Any]] = []
    for spec in ref_specs:
        t_y = spec["days"] / 365.0
        p = SVIParameters(**spec["svi"])
        for k_q in [-0.15, 0.0, 0.15]:
            w_true = float(svi_total_variance(np.array([k_q]), p)[0])
            iv_true = math.sqrt(w_true / t_y)
            oracle_queries.append(
                {
                    "expiry_years": round(t_y, 10),
                    "log_moneyness": k_q,
                    "expected_total_variance": round(w_true, 10),
                    "expected_implied_vol": round(iv_true, 10),
                }
            )
    # Add midpoint maturity interpolation oracle (T = 60/365 between 30d and 90d)
    t_mid = 60.0 / 365.0
    lam = (60.0 - 30.0) / (90.0 - 30.0)
    p0 = SVIParameters(**ref_specs[0]["svi"])
    p1 = SVIParameters(**ref_specs[1]["svi"])
    for k_q in [-0.10, 0.0, 0.10]:
        w0 = float(svi_total_variance(np.array([k_q]), p0)[0])
        w1 = float(svi_total_variance(np.array([k_q]), p1)[0])
        w_interp = (1.0 - lam) * w0 + lam * w1
        oracle_queries.append(
            {
                "expiry_years": round(t_mid, 10),
                "log_moneyness": k_q,
                "expected_total_variance": round(w_interp, 10),
                "expected_implied_vol": round(math.sqrt(w_interp / t_mid), 10),
            }
        )

    expected_ref = {
        "schema_version": SCHEMA_VERSION,
        "reference_snapshot_id": ref_chain["snapshot_id"],
        "provenance": "Synthetic arbitrage-free raw SVI surface generated deterministically (seed=42) with exact Black-76 prices.",
        "ground_truth_slices": ref_specs,
        "oracle_queries": oracle_queries,
        "tolerances": {
            "max_iv_recovery_abs_err": 1e-3,
            "max_weighted_rmse_iv": 5e-4,
            "max_forward_parity_rel_diff": 2e-3,
        },
    }
    (FIXTURES_DIR / "expected_reference.json").write_text(
        json.dumps(expected_ref, indent=2), encoding="utf-8"
    )

    # 2. Edge-cases chain: wide/crossed quotes, near-zero vega, intrinsic violation, missing strikes
    edge_specs = [
        {"days": 30, "svi": {"a": 0.0020, "b": 0.022, "rho": -0.40, "m": 0.01, "sigma": 0.14}},
        {"days": 90, "svi": {"a": 0.0072, "b": 0.052, "rho": -0.38, "m": 0.01, "sigma": 0.18}},
    ]
    # Sparse/missing strikes (gap in middle)
    sparse_k = [-0.20, -0.14, -0.08, 0.0, 0.09, 0.16, 0.22]
    edge_chain = _make_svi_chain(
        snapshot_id="edge-cases-synth-eqx-20260115",
        spot=100.0,
        rate=0.045,
        div_yield=0.015,
        expiry_specs=edge_specs,
        k_grid=sparse_k,
        spread_bps_atm=30.0,
        noise_iv_std=0.0,
        seed=99,
        include_both_sides_near_atm=False,
    )
    exp30 = edge_chain["expiries"][0]["expiry_timestamp"]
    exp90 = edge_chain["expiries"][1]["expiry_timestamp"]
    # Add short/expired expiry for short-expiry rejection test
    exp_short = _expiry_ts(0)
    edge_chain["expiries"].append(
        {
            "expiry_timestamp": "2026-01-15T20:00:00Z",
            "time_to_expiry_years": 4.0 / (365.0 * 24.0),
            "forward": 100.0,
            "discount_factor": 1.0,
        }
    )
    extra_bad_quotes = [
        # Crossed quote (bid > ask)
        {
            "quote_id": "edge-crossed-01",
            "expiry_timestamp": exp30,
            "strike": 100.0,
            "option_type": "call",
            "bid": 2.80,
            "ask": 2.40,
        },
        # Wide relative spread (> 35%)
        {
            "quote_id": "edge-wide-rel-01",
            "expiry_timestamp": exp30,
            "strike": 97.0,
            "option_type": "put",
            "bid": 0.50,
            "ask": 1.30,
        },
        # Wide absolute spread (> 25.0)
        {
            "quote_id": "edge-wide-abs-01",
            "expiry_timestamp": exp90,
            "strike": 70.0,
            "option_type": "call",
            "bid": 18.0,
            "ask": 48.0,
        },
        # Zero bid
        {
            "quote_id": "edge-zero-bid-01",
            "expiry_timestamp": exp30,
            "strike": 128.0,
            "option_type": "call",
            "bid": 0.0,
            "ask": 0.04,
        },
        # Intrinsic bound violation (deep ITM call priced below discounted intrinsic)
        {
            "quote_id": "edge-intrinsic-01",
            "expiry_timestamp": exp90,
            "strike": 80.0,
            "option_type": "call",
            "bid": 18.50,
            "ask": 19.00,
        },
        # Near-zero vega deep OTM option (within moneyness bounds K/F=1.90, tiny price)
        {
            "quote_id": "edge-near-zero-vega-01",
            "expiry_timestamp": exp30,
            "strike": 185.0,
            "option_type": "call",
            "bid": 1e-6,
            "ask": 1.2e-6,
        },
        # Expired / ultra-short expiry
        {
            "quote_id": "edge-short-expiry-01",
            "expiry_timestamp": "2026-01-15T20:00:00Z",
            "strike": 100.0,
            "option_type": "call",
            "bid": 0.25,
            "ask": 0.30,
        },
    ]
    edge_chain["quotes"].extend(extra_bad_quotes)
    (FIXTURES_DIR / "edge_cases_chain.json").write_text(
        json.dumps(edge_chain, indent=2), encoding="utf-8"
    )

    # 3. Held-out smile evaluation fixture (dense 17-strike synthetic chain split into train & held-out)
    full_k = [round(-0.24 + 0.03 * i, 6) for i in range(17)]
    # Hold out interior strikes at indices 2, 5, 8, 11, 14 (keep endpoints for domain coverage)
    heldout_indices = {2, 5, 11, 14}
    train_k = [k for i, k in enumerate(full_k) if i not in heldout_indices]
    hold_k = [k for i, k in enumerate(full_k) if i in heldout_indices]

    train_chain = _make_svi_chain(
        snapshot_id="heldout-train-synth-eqx-20260115",
        spot=100.0,
        rate=0.045,
        div_yield=0.015,
        expiry_specs=ref_specs,
        k_grid=train_k,
        spread_bps_atm=30.0,
        noise_iv_std=0.0008,  # 8 bps realistic microstructure IV noise
        seed=123,
    )
    hold_chain = _make_svi_chain(
        snapshot_id="heldout-test-synth-eqx-20260115",
        spot=100.0,
        rate=0.045,
        div_yield=0.015,
        expiry_specs=ref_specs,
        k_grid=hold_k,
        spread_bps_atm=30.0,
        noise_iv_std=0.0,  # Evaluate against clean ground truth
        seed=456,
        include_both_sides_near_atm=False,
    )
    heldout_bundle = {
        "schema_version": SCHEMA_VERSION,
        "provenance": "Synthetic 4-expiry, 17-strike smile split into 13 training strikes (with 8 bps IV noise, seed=123) and 4 held-out interior strikes per expiry.",
        "ground_truth_slices": ref_specs,
        "train_snapshot": train_chain,
        "heldout_snapshot": hold_chain,
    }
    (FIXTURES_DIR / "heldout_smile_chain.json").write_text(
        json.dumps(heldout_bundle, indent=2), encoding="utf-8"
    )

    # 4. Calendar arbitrage violation chain (30d total variance > 90d total variance)
    arb_specs = [
        {"days": 30, "svi": {"a": 0.0140, "b": 0.040, "rho": -0.35, "m": 0.0, "sigma": 0.16}},  # Very high 30d total var
        {"days": 90, "svi": {"a": 0.0080, "b": 0.035, "rho": -0.35, "m": 0.0, "sigma": 0.16}},  # Lower 90d total var!
    ]
    arb_chain = _make_svi_chain(
        snapshot_id="arb-calendar-violation-20260115",
        spot=100.0,
        rate=0.045,
        div_yield=0.015,
        expiry_specs=arb_specs,
        k_grid=ref_k_grid,
        spread_bps_atm=25.0,
        noise_iv_std=0.0,
        seed=77,
    )
    (FIXTURES_DIR / "arbitrage_violation_chain.json").write_text(
        json.dumps(arb_chain, indent=2), encoding="utf-8"
    )

    print(f"Generated deterministic fixtures in {FIXTURES_DIR}")


if __name__ == "__main__":
    generate_all_fixtures()
