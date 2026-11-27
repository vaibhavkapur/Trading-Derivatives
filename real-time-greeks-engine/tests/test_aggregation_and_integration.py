"""Integration tests for incremental aggregation, sign/multiplier changes, caching, scenarios, and replay."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from real_time_greeks_engine.adapters import JsonFixtureAdapter
from real_time_greeks_engine.config import load_config
from real_time_greeks_engine.engine import RealTimeGreeksEngine
from real_time_greeks_engine.models import OptionPosition, ScenarioShock, SpotTick

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "data" / "fixtures"


def _hydrated_engine() -> RealTimeGreeksEngine:
    cfg = load_config(ROOT / "config" / "default.json")
    engine = RealTimeGreeksEngine(cfg)
    JsonFixtureAdapter(FIXTURES / "valid_portfolio.json").load_into(engine)
    return engine


def test_incremental_aggregation_reconciles_with_full_recompute() -> None:
    engine = _hydrated_engine()
    snap = engine.get_snapshot()

    assert snap.totals.position_count == 5
    assert set(snap.by_underlying.keys()) == {"ACME", "SPX_SYN", "TECH_SYN"}
    assert "ACME|2027-06-01" in snap.by_underlying_expiry
    assert "ACME|2026-12-01" in snap.by_underlying_expiry
    assert "SPX_SYN|2026-09-01" in snap.by_underlying_expiry

    recon = engine.reconcile()
    assert recon.passed is True
    assert recon.max_abs_diff <= 1e-12
    assert recon.mismatched_buckets == []


def test_position_sign_and_multiplier_changes_reconcile_exactly() -> None:
    """Required integration scenario: position sign flips, multiplier updates, zeroing, and removal."""
    engine = _hydrated_engine()
    base_snap = engine.get_snapshot()
    orig_pos_res = base_snap.positions["POS-ACME-1Y-C100"]

    # 1. Flip sign from +10 to -10 (same multiplier 100) -> exact negation of position Greeks
    pos_flip = OptionPosition(
        position_id="POS-ACME-1Y-C100",
        account_id="DESK-EQ-OPT",
        quantity=-10.0,
        version=2,
        updated_at_utc=base_snap.valuation_time_utc,
        instrument=engine._positions["POS-ACME-1Y-C100"].instrument,
    )
    snap_flip = engine.upsert_position(pos_flip)
    flipped_res = snap_flip.positions["POS-ACME-1Y-C100"]
    assert flipped_res.position_greeks.pv == pytest.approx(-orig_pos_res.position_greeks.pv, rel=1e-12)
    assert flipped_res.position_greeks.dollar_delta == pytest.approx(
        -orig_pos_res.position_greeks.dollar_delta, rel=1e-12
    )
    assert engine.reconcile().passed is True

    # 2. Change multiplier from 100 -> 250 and quantity from -10 -> +4 (net scale +1000 == original +1000)
    orig_inst = engine._positions["POS-ACME-1Y-C100"].instrument
    new_inst = orig_inst.model_copy(update={"contract_multiplier": 250.0})
    pos_mult = OptionPosition(
        position_id="POS-ACME-1Y-C100",
        account_id="DESK-EQ-OPT",
        quantity=4.0,
        version=3,
        updated_at_utc=base_snap.valuation_time_utc,
        instrument=new_inst,
    )
    snap_mult = engine.upsert_position(pos_mult)
    mult_res = snap_mult.positions["POS-ACME-1Y-C100"]
    assert mult_res.position_greeks.pv == pytest.approx(orig_pos_res.position_greeks.pv, rel=1e-12)
    assert mult_res.position_greeks.vega == pytest.approx(orig_pos_res.position_greeks.vega, rel=1e-12)
    assert snap_mult.totals.pv == pytest.approx(base_snap.totals.pv, rel=1e-12)
    assert engine.reconcile().passed is True

    # 3. Change multiplier to 50 and quantity to -15, then remove entire ACME expiry bucket
    pos_small = pos_mult.model_copy(
        update={
            "quantity": -15.0,
            "version": 4,
            "instrument": orig_inst.model_copy(update={"contract_multiplier": 50.0}),
        }
    )
    engine.upsert_position(pos_small)
    assert engine.reconcile().passed is True

    snap_removed = engine.remove_position("POS-ACME-1Y-C100")
    assert "POS-ACME-1Y-C100" not in snap_removed.positions
    assert "ACME|2027-06-01" not in snap_removed.by_underlying_expiry
    assert snap_removed.totals.position_count == 4
    assert engine.reconcile().passed is True


def test_version_aware_cache_hits_and_version_isolation() -> None:
    engine = _hydrated_engine()
    diag1 = engine.diagnostics()
    assert diag1["cache"]["size"] == 5
    initial_hits = diag1["cache"]["hits"]

    # Re-evaluating same valuation time with unchanged market/position versions should hit cache for all 5
    engine.set_valuation_time(engine.get_snapshot().valuation_time_utc, recompute_all=True)
    diag2 = engine.diagnostics()
    assert diag2["cache"]["hits"] == initial_hits + 5

    # Updating ACME spot tick only invalidates/misses ACME positions (2), leaving others untouched
    misses_before = diag2["cache"]["misses"]
    tick = SpotTick(
        tick_id="TICK-ACME-CACHE-2",
        underlying="ACME",
        spot=103.0,
        sequence_no=2,
        event_time_utc=engine.get_snapshot().valuation_time_utc,
        received_time_utc=engine.get_snapshot().valuation_time_utc,
    )
    outcome, _ = engine.ingest_spot_tick(tick)
    assert outcome.accepted is True
    diag3 = engine.diagnostics()
    assert diag3["cache"]["misses"] == misses_before + 2
    assert engine.reconcile().passed is True


def test_parallel_scenarios_match_serial_and_fixed_seed_reproducible() -> None:
    engine = _hydrated_engine()
    shocks_a = engine.generate_stochastic_scenarios(12, seed=12345)
    shocks_b = engine.generate_stochastic_scenarios(12, seed=12345)
    assert shocks_a == shocks_b

    serial = engine.run_scenarios(shocks_a, max_workers=1)
    parallel = engine.run_scenarios(shocks_a, max_workers=4)
    assert len(serial) == len(parallel) == 12
    for s_res, p_res in zip(serial, parallel):
        assert s_res.scenario_id == p_res.scenario_id
        assert s_res.scenario_pv == pytest.approx(p_res.scenario_pv, abs=1e-12)
        assert s_res.pnl == pytest.approx(p_res.pnl, abs=1e-12)

    # First-order + second-order Taylor check on small 0.5% parallel spot shock
    snap = engine.get_snapshot()
    small = engine.run_scenarios([ScenarioShock(scenario_id="UP_50BPS", spot_shock_pct=0.005)])[0]
    approx_pnl = (
        snap.totals.dollar_delta * 0.005
        + 0.5 * (snap.totals.dollar_gamma_1pct / 0.01) * (0.005 ** 2)
    )
    assert small.pnl == pytest.approx(approx_pnl, rel=0.02)


def test_deterministic_replay_produces_identical_snapshot_id() -> None:
    events = json.loads((FIXTURES / "replay_events.json").read_text(encoding="utf-8"))["events"]

    eng1 = _hydrated_engine()
    snap1 = eng1.replay_events(events)

    eng2 = _hydrated_engine()
    snap2 = eng2.replay_events(events)

    assert snap1.snapshot_id == snap2.snapshot_id
    assert snap1.totals == snap2.totals
    assert snap1.by_underlying == snap2.by_underlying
    assert eng1.reconcile().passed is True
    assert eng1.diagnostics()["counters"]["duplicate_ticks_ignored"] == 1
