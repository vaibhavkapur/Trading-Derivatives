"""Tests for par rate, parallel PV01, convexity, and key-rate (KR01) reconciliation."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from irs_engine.domain.calendars import CalendarRegistry
from irs_engine.domain.enums import FiniteDifferenceScheme, SwapDirection
from irs_engine.domain.sensitivities import compute_sensitivities
from irs_engine.io import load_valuation_bundle


def test_key_rate_reconciles_with_parallel_pv01(
    fixtures_dir: Path, calendar_registry: CalendarRegistry
) -> None:
    trade, disc, fwd, fixings, cfg, _ = load_valuation_bundle(
        fixtures_dir / "valid" / "usd_5y_vanilla_swap.json"
    )
    sens = compute_sensitivities(
        trade, disc, fwd, fixing_history=fixings, calendar_registry=calendar_registry, config=cfg
    )

    # Payer swap gains value when interest rates rise -> positive parallel PV01
    assert sens.parallel_pv01 > 0.0
    assert sens.forward_pv01 > 0.0

    # Key-rate reconciliation: per-curve and combined
    assert abs(sens.discount_reconciliation_diff) <= 0.05
    assert abs(sens.forward_reconciliation_diff) <= 0.05
    assert abs(sens.total_reconciliation_diff) <= cfg.kr01_reconciliation_abs_tolerance
    assert sens.total_reconciliation_rel_diff <= cfg.kr01_reconciliation_rel_tolerance
    assert sens.reconciliation_within_tolerance is True

    # Final payment (2031-01-05) is 1826/365 = 5.00274Y (includes 2028 leap day),
    # so 7Y carries a tiny linear-interpolation bleed while 10Y must be strictly zero.
    for kr in sens.discount_kr01 + sens.forward_kr01:
        if kr.tenor_years >= 10.0:
            assert abs(kr.kr01) < 1e-10


def test_payer_receiver_sensitivity_symmetry(
    fixtures_dir: Path, calendar_registry: CalendarRegistry
) -> None:
    trade, disc, fwd, fixings, cfg, _ = load_valuation_bundle(
        fixtures_dir / "valid" / "usd_5y_vanilla_swap.json"
    )
    recv_trade = dataclasses.replace(trade, direction=SwapDirection.RECEIVER)

    s_payer = compute_sensitivities(
        trade, disc, fwd, fixing_history=fixings, calendar_registry=calendar_registry, config=cfg
    )
    s_recv = compute_sensitivities(
        recv_trade,
        disc,
        fwd,
        fixing_history=fixings,
        calendar_registry=calendar_registry,
        config=cfg,
    )

    assert abs(s_payer.parallel_pv01 + s_recv.parallel_pv01) <= 1e-9
    assert abs(s_payer.discount_pv01 + s_recv.discount_pv01) <= 1e-9
    assert abs(s_payer.forward_pv01 + s_recv.forward_pv01) <= 1e-9
    assert abs(s_payer.parallel_convexity + s_recv.parallel_convexity) <= 1e-9
    for kp, kr in zip(s_payer.discount_kr01, s_recv.discount_kr01):
        assert abs(kp.kr01 + kr.kr01) <= 1e-9
    for kp, kr in zip(s_payer.forward_kr01, s_recv.forward_kr01):
        assert abs(kp.kr01 + kr.kr01) <= 1e-9


def test_central_vs_forward_finite_difference_close(
    fixtures_dir: Path, calendar_registry: CalendarRegistry
) -> None:
    trade, disc, fwd, fixings, cfg, _ = load_valuation_bundle(
        fixtures_dir / "valid" / "usd_5y_vanilla_swap.json"
    )
    s_cent = compute_sensitivities(
        trade,
        disc,
        fwd,
        fixing_history=fixings,
        calendar_registry=calendar_registry,
        config=cfg,
        scheme=FiniteDifferenceScheme.CENTRAL,
    )
    s_fwd = compute_sensitivities(
        trade,
        disc,
        fwd,
        fixing_history=fixings,
        calendar_registry=calendar_registry,
        config=cfg,
        scheme=FiniteDifferenceScheme.FORWARD,
    )
    # 1bp forward difference agrees with central difference to within ~0.2% (convexity term)
    rel_diff = abs(s_cent.parallel_pv01 - s_fwd.parallel_pv01) / abs(s_cent.parallel_pv01)
    assert rel_diff < 0.005
    assert s_fwd.reconciliation_within_tolerance is True
