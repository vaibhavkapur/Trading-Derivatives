"""Unit tests for analytic Black-Scholes-Merton pricer, parity, bounds, limits, and Greeks."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from derivatives_pricing_engine import ExerciseStyle, OptionType, UnsupportedFeatureError
from derivatives_pricing_engine.analytics.black_scholes import price_black_scholes


def _independent_erfc_bs(
    s: float, k: float, t: float, r: float, q: float, sigma: float, is_call: bool
) -> float:
    """Independent reference oracle using Python stdlib math.erfc (no SciPy)."""
    def norm_cdf(x: float) -> float:
        return 0.5 * math.erfc(-x / math.sqrt(2.0))

    d1 = (math.log(s / k) + (r - q + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    if is_call:
        return s * math.exp(-q * t) * norm_cdf(d1) - k * math.exp(-r * t) * norm_cdf(d2)
    return k * math.exp(-r * t) * norm_cdf(-d2) - s * math.exp(-q * t) * norm_cdf(-d1)


def test_reference_hull_fixtures_within_1e_8(fixtures_dir: Path) -> None:
    payload = json.loads((fixtures_dir / "reference_hull_examples.json").read_text(encoding="utf-8"))
    for case in payload["cases"]:
        opt_type = OptionType(case["option_type"])
        out = price_black_scholes(
            spot=case["spot"],
            strike=case["strike"],
            time_to_maturity=case["time_to_maturity"],
            risk_free_rate=case["risk_free_rate"],
            dividend_yield=case["dividend_yield"],
            volatility=case["volatility"],
            option_type=opt_type,
        )
        oracle = _independent_erfc_bs(
            case["spot"],
            case["strike"],
            case["time_to_maturity"],
            case["risk_free_rate"],
            case["dividend_yield"],
            case["volatility"],
            opt_type == OptionType.CALL,
        )
        assert abs(out.price - case["expected_price"]) <= 1e-8
        assert abs(out.price - oracle) <= 1e-12


@pytest.mark.parametrize(
    ("spot", "strike", "t", "r", "q", "vol"),
    [
        (100.0, 100.0, 1.0, 0.05, 0.0, 0.20),
        (100.0, 80.0, 0.5, 0.03, 0.02, 0.35),
        (100.0, 125.0, 2.0, -0.01, 0.04, 0.15),
        (50.0, 55.0, 0.08, 0.08, 0.01, 0.60),
        (100.0, 100.0, 1.0, 0.05, 0.02, 0.0),
        (100.0, 95.0, 0.0, 0.05, 0.02, 0.20),
    ],
)
def test_put_call_parity_and_intrinsic_bounds(
    spot: float, strike: float, t: float, r: float, q: float, vol: float
) -> None:
    call = price_black_scholes(spot, strike, t, r, q, vol, OptionType.CALL)
    put = price_black_scholes(spot, strike, t, r, q, vol, OptionType.PUT)

    rhs = spot * math.exp(-q * t) - strike * math.exp(-r * t)
    assert abs((call.price - put.price) - rhs) <= 1e-12

    # No-arbitrage European bounds
    assert call.price >= max(rhs, 0.0) - 1e-12
    assert call.price <= spot * math.exp(-q * t) + 1e-12
    assert put.price >= max(-rhs, 0.0) - 1e-12
    assert put.price <= strike * math.exp(-r * t) + 1e-12


def test_zero_maturity_and_zero_volatility_limits() -> None:
    # Zero maturity
    c0 = price_black_scholes(105.0, 100.0, 0.0, 0.05, 0.02, 0.25, OptionType.CALL)
    p0 = price_black_scholes(105.0, 100.0, 0.0, 0.05, 0.02, 0.25, OptionType.PUT)
    assert c0.price == 5.0 and c0.delta == 1.0 and c0.regime == "zero_maturity_limit"
    assert p0.price == 0.0 and p0.delta == 0.0 and p0.regime == "zero_maturity_limit"

    # Zero volatility continuity as sigma -> 0+
    c_zero = price_black_scholes(100.0, 100.0, 1.0, 0.05, 0.02, 0.0, OptionType.CALL)
    c_tiny = price_black_scholes(100.0, 100.0, 1.0, 0.05, 0.02, 1e-7, OptionType.CALL)
    assert c_zero.regime == "zero_volatility_limit"
    assert abs(c_zero.price - c_tiny.price) <= 1e-8

    p_zero = price_black_scholes(90.0, 100.0, 1.0, 0.05, 0.0, 0.0, OptionType.PUT)
    p_tiny = price_black_scholes(90.0, 100.0, 1.0, 0.05, 0.0, 1e-7, OptionType.PUT)
    assert abs(p_zero.price - p_tiny.price) <= 1e-8


@pytest.mark.parametrize("option_type", [OptionType.CALL, OptionType.PUT])
def test_greeks_match_central_finite_differences(option_type: OptionType) -> None:
    s, k, t, r, q, vol = 100.0, 102.0, 0.75, 0.045, 0.015, 0.25
    base = price_black_scholes(s, k, t, r, q, vol, option_type)

    eps_s = 1e-4
    up_s = price_black_scholes(s + eps_s, k, t, r, q, vol, option_type).price
    dn_s = price_black_scholes(s - eps_s, k, t, r, q, vol, option_type).price
    fd_delta = (up_s - dn_s) / (2.0 * eps_s)
    fd_gamma = (up_s - 2.0 * base.price + dn_s) / (eps_s * eps_s)

    eps_v = 1e-5
    fd_vega = (
        price_black_scholes(s, k, t, r, q, vol + eps_v, option_type).price
        - price_black_scholes(s, k, t, r, q, vol - eps_v, option_type).price
    ) / (2.0 * eps_v)

    eps_r = 1e-5
    fd_rho = (
        price_black_scholes(s, k, t, r + eps_r, q, vol, option_type).price
        - price_black_scholes(s, k, t, r - eps_r, q, vol, option_type).price
    ) / (2.0 * eps_r)

    eps_t = 1e-5
    # Theta = -dV/dT
    fd_theta = -(
        price_black_scholes(s, k, t + eps_t, r, q, vol, option_type).price
        - price_black_scholes(s, k, t - eps_t, r, q, vol, option_type).price
    ) / (2.0 * eps_t)

    assert abs(base.delta - fd_delta) <= 1e-6
    assert abs(base.gamma - fd_gamma) <= 1e-5
    assert abs(base.vega - fd_vega) <= 1e-5
    assert abs(base.rho - fd_rho) <= 1e-5
    assert abs(base.theta - fd_theta) <= 1e-5


def test_black_scholes_rejects_american_exercise() -> None:
    with pytest.raises(UnsupportedFeatureError):
        price_black_scholes(100.0, 100.0, 1.0, 0.05, 0.0, 0.2, OptionType.PUT, ExerciseStyle.AMERICAN)
