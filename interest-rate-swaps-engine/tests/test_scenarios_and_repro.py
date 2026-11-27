"""Tests for curve shock scenarios, stochastic reproducibility, valuation IDs, and adapters."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from irs_engine.adapters import InMemoryCurveProvider, IRSEngineServiceAdapter
from irs_engine.domain.calendars import CalendarRegistry
from irs_engine.domain.enums import SwapDirection
from irs_engine.domain.pricer import price_swap
from irs_engine.domain.scenarios import run_deterministic_scenarios, run_stochastic_scenarios
from irs_engine.io import load_valuation_bundle, to_plain_dict


def test_curve_shocks_and_payer_receiver_symmetry(
    fixtures_dir: Path, config_dir: Path, calendar_registry: CalendarRegistry
) -> None:
    trade, disc, fwd, fixings, cfg, _ = load_valuation_bundle(
        fixtures_dir / "valid" / "usd_5y_vanilla_swap.json"
    )
    recv_trade = dataclasses.replace(trade, direction=SwapDirection.RECEIVER)
    scen_cfg = json.loads((config_dir / "scenarios.json").read_text(encoding="utf-8"))
    specs = scen_cfg["deterministic_scenarios"]

    payer_snaps = run_deterministic_scenarios(
        trade, disc, fwd, specs, fixing_history=fixings, calendar_registry=calendar_registry, config=cfg
    )
    recv_snaps = run_deterministic_scenarios(
        recv_trade,
        disc,
        fwd,
        specs,
        fixing_history=fixings,
        calendar_registry=calendar_registry,
        config=cfg,
    )

    by_id = {s.scenario_id: s for s in payer_snaps}
    # For a payer swap: +100bp parallel raises NPV significantly; -100bp lowers NPV
    assert by_id["parallel_up_100bp"].pnl > 350_000.0
    assert by_id["parallel_down_100bp"].pnl < -350_000.0
    # Forward-discount basis widening (+25bp on forward only) raises floating PV -> positive payer PnL
    assert by_id["basis_widening_25bp"].pnl > 100_000.0

    # Exact payer/receiver symmetry across every shock scenario
    for p_s, r_s in zip(payer_snaps, recv_snaps):
        assert abs(p_s.npv + r_s.npv) <= 1e-8
        assert abs(p_s.pnl + r_s.pnl) <= 1e-8
        assert p_s.par_rate == pytest.approx(r_s.par_rate, abs=1e-14)


def test_stochastic_scenarios_deterministic_seed_reproducibility(
    fixtures_dir: Path, calendar_registry: CalendarRegistry
) -> None:
    trade, disc, fwd, fixings, cfg, _ = load_valuation_bundle(
        fixtures_dir / "valid" / "usd_5y_vanilla_swap.json"
    )
    run1 = run_stochastic_scenarios(
        trade,
        disc,
        fwd,
        seed=42,
        num_paths=100,
        fixing_history=fixings,
        calendar_registry=calendar_registry,
        config=cfg,
    )
    run2 = run_stochastic_scenarios(
        trade,
        disc,
        fwd,
        seed=42,
        num_paths=100,
        fixing_history=fixings,
        calendar_registry=calendar_registry,
        config=cfg,
    )
    run_diff_seed = run_stochastic_scenarios(
        trade,
        disc,
        fwd,
        seed=99,
        num_paths=100,
        fixing_history=fixings,
        calendar_registry=calendar_registry,
        config=cfg,
    )

    assert to_plain_dict(run1) == to_plain_dict(run2)
    assert run1.mean_pnl != run_diff_seed.mean_pnl
    assert run1.expected_shortfall_95 >= run1.var_95


def test_valuation_id_deterministic_and_sensitive_to_inputs(
    fixtures_dir: Path, calendar_registry: CalendarRegistry
) -> None:
    trade, disc, fwd, fixings, cfg, _ = load_valuation_bundle(
        fixtures_dir / "valid" / "usd_5y_vanilla_swap.json"
    )
    r1 = price_swap(
        trade, disc, fwd, fixing_history=fixings, calendar_registry=calendar_registry, config=cfg
    )
    r2 = price_swap(
        trade, disc, fwd, fixing_history=fixings, calendar_registry=calendar_registry, config=cfg
    )
    assert r1.valuation_id == r2.valuation_id
    assert to_plain_dict(r1) == to_plain_dict(r2)

    mod_trade = dataclasses.replace(trade, notional=trade.notional + 1.0)
    r3 = price_swap(
        mod_trade, disc, fwd, fixing_history=fixings, calendar_registry=calendar_registry, config=cfg
    )
    assert r3.valuation_id != r1.valuation_id


def test_integration_adapter_roundtrip(
    fixtures_dir: Path, calendar_registry: CalendarRegistry
) -> None:
    trade, disc, fwd, fixings, cfg, _ = load_valuation_bundle(
        fixtures_dir / "valid" / "usd_5y_vanilla_swap.json"
    )
    provider = InMemoryCurveProvider([disc, fwd])
    adapter = IRSEngineServiceAdapter(provider, calendar_registry=calendar_registry, config=cfg)

    val = adapter.value_swap(trade, disc.valuation_date, fixing_history=fixings)
    sens = adapter.compute_risk(trade, disc.valuation_date, fixing_history=fixings)

    direct_val = price_swap(
        trade, disc, fwd, fixing_history=fixings, calendar_registry=calendar_registry, config=cfg
    )
    assert val.valuation_id == direct_val.valuation_id
    assert val.npv == pytest.approx(direct_val.npv)
    assert sens.reconciliation_within_tolerance is True
