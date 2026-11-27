"""Unit tests for quote cleaning, forward estimation, edge cases, and Polars export."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from volatility_surface_engine import (
    CalibrationConfig,
    ExpiryMarketData,
    OptionChainSnapshot,
    OptionQuote,
    build_surface,
    clean_and_invert_quotes,
    iv_quotes_to_polars,
    load_chain_snapshot,
    load_config,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "default_config.json"
FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"


def test_put_call_parity_forward_estimation_on_reference_chain() -> None:
    cfg = load_config(CONFIG_PATH)
    chain = load_chain_snapshot(FIXTURES_DIR / "valid_chain_reference.json")
    iv_quotes, fwd_estimates, _rejections, _counts, warnings = clean_and_invert_quotes(chain, cfg)

    assert len(iv_quotes) == 44  # 4 expiries * 11 unique strikes after OTM wing preference
    assert len(fwd_estimates) == 4
    assert not warnings
    for est in fwd_estimates:
        assert est.parity_pairs_used >= 3
        assert est.estimated_forward is not None
        assert est.relative_difference is not None
        assert est.relative_difference < 2e-3


def test_forward_discrepancy_emits_diagnostic_warning() -> None:
    cfg = load_config(CONFIG_PATH)
    chain = load_chain_snapshot(FIXTURES_DIR / "valid_chain_reference.json")
    # Perturb supplied forward on first expiry by 10% while keeping quotes unchanged
    bad_exp0 = replace(chain.expiries[0], forward=chain.expiries[0].forward * 1.10)
    perturbed = replace(chain, expiries=[bad_exp0] + list(chain.expiries[1:]))
    _, fwd_estimates, _, _, warnings = clean_and_invert_quotes(perturbed, cfg)

    assert any("Forward parity discrepancy" in w for w in warnings)
    assert fwd_estimates[0].relative_difference is not None
    assert fwd_estimates[0].relative_difference > 0.05


def test_edge_cases_wide_crossed_near_zero_vega_and_missing_strikes() -> None:
    cfg = load_config(CONFIG_PATH)
    chain = load_chain_snapshot(FIXTURES_DIR / "edge_cases_chain.json")
    iv_quotes, _fwds, rejections, counts, _warnings = clean_and_invert_quotes(chain, cfg)

    # Every injected edge case must be cleanly caught with explicit reason codes
    assert counts.get("crossed_quote", 0) >= 1
    assert counts.get("wide_relative_spread", 0) >= 1
    assert counts.get("wide_absolute_spread", 0) >= 1
    assert counts.get("non_positive_bid", 0) >= 1
    assert counts.get("intrinsic_bound_violation", 0) >= 1
    assert counts.get("expired_or_short_expiry", 0) >= 1
    # Deep OTM quote caught either by near_zero_vega or iv_inversion_failed (never accepted!)
    rejected_ids = {r.quote_id for r in rejections}
    assert "edge-near-zero-vega-01" in rejected_ids
    assert counts.get("near_zero_vega", 0) + counts.get("iv_inversion_failed", 0) >= 1

    # Despite missing strikes & dirty quotes, remaining clean quotes calibrate a valid surface
    surface = build_surface(chain, cfg)
    assert surface.is_valid
    assert len(surface.slices) == 2
    assert len(iv_quotes) == 14


def test_polars_dataframe_export() -> None:
    pl = pytest.importorskip("polars")
    cfg = load_config(CONFIG_PATH)
    chain = load_chain_snapshot(FIXTURES_DIR / "valid_chain_reference.json")
    iv_quotes, _, _, _, _ = clean_and_invert_quotes(chain, cfg)
    df = iv_quotes_to_polars(iv_quotes)
    assert isinstance(df, pl.DataFrame)
    assert df.height == len(iv_quotes)
    assert "implied_vol" in df.columns
    assert "log_moneyness" in df.columns
