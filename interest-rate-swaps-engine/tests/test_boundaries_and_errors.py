"""Boundary and failure behavior tests for invalid fixtures, edge regimes, and config errors."""

from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path

import pytest

from irs_engine.domain.calendars import CalendarRegistry
from irs_engine.domain.enums import CurveRole
from irs_engine.domain.errors import ConfigurationError, CurveError, ValidationError
from irs_engine.domain.pricer import price_swap
from irs_engine.domain.sensitivities import compute_sensitivities
from irs_engine.io import load_config_file, load_valuation_bundle


def test_invalid_fixtures_raise_explicit_errors(
    fixtures_dir: Path, calendar_registry: CalendarRegistry
) -> None:
    with pytest.raises(ValidationError) as e_dates:
        load_valuation_bundle(fixtures_dir / "invalid" / "invalid_dates.json")
    assert e_dates.value.error_code == "VALIDATION_ERROR"

    with pytest.raises(CurveError) as e_mono:
        load_valuation_bundle(fixtures_dir / "invalid" / "non_monotonic_curve.json")
    assert e_mono.value.error_code == "CURVE_ERROR"

    trade, disc, fwd, fixings, cfg, _ = load_valuation_bundle(
        fixtures_dir / "invalid" / "currency_mismatch.json"
    )
    with pytest.raises(CurveError) as e_ccy:
        price_swap(
            trade, disc, fwd, fixing_history=fixings, calendar_registry=calendar_registry, config=cfg
        )
    assert e_ccy.value.error_code == "CURVE_ERROR"


def test_role_and_valuation_date_and_index_mismatch_rejected(
    fixtures_dir: Path, calendar_registry: CalendarRegistry
) -> None:
    trade, disc, fwd, fixings, cfg, _ = load_valuation_bundle(
        fixtures_dir / "valid" / "usd_5y_vanilla_swap.json"
    )
    # Swapped roles (FORWARD passed as discount)
    with pytest.raises(CurveError):
        price_swap(
            trade, fwd, fwd, fixing_history=fixings, calendar_registry=calendar_registry, config=cfg
        )

    # Valuation date mismatch
    shifted_fwd = dataclasses.replace(fwd, valuation_date=date(2026, 1, 6))
    with pytest.raises(CurveError):
        price_swap(
            trade,
            disc,
            shifted_fwd,
            fixing_history=fixings,
            calendar_registry=calendar_registry,
            config=cfg,
        )

    # Index mismatch
    wrong_idx_fwd = dataclasses.replace(fwd, index_name="USD-SOFR-6M")
    with pytest.raises(ValidationError):
        price_swap(
            trade,
            disc,
            wrong_idx_fwd,
            fixing_history=fixings,
            calendar_registry=calendar_registry,
            config=cfg,
        )

    # Discount role passed as forward
    disc_as_fwd = dataclasses.replace(disc, role=CurveRole.DISCOUNT)
    with pytest.raises(CurveError):
        price_swap(
            trade,
            disc,
            disc_as_fwd,
            fixing_history=fixings,
            calendar_registry=calendar_registry,
            config=cfg,
        )


def test_boundary_fixtures_negative_rates_single_period_and_eom_leap(
    fixtures_dir: Path, calendar_registry: CalendarRegistry
) -> None:
    # 1. Negative EUR rates regime
    t_neg, d_neg, f_neg, h_neg, c_neg, _ = load_valuation_bundle(
        fixtures_dir / "boundary" / "negative_rates_eur_swap.json"
    )
    res_neg = price_swap(
        t_neg, d_neg, f_neg, fixing_history=h_neg, calendar_registry=calendar_registry, config=c_neg
    )
    assert res_neg.par_rate < 0.0
    assert all(cf.discount_factor > 1.0 for cf in res_neg.fixed_cashflows)
    sens_neg = compute_sensitivities(
        t_neg, d_neg, f_neg, fixing_history=h_neg, calendar_registry=calendar_registry, config=c_neg
    )
    assert sens_neg.reconciliation_within_tolerance is True

    # 2. Single-period swap
    t_1p, d_1p, f_1p, h_1p, c_1p, _ = load_valuation_bundle(
        fixtures_dir / "boundary" / "single_period_swap.json"
    )
    res_1p = price_swap(
        t_1p, d_1p, f_1p, fixing_history=h_1p, calendar_registry=calendar_registry, config=c_1p
    )
    assert len(res_1p.fixed_cashflows) == 1
    assert len(res_1p.floating_cashflows) == 1

    # 3. Leap-year EOM GBP swap
    t_eom, d_eom, f_eom, h_eom, c_eom, _ = load_valuation_bundle(
        fixtures_dir / "boundary" / "eom_leap_year_swap.json"
    )
    res_eom = price_swap(
        t_eom, d_eom, f_eom, fixing_history=h_eom, calendar_registry=calendar_registry, config=c_eom
    )
    assert len(res_eom.fixed_cashflows) == 4
    assert any(cf.accrual_end == date(2028, 2, 29) for cf in res_eom.fixed_cashflows)


def test_missing_config_raises_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        load_config_file(tmp_path / "does_not_exist.json")
    assert exc_info.value.error_code == "CONFIGURATION_ERROR"

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not-valid-json", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config_file(bad_json)
