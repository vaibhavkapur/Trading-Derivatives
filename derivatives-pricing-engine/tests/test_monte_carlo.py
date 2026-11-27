"""Unit tests for seeded Monte Carlo with antithetic variates and confidence intervals."""

from __future__ import annotations

import pytest

from derivatives_pricing_engine import ExerciseStyle, OptionType, UnsupportedFeatureError, ValidationError
from derivatives_pricing_engine.analytics.black_scholes import price_black_scholes
from derivatives_pricing_engine.analytics.monte_carlo import monte_carlo_coverage_study, price_monte_carlo


def test_monte_carlo_reproducible_and_close_to_black_scholes() -> None:
    bs = price_black_scholes(100.0, 100.0, 1.0, 0.05, 0.02, 0.20, OptionType.CALL).price
    mc1 = price_monte_carlo(100.0, 100.0, 1.0, 0.05, 0.02, 0.20, OptionType.CALL, paths=100000, seed=42, antithetic=True)
    mc2 = price_monte_carlo(100.0, 100.0, 1.0, 0.05, 0.02, 0.20, OptionType.CALL, paths=100000, seed=42, antithetic=True)

    assert mc1.price == mc2.price
    assert mc1.standard_error == mc2.standard_error
    assert mc1.ci_low == mc2.ci_low
    assert mc1.ci_high == mc2.ci_high
    assert mc1.ci_low < mc1.price < mc1.ci_high
    assert abs(mc1.price - bs) <= 3.5 * mc1.standard_error
    assert mc1.variance_reduction_ratio > 1.5
    assert mc1.runtime_ms > 0.0


def test_monte_carlo_confidence_interval_empirical_coverage() -> None:
    study = monte_carlo_coverage_study(
        spot=100.0,
        strike=100.0,
        time_to_maturity=1.0,
        risk_free_rate=0.05,
        dividend_yield=0.02,
        volatility=0.20,
        option_type=OptionType.CALL,
        trials=120,
        paths_per_trial=8000,
        seed_base=5000,
        antithetic=True,
        confidence_level=0.95,
    )
    # Binomial 95% confidence interval for 120 trials at p=0.95 is roughly [0.89, 0.99]
    assert 0.88 <= study["empirical_coverage"] <= 1.0
    assert abs(study["mean_error"]) < 0.05
    assert study["mean_variance_reduction_ratio"] > 1.5


def test_monte_carlo_validation_and_unsupported_american() -> None:
    with pytest.raises(UnsupportedFeatureError):
        price_monte_carlo(100.0, 100.0, 1.0, 0.05, 0.0, 0.2, OptionType.PUT, ExerciseStyle.AMERICAN, paths=1000, seed=1)

    with pytest.raises(ValidationError):
        price_monte_carlo(100.0, 100.0, 1.0, 0.05, 0.0, 0.2, OptionType.CALL, paths=101, seed=1, antithetic=True)

    with pytest.raises(ValidationError):
        price_monte_carlo(100.0, 100.0, 1.0, 0.05, 0.0, 0.2, OptionType.CALL, paths=1000, seed=1, confidence_level=1.0)


def test_monte_carlo_zero_limits() -> None:
    z_t = price_monte_carlo(105.0, 100.0, 0.0, 0.05, 0.0, 0.2, OptionType.CALL, paths=1000, seed=1)
    assert z_t.price == 5.0 and z_t.standard_error == 0.0 and z_t.ci_low == 5.0 and z_t.ci_high == 5.0

    z_v = price_monte_carlo(100.0, 100.0, 1.0, 0.05, 0.0, 0.0, OptionType.CALL, paths=1000, seed=1)
    bs_v = price_black_scholes(100.0, 100.0, 1.0, 0.05, 0.0, 0.0, OptionType.CALL)
    assert z_v.price == pytest.approx(bs_v.price, abs=1e-12)
    assert z_v.standard_error == 0.0
