"""Integration tests for known historical vs projected fixings and missing fixing history."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from irs_engine.domain.calendars import CalendarRegistry
from irs_engine.domain.enums import (
    FixingFallbackPolicy,
    FixingSource,
    ValuationDateFixingPolicy,
)
from irs_engine.domain.errors import MissingFixingError
from irs_engine.domain.models import FixingHistory, ValuationConfig
from irs_engine.domain.pricer import price_swap
from irs_engine.io import load_valuation_bundle


def test_seasoned_swap_uses_settled_historical_and_projected_fixings(
    fixtures_dir: Path, calendar_registry: CalendarRegistry
) -> None:
    trade, disc, fwd, fixings, cfg, _ = load_valuation_bundle(
        fixtures_dir / "valid" / "seasoned_swap_known_fixings.json"
    )
    res = price_swap(
        trade, disc, fwd, fixing_history=fixings, calendar_registry=calendar_registry, config=cfg
    )

    assert res.diagnostics["fixed_periods_settled"] == 1
    assert res.diagnostics["floating_periods_settled"] == 2
    assert res.diagnostics["historical_fixings_used"] == 1
    assert res.diagnostics["projected_fixings_used"] > 0
    assert res.diagnostics["fallback_fixings_used"] == 0

    # Verify settled periods contribute zero PV
    settled_flt = [cf for cf in res.floating_cashflows if cf.status == "SETTLED"]
    assert len(settled_flt) == 2
    assert all(cf.present_value == 0.0 for cf in settled_flt)

    # Active period fixed on 2026-01-05 must use known historical fixing 0.0422
    hist_flt = [cf for cf in res.floating_cashflows if cf.fixing_source == FixingSource.HISTORICAL]
    assert len(hist_flt) == 1
    assert hist_flt[0].rate == pytest.approx(0.0422)
    assert hist_flt[0].all_in_rate == pytest.approx(0.0422 + trade.floating_leg.spread)


def test_missing_historical_fixing_raises_error_by_default(
    fixtures_dir: Path, calendar_registry: CalendarRegistry
) -> None:
    trade, disc, fwd, fixings, cfg, _ = load_valuation_bundle(
        fixtures_dir / "invalid" / "missing_historical_fixing.json"
    )
    with pytest.raises(MissingFixingError) as exc_info:
        price_swap(
            trade, disc, fwd, fixing_history=fixings, calendar_registry=calendar_registry, config=cfg
        )
    assert exc_info.value.error_code == "MISSING_FIXING"
    assert exc_info.value.details["index_name"] == "USD-SOFR-3M"


def test_missing_historical_fixing_explicit_fallback_policy(
    fixtures_dir: Path, calendar_registry: CalendarRegistry
) -> None:
    trade, disc, fwd, fixings, _cfg, _ = load_valuation_bundle(
        fixtures_dir / "invalid" / "missing_historical_fixing.json"
    )
    fallback_cfg = ValuationConfig(missing_fixing_policy=FixingFallbackPolicy.PROJECT_FALLBACK)
    res = price_swap(
        trade,
        disc,
        fwd,
        fixing_history=fixings,
        calendar_registry=calendar_registry,
        config=fallback_cfg,
    )
    assert res.diagnostics["fallback_fixings_used"] == 1
    fb_rows = [
        cf for cf in res.floating_cashflows if cf.fixing_source == FixingSource.FALLBACK_PROJECTED
    ]
    assert len(fb_rows) == 1


def test_valuation_date_fixing_policy(
    fixtures_dir: Path, calendar_registry: CalendarRegistry
) -> None:
    # hand_calculated_2y_oracle has fixing_lag_days=0 and effective_date == valuation_date (2026-01-05)
    trade, disc, fwd, _fixings, _cfg, _ = load_valuation_bundle(
        fixtures_dir / "valid" / "hand_calculated_2y_oracle.json"
    )
    # Default ALLOW_PROJECT projects when valuation-date fixing is absent
    res_allow = price_swap(
        trade,
        disc,
        fwd,
        fixing_history=FixingHistory(),
        calendar_registry=calendar_registry,
        config=ValuationConfig(valuation_date_fixing_policy=ValuationDateFixingPolicy.ALLOW_PROJECT),
    )
    assert res_allow.floating_cashflows[0].fixing_source == FixingSource.PROJECTED

    # REQUIRE_HISTORICAL raises MissingFixingError if absent on valuation_date
    with pytest.raises(MissingFixingError):
        price_swap(
            trade,
            disc,
            fwd,
            fixing_history=FixingHistory(),
            calendar_registry=calendar_registry,
            config=ValuationConfig(
                valuation_date_fixing_policy=ValuationDateFixingPolicy.REQUIRE_HISTORICAL
            ),
        )
