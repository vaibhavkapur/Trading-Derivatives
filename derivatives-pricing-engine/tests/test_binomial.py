"""Unit tests for CRR binomial tree pricing, early exercise, and convergence."""

from __future__ import annotations

import pytest

from derivatives_pricing_engine import ExerciseStyle, NumericalStabilityError, OptionType
from derivatives_pricing_engine.analytics.binomial import binomial_convergence_study, price_binomial
from derivatives_pricing_engine.analytics.black_scholes import price_black_scholes


def test_binomial_european_converges_to_black_scholes() -> None:
    study = binomial_convergence_study(
        spot=100.0,
        strike=100.0,
        time_to_maturity=1.0,
        risk_free_rate=0.05,
        dividend_yield=0.02,
        volatility=0.20,
        option_type=OptionType.CALL,
        exercise_style=ExerciseStyle.EUROPEAN,
        step_counts=(50, 200, 800),
    )
    rows = study["resolutions"]
    assert len(rows) == 3
    assert rows[-1]["abs_error"] < rows[0]["abs_error"]
    assert rows[-1]["abs_error"] < 0.005


def test_american_call_zero_dividend_equals_european() -> None:
    bs = price_black_scholes(100.0, 100.0, 1.0, 0.05, 0.0, 0.25, OptionType.CALL)
    am_call = price_binomial(100.0, 100.0, 1.0, 0.05, 0.0, 0.25, OptionType.CALL, ExerciseStyle.AMERICAN, steps=500)
    eu_call = price_binomial(100.0, 100.0, 1.0, 0.05, 0.0, 0.25, OptionType.CALL, ExerciseStyle.EUROPEAN, steps=500)
    assert am_call.early_exercise_nodes == 0
    assert abs(am_call.price - eu_call.price) <= 1e-12
    assert abs(am_call.price - bs.price) <= 0.01


def test_american_put_has_positive_early_exercise_premium() -> None:
    eu_bs = price_black_scholes(100.0, 100.0, 1.0, 0.05, 0.0, 0.20, OptionType.PUT)
    am_put = price_binomial(100.0, 100.0, 1.0, 0.05, 0.0, 0.20, OptionType.PUT, ExerciseStyle.AMERICAN, steps=500)
    assert am_put.price > eu_bs.price + 0.45
    assert am_put.price >= am_put.intrinsic
    assert am_put.early_exercise_nodes > 0


def test_american_call_positive_dividend_early_exercise_premium() -> None:
    eu_bs = price_black_scholes(120.0, 100.0, 1.0, 0.03, 0.08, 0.20, OptionType.CALL)
    am_call = price_binomial(120.0, 100.0, 1.0, 0.03, 0.08, 0.20, OptionType.CALL, ExerciseStyle.AMERICAN, steps=400)
    assert am_call.price > eu_bs.price
    assert am_call.early_exercise_nodes > 0


def test_binomial_zero_maturity_and_zero_vol_limits() -> None:
    z_t = price_binomial(90.0, 100.0, 0.0, 0.05, 0.0, 0.2, OptionType.PUT, ExerciseStyle.AMERICAN, steps=50)
    assert z_t.price == 10.0 and z_t.regime == "zero_maturity_limit"

    # Zero vol American put with r > 0 exercises immediately for intrinsic K - S = 10
    z_v = price_binomial(90.0, 100.0, 1.0, 0.05, 0.0, 0.0, OptionType.PUT, ExerciseStyle.AMERICAN, steps=50)
    assert z_v.price == pytest.approx(10.0, abs=1e-12)
    assert z_v.regime == "zero_volatility_limit"


def test_binomial_detects_no_arbitrage_probability_violation() -> None:
    # Very high drift vs tiny volatility with few steps causes p outside (0, 1)
    with pytest.raises(NumericalStabilityError):
        price_binomial(100.0, 100.0, 1.0, 0.50, 0.0, 0.05, OptionType.CALL, ExerciseStyle.EUROPEAN, steps=2)
