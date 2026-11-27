"""Unit and boundary tests for static arbitrage detection, surface invalidation, and extrapolation rejection."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from volatility_surface_engine import (
    ArbitrageConfig,
    ArbitrageViolationError,
    ExtrapolationError,
    SVIParameters,
    SliceCalibrationResult,
    build_surface,
    check_surface_arbitrage,
    load_chain_snapshot,
    load_config,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "default_config.json"
FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"


def test_valid_reference_surface_has_zero_arbitrage_violations() -> None:
    cfg = load_config(CONFIG_PATH)
    chain = load_chain_snapshot(FIXTURES_DIR / "valid_chain_reference.json")
    surface = build_surface(chain, cfg)

    assert surface.is_valid
    assert surface.snapshot.arbitrage_check.butterfly_violation_count == 0
    assert surface.snapshot.arbitrage_check.calendar_violation_count == 0
    assert surface.snapshot.arbitrage_check.min_butterfly_density > 0.0
    assert surface.snapshot.arbitrage_check.min_call_convexity >= -1e-6
    assert surface.snapshot.arbitrage_check.min_calendar_variance_diff > 0.0


def test_calendar_arbitrage_violation_marks_surface_invalid_and_strict_mode_raises() -> None:
    cfg = load_config(CONFIG_PATH)
    arb_chain = load_chain_snapshot(FIXTURES_DIR / "arbitrage_violation_chain.json")

    # Non-strict mode marks surface invalid and reports violations
    arb_surface = build_surface(arb_chain, cfg)
    assert not arb_surface.is_valid
    assert arb_surface.snapshot.arbitrage_check.calendar_violation_count > 0
    assert arb_surface.snapshot.arbitrage_check.min_calendar_variance_diff < 0.0
    assert any(v.kind == "calendar_variance" for v in arb_surface.snapshot.arbitrage_check.violations)

    # Querying with require_valid_surface=True raises ArbitrageViolationError
    with pytest.raises(ArbitrageViolationError, match="marked invalid"):
        arb_surface.query(expiry_years=30.0 / 365.0, log_moneyness=0.0, require_valid_surface=True)

    # Strict build mode raises ArbitrageViolationError directly
    strict_cfg = replace(cfg, arbitrage=replace(cfg.arbitrage, reject_on_violation=True))
    with pytest.raises(ArbitrageViolationError, match="Static arbitrage violations"):
        build_surface(arb_chain, strict_cfg)


def test_butterfly_arbitrage_violation_detected_by_durrleman_and_call_convexity() -> None:
    # Construct a pathological raw SVI slice with extreme wing slope / curvature violating g(k) >= 0
    bad_slice = SliceCalibrationResult(
        expiry_timestamp="2026-04-15T16:00:00Z",
        time_to_expiry_years=0.25,
        forward=100.0,
        discount_factor=0.99,
        k_min=-0.30,
        k_max=0.30,
        quote_count=11,
        svi_params=SVIParameters(a=0.0005, b=1.60, rho=-0.95, m=0.0, sigma=0.005),
        sabr_params=None,
        svi_weighted_rmse_iv=0.0,
        svi_unweighted_rmse_iv=0.0,
        svi_max_abs_err_iv=0.0,
        sabr_weighted_rmse_iv=None,
        sabr_unweighted_rmse_iv=None,
        sabr_max_abs_err_iv=None,
        svi_converged=True,
        sabr_converged=False,
        svi_nfev=1,
        sabr_nfev=0,
        fitted_quotes=[],
    )
    arb = check_surface_arbitrage([bad_slice], ArbitrageConfig())
    assert not arb.is_valid
    assert arb.butterfly_violation_count > 0
    assert arb.min_butterfly_density < 0.0


def test_extrapolation_rejection_on_strike_and_maturity_axes() -> None:
    cfg = load_config(CONFIG_PATH)
    chain = load_chain_snapshot(FIXTURES_DIR / "valid_chain_reference.json")
    surface = build_surface(chain, cfg)

    # Valid interior query succeeds
    ok = surface.query(expiry_years=60.0 / 365.0, log_moneyness=0.0)
    assert not ok.extrapolated
    assert 0.10 < ok.implied_vol < 0.40

    # Strike / log-moneyness extrapolation rejected (low and high)
    with pytest.raises(ExtrapolationError, match="strike domain"):
        surface.query(expiry_years=90.0 / 365.0, log_moneyness=-0.50)
    with pytest.raises(ExtrapolationError, match="strike domain"):
        surface.query(expiry_years=90.0 / 365.0, strike=200.0)

    # Maturity extrapolation rejected (short and long)
    with pytest.raises(ExtrapolationError, match="maturity domain"):
        surface.query(expiry_years=5.0 / 365.0, log_moneyness=0.0)
    with pytest.raises(ExtrapolationError, match="maturity domain"):
        surface.query(expiry_years=2.0, log_moneyness=0.0)

    # Explicit opt-in marks extrapolated=True
    ext = surface.query(expiry_years=2.0, log_moneyness=0.0, allow_extrapolation=True)
    assert ext.extrapolated
    assert ext.implied_vol > 0.0
