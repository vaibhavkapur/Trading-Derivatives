"""Unit tests for Black-76 pricing, analytic Greeks, bounds enforcement, and IV inversion."""

from __future__ import annotations

import math

import pytest

from volatility_surface_engine import (
    InputBoundsError,
    black76_delta,
    black76_gamma,
    black76_price,
    black76_price_bounds,
    black76_vega,
    implied_volatility_black76,
)


@pytest.mark.parametrize("strike", [80.0, 92.0, 102.5, 112.0, 125.0])
@pytest.mark.parametrize("vol", [0.12, 0.25, 0.55])
@pytest.mark.parametrize("t_years", [0.10, 0.5, 2.0])
def test_black76_put_call_parity_and_iv_roundtrip(strike: float, vol: float, t_years: float) -> None:
    fwd = 102.5
    df = math.exp(-0.04 * t_years)
    c = black76_price(fwd, strike, t_years, vol, df, "call")
    p = black76_price(fwd, strike, t_years, vol, df, "put")

    # Exact put-call parity: C - P = DF * (F - K)
    assert abs((c - p) - df * (fwd - strike)) < 1e-11

    # Always invert the OTM option (and ITM option when time value is well above float64 eps)
    c_low, _ = black76_price_bounds(fwd, strike, df, "call")
    p_low, _ = black76_price_bounds(fwd, strike, df, "put")
    if c - c_low > 1e-8:
        iv_c = implied_volatility_black76(c, fwd, strike, t_years, df, "call")
        assert abs(iv_c - vol) < 1e-7
    if p - p_low > 1e-8:
        iv_p = implied_volatility_black76(p, fwd, strike, t_years, df, "put")
        assert abs(iv_p - vol) < 1e-7


def test_black76_greeks_match_finite_difference_oracle() -> None:
    fwd, strike, t_years, vol, df = 100.0, 105.0, 0.5, 0.24, 0.98
    eps_v = 1e-6
    eps_f = 1e-4

    # Vega
    num_vega = (
        black76_price(fwd, strike, t_years, vol + eps_v, df, "call")
        - black76_price(fwd, strike, t_years, vol - eps_v, df, "call")
    ) / (2.0 * eps_v)
    assert abs(black76_vega(fwd, strike, t_years, vol, df) - num_vega) < 1e-6

    # Delta (call and put)
    for opt in ("call", "put"):
        num_delta = (
            black76_price(fwd + eps_f, strike, t_years, vol, df, opt)
            - black76_price(fwd - eps_f, strike, t_years, vol, df, opt)
        ) / (2.0 * eps_f)
        assert abs(black76_delta(fwd, strike, t_years, vol, df, opt) - num_delta) < 1e-6

    # Gamma
    num_gamma = (
        black76_price(fwd + eps_f, strike, t_years, vol, df, "call")
        - 2.0 * black76_price(fwd, strike, t_years, vol, df, "call")
        + black76_price(fwd - eps_f, strike, t_years, vol, df, "call")
    ) / (eps_f * eps_f)
    assert abs(black76_gamma(fwd, strike, t_years, vol, df) - num_gamma) < 1e-5


def test_iv_inversion_enforces_strict_bounds_and_near_zero_vega() -> None:
    fwd, strike, t_years, df = 100.0, 90.0, 0.25, 0.99
    low, high = black76_price_bounds(fwd, strike, df, "call")

    # Below or at intrinsic bound
    with pytest.raises(InputBoundsError, match="no-arbitrage bounds"):
        implied_volatility_black76(low, fwd, strike, t_years, df, "call")
    with pytest.raises(InputBoundsError, match="no-arbitrage bounds"):
        implied_volatility_black76(low - 0.5, fwd, strike, t_years, df, "call")

    # Above upper bound
    with pytest.raises(InputBoundsError, match="no-arbitrage bounds"):
        implied_volatility_black76(high + 1.0, fwd, strike, t_years, df, "call")

    # NaN / non-positive inputs
    with pytest.raises(InputBoundsError):
        implied_volatility_black76(float("nan"), fwd, strike, t_years, df, "call")
    with pytest.raises(InputBoundsError):
        implied_volatility_black76(1.0, fwd, strike, -0.1, df, "call")

    # Near-zero vega deep OTM option rejected when min_vega is specified
    deep_k = 190.0
    tiny_t = 30.0 / 365.0
    tiny_price = black76_price(100.0, deep_k, tiny_t, 0.22, 0.996, "call")
    # If price is above solver lower bound, check min_vega rejection
    p_lo = black76_price(100.0, deep_k, tiny_t, 1e-5, 0.996, "call")
    if tiny_price > p_lo:
        with pytest.raises(InputBoundsError, match="near-zero vega"):
            implied_volatility_black76(tiny_price, 100.0, deep_k, tiny_t, 0.996, "call", min_vega=1e-3)
    else:
        with pytest.raises(InputBoundsError):
            implied_volatility_black76(tiny_price, 100.0, deep_k, tiny_t, 0.996, "call", min_vega=1e-3)
