"""Integration tests for PricingEngine service, American vs European scenarios, replay, and CLI."""

from __future__ import annotations

import json
from pathlib import Path

from derivatives_pricing_engine import (
    ExerciseStyle,
    MarketSnapshot,
    OptionContract,
    OptionType,
    PricingEngine,
    PricingMethod,
    PricingRequest,
)
from derivatives_pricing_engine.cli import main as cli_main


def test_service_prices_valid_fixtures_and_tracks_metrics(engine: PricingEngine, fixtures_dir: Path) -> None:
    euro_raw = json.loads((fixtures_dir / "valid_european_call.json").read_text(encoding="utf-8"))
    amer_raw = json.loads((fixtures_dir / "valid_american_put.json").read_text(encoding="utf-8"))

    res_euro = engine.price(PricingRequest.from_dict(euro_raw))
    res_amer = engine.price(PricingRequest.from_dict(amer_raw))

    assert res_euro.total_price == res_euro.unit_price * 100.0
    assert res_euro.result_id.startswith("res-")
    assert res_amer.unit_price > 6.0
    assert res_amer.diagnostics["early_exercise_nodes"] > 0

    snap = engine.metrics.snapshot()
    assert snap["requests_total"] == 2
    assert snap["requests_succeeded"] == 2
    assert snap["requests_failed"] == 0


def test_integration_american_put_premium_vs_european_across_methods(engine: PricingEngine) -> None:
    amer_put = OptionContract(
        contract_id="INT-AMR-PUT-100",
        underlying="SYNTH-EQ-01",
        currency="USD",
        option_type=OptionType.PUT,
        exercise_style=ExerciseStyle.AMERICAN,
        strike=100.0,
        expiry_timestamp="2027-01-15T16:00:00Z",
    )
    market = MarketSnapshot(
        snapshot_id="SNAP-INT-01",
        valuation_timestamp="2026-01-15T16:00:00Z",
        spot=100.0,
        risk_free_rate=0.05,
        dividend_yield=0.0,
        volatility=0.20,
    )
    comparison = engine.compare_methods(amer_put, market)
    euro_bs = comparison["european_black_scholes_reference"]
    bin_price = comparison["results"]["binomial"]["unit_price"]
    pde_price = comparison["results"]["pde_fd"]["unit_price"]

    # Both independent numerical solvers agree closely and confirm positive early exercise premium
    assert bin_price > euro_bs + 0.45
    assert pde_price > euro_bs + 0.45
    assert abs(bin_price - pde_price) < 0.01


def test_reproducible_replay_envelope(engine: PricingEngine, fixtures_dir: Path, tmp_path: Path) -> None:
    euro_raw = json.loads((fixtures_dir / "valid_european_call.json").read_text(encoding="utf-8"))
    euro_raw["method"] = "monte_carlo"
    euro_raw["method_params"] = {"paths": 20000, "seed": 777, "antithetic": True}

    req = PricingRequest.from_dict(euro_raw)
    res1 = engine.price(req)
    envelope = {"request": req.to_dict(), "result": res1.to_dict()}

    replay = engine.replay_envelope(envelope)
    assert replay["result_id_matches"] is True
    assert replay["abs_unit_price_diff"] == 0.0
    assert replay["exact_reproducible"] is True

    # Also verify CLI price + replay round-trip
    req_path = tmp_path / "req.json"
    env_path = tmp_path / "env.json"
    req_path.write_text(json.dumps(euro_raw), encoding="utf-8")

    rc_price = cli_main(["price", "--input", str(req_path), "--output", str(env_path)])
    assert rc_price == 0
    rc_replay = cli_main(["replay", "--input", str(env_path)])
    assert rc_replay == 0
