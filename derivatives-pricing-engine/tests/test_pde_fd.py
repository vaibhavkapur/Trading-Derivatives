"""Unit tests for finite-difference PDE solver, schemes, stability checks, and grid refinement."""

from __future__ import annotations

import pytest

from derivatives_pricing_engine import ExerciseStyle, NumericalStabilityError, OptionType, UnsupportedFeatureError
from derivatives_pricing_engine.analytics.binomial import price_binomial
from derivatives_pricing_engine.analytics.black_scholes import price_black_scholes
from derivatives_pricing_engine.analytics.pde_fd import pde_convergence_study, price_pde_fd


def test_pde_crank_nicolson_european_grid_refinement() -> None:
    study = pde_convergence_study(
        spot=100.0,
        strike=100.0,
        time_to_maturity=1.0,
        risk_free_rate=0.05,
        dividend_yield=0.02,
        volatility=0.20,
        option_type=OptionType.CALL,
        exercise_style=ExerciseStyle.EUROPEAN,
        resolutions=((100, 100), (200, 200), (400, 400)),
        scheme="crank_nicolson",
    )
    rows = study["resolutions"]
    assert len(rows) == 3
    # Error shrinks steadily with grid refinement (~4x per 2x refinement -> order ~2)
    assert rows[1]["abs_error"] < rows[0]["abs_error"]
    assert rows[2]["abs_error"] < rows[1]["abs_error"]
    assert rows[2]["empirical_order"] > 1.8
    assert rows[2]["abs_error"] < 0.005


def test_pde_american_put_matches_binomial_and_exceeds_european() -> None:
    eu_bs = price_black_scholes(100.0, 100.0, 1.0, 0.05, 0.0, 0.20, OptionType.PUT).price
    am_bin = price_binomial(100.0, 100.0, 1.0, 0.05, 0.0, 0.20, OptionType.PUT, ExerciseStyle.AMERICAN, steps=800).price
    am_pde = price_pde_fd(
        spot=100.0,
        strike=100.0,
        time_to_maturity=1.0,
        risk_free_rate=0.05,
        dividend_yield=0.0,
        volatility=0.20,
        option_type=OptionType.PUT,
        exercise_style=ExerciseStyle.AMERICAN,
        spot_steps=400,
        time_steps=400,
        scheme="crank_nicolson",
    )
    assert am_pde.psor_converged is True
    assert am_pde.price > eu_bs + 0.45
    assert abs(am_pde.price - am_bin) < 0.005


def test_pde_implicit_and_stable_explicit_schemes() -> None:
    bs = price_black_scholes(100.0, 100.0, 0.5, 0.05, 0.0, 0.20, OptionType.PUT).price

    imp = price_pde_fd(
        spot=100.0, strike=100.0, time_to_maturity=0.5, risk_free_rate=0.05,
        dividend_yield=0.0, volatility=0.20, option_type=OptionType.PUT,
        exercise_style=ExerciseStyle.EUROPEAN, spot_steps=200, time_steps=200, scheme="implicit",
    )
    assert abs(imp.price - bs) < 0.025

    # Stable explicit grid: M=80, N=400 for T=0.5 gives CFL = 0.00125 * (0.04 * 79^2 + 0.05) ~= 0.312 < 1.0
    exp_ok = price_pde_fd(
        spot=100.0, strike=100.0, time_to_maturity=0.5, risk_free_rate=0.05,
        dividend_yield=0.0, volatility=0.20, option_type=OptionType.PUT,
        exercise_style=ExerciseStyle.EUROPEAN, spot_steps=80, time_steps=400, scheme="explicit",
    )
    assert exp_ok.is_stable is True
    assert exp_ok.cfl_number < 1.0
    assert abs(exp_ok.price - bs) < 0.12


def test_pde_explicit_cfl_violation_raises_stability_error() -> None:
    with pytest.raises(NumericalStabilityError) as exc_info:
        price_pde_fd(
            spot=100.0, strike=100.0, time_to_maturity=1.0, risk_free_rate=0.05,
            dividend_yield=0.0, volatility=0.30, option_type=OptionType.PUT,
            exercise_style=ExerciseStyle.EUROPEAN, spot_steps=100, time_steps=50, scheme="explicit",
        )
    assert exc_info.value.details["cfl_number"] > 1.0
    assert exc_info.value.details["min_required_time_steps"] > 50


def test_pde_rejects_unsupported_scheme() -> None:
    with pytest.raises(UnsupportedFeatureError):
        price_pde_fd(100.0, 100.0, 1.0, 0.05, 0.0, 0.20, OptionType.CALL, scheme="leapfrog")
