"""Domain correctness tests: at-market par swap, hand-calculated oracle, dual-curve, symmetry."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from irs_engine.domain.calendars import CalendarRegistry
from irs_engine.domain.enums import SwapDirection
from irs_engine.domain.oracle import (
    check_optional_quantlib_oracle,
    run_independent_analytic_oracle,
    single_curve_telescoping_closed_form,
)
from irs_engine.domain.pricer import price_swap
from irs_engine.io import load_valuation_bundle


def test_at_market_par_swap_pv_near_zero(
    fixtures_dir: Path, calendar_registry: CalendarRegistry
) -> None:
    trade, disc, fwd, fixings, cfg, _ = load_valuation_bundle(
        fixtures_dir / "valid" / "usd_5y_par_swap.json"
    )
    res = price_swap(
        trade, disc, fwd, fixing_history=fixings, calendar_registry=calendar_registry, config=cfg
    )
    assert abs(res.npv) <= cfg.par_swap_pv_tolerance
    assert res.fixed_leg_pv == pytest.approx(res.floating_leg_pv, abs=cfg.par_swap_pv_tolerance)


def test_exact_par_rate_and_par_spread_zero_npv(
    fixtures_dir: Path, calendar_registry: CalendarRegistry
) -> None:
    trade, disc, fwd, fixings, cfg, _ = load_valuation_bundle(
        fixtures_dir / "valid" / "usd_5y_vanilla_swap.json"
    )
    base = price_swap(
        trade, disc, fwd, fixing_history=fixings, calendar_registry=calendar_registry, config=cfg
    )

    # Set fixed rate = par_rate -> NPV == 0
    par_trade = dataclasses.replace(
        trade, fixed_leg=dataclasses.replace(trade.fixed_leg, fixed_rate=base.par_rate)
    )
    par_res = price_swap(
        par_trade, disc, fwd, fixing_history=fixings, calendar_registry=calendar_registry, config=cfg
    )
    assert abs(par_res.npv) <= 1e-8

    # Set floating spread = par_spread -> NPV == 0
    par_spread_trade = dataclasses.replace(
        trade, floating_leg=dataclasses.replace(trade.floating_leg, spread=base.par_spread)
    )
    ps_res = price_swap(
        par_spread_trade,
        disc,
        fwd,
        fixing_history=fixings,
        calendar_registry=calendar_registry,
        config=cfg,
    )
    assert abs(ps_res.npv) <= 1e-8


def test_payer_receiver_exact_sign_symmetry(
    fixtures_dir: Path, calendar_registry: CalendarRegistry
) -> None:
    trade, disc, fwd, fixings, cfg, _ = load_valuation_bundle(
        fixtures_dir / "valid" / "usd_5y_vanilla_swap.json"
    )
    payer = price_swap(
        trade, disc, fwd, fixing_history=fixings, calendar_registry=calendar_registry, config=cfg
    )
    recv_trade = dataclasses.replace(trade, direction=SwapDirection.RECEIVER)
    receiver = price_swap(
        recv_trade, disc, fwd, fixing_history=fixings, calendar_registry=calendar_registry, config=cfg
    )
    assert abs(payer.npv + receiver.npv) <= cfg.symmetry_pv_tolerance
    assert payer.par_rate == pytest.approx(receiver.par_rate, abs=1e-14)
    assert payer.fixed_leg_pv == pytest.approx(receiver.fixed_leg_pv, abs=1e-12)
    assert payer.floating_leg_pv == pytest.approx(receiver.floating_leg_pv, abs=1e-12)


def test_hand_calculated_2y_oracle_and_telescoping_identity(
    fixtures_dir: Path, calendar_registry: CalendarRegistry
) -> None:
    trade, disc, fwd, fixings, cfg, meta = load_valuation_bundle(
        fixtures_dir / "valid" / "hand_calculated_2y_oracle.json"
    )
    expected = meta["hand_calculated_expected"]
    res = price_swap(
        trade, disc, fwd, fixing_history=fixings, calendar_registry=calendar_registry, config=cfg
    )
    assert res.floating_leg_pv == pytest.approx(expected["floating_leg_pv"], abs=1e-6)
    assert res.fixed_leg_pv == pytest.approx(expected["fixed_leg_pv"], abs=1e-6)
    assert res.npv == pytest.approx(expected["npv"], abs=1e-6)
    assert res.par_rate == pytest.approx(expected["par_rate"], abs=1e-12)

    # Verify independent closed-form telescoping formula
    cf_times = [(cf.payment_date - disc.valuation_date).days / 365.0 for cf in res.fixed_cashflows]
    cf_taus = [cf.year_fraction for cf in res.fixed_cashflows]
    tele = single_curve_telescoping_closed_form(
        trade.notional,
        trade.fixed_leg.fixed_rate,
        disc.nodes[0].zero_rate,
        cf_times,
        cf_taus,
        trade.direction,
    )
    assert abs(res.npv - tele["npv"]) <= 1e-8
    assert abs(res.par_rate - tele["par_rate"]) <= 1e-12


def test_independent_analytic_oracle_on_5y_dual_curve_and_stubs(
    fixtures_dir: Path, calendar_registry: CalendarRegistry
) -> None:
    for fname in (
        "usd_5y_vanilla_swap.json",
        "stub_front_short_swap.json",
        "stub_back_long_swap.json",
    ):
        trade, disc, fwd, fixings, cfg, _ = load_valuation_bundle(fixtures_dir / "valid" / fname)
        comp = run_independent_analytic_oracle(
            trade, disc, fwd, fixing_history=fixings, calendar_registry=calendar_registry, config=cfg
        )
        assert comp.status == "PASSED"
        assert comp.abs_npv_diff is not None and comp.abs_npv_diff <= 1e-6
        assert comp.abs_par_rate_diff is not None and comp.abs_par_rate_diff <= 1e-10

    ql_res = check_optional_quantlib_oracle(trade, disc, fwd)
    assert ql_res["status"] in {"NOT_RUN", "AVAILABLE"}


def test_dual_curve_distinctness_changes_valuation(
    fixtures_dir: Path, calendar_registry: CalendarRegistry
) -> None:
    trade, disc, fwd, fixings, cfg, _ = load_valuation_bundle(
        fixtures_dir / "valid" / "usd_5y_vanilla_swap.json"
    )
    dual_res = price_swap(
        trade, disc, fwd, fixing_history=fixings, calendar_registry=calendar_registry, config=cfg
    )
    # Build a single-curve forward snapshot equal to discount curve zero rates
    single_fwd = dataclasses.replace(
        disc, curve_id="USD-OIS-AS-FWD", role=fwd.role, index_name=fwd.index_name
    )
    single_res = price_swap(
        trade,
        disc,
        single_fwd,
        fixing_history=fixings,
        calendar_registry=calendar_registry,
        config=cfg,
    )
    # Forward curve is ~15-18 bps higher than OIS discount curve -> dual-curve floating PV is strictly larger
    assert dual_res.floating_leg_pv > single_res.floating_leg_pv + 50_000.0
    assert dual_res.par_rate > single_res.par_rate + 0.0010
