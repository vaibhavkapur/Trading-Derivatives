"""Seeded Monte Carlo pricer with antithetic variates and confidence intervals."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np
from scipy.special import ndtri

from ..contracts import ExerciseStyle, OptionType
from ..errors import UnsupportedFeatureError, ValidationError
from .black_scholes import price_black_scholes
from .day_count import discount_factor, intrinsic_payoff


@dataclass(frozen=True)
class MonteCarloOutput:
    """Monte Carlo valuation estimate, uncertainty bounds, and runtime diagnostics."""

    price: float
    intrinsic: float
    time_value: float
    discount_factor: float
    paths: int
    seed: int
    antithetic: bool
    confidence_level: float
    standard_error: float
    ci_low: float
    ci_high: float
    ci_half_width: float
    variance_reduction_ratio: float
    runtime_ms: float
    regime: str


def _payoff_array(spots: np.ndarray, strike: float, option_type: OptionType) -> np.ndarray:
    if option_type == OptionType.CALL:
        return np.maximum(spots - strike, 0.0)
    return np.maximum(strike - spots, 0.0)


def price_monte_carlo(
    spot: float,
    strike: float,
    time_to_maturity: float,
    risk_free_rate: float,
    dividend_yield: float,
    volatility: float,
    option_type: OptionType,
    exercise_style: ExerciseStyle = ExerciseStyle.EUROPEAN,
    paths: int = 100000,
    seed: int = 42,
    antithetic: bool = True,
    confidence_level: float = 0.95,
) -> MonteCarloOutput:
    """Price a European option via seeded geometric Brownian motion Monte Carlo."""
    t_start = time.perf_counter()

    if exercise_style != ExerciseStyle.EUROPEAN:
        raise UnsupportedFeatureError(
            "Plain Monte Carlo only supports European exercise; American options require binomial or PDE solvers.",
            details={"exercise_style": exercise_style.value, "method": "monte_carlo"},
        )
    if isinstance(paths, bool) or not isinstance(paths, int) or paths < 2:
        raise ValidationError("'paths' must be an integer >= 2.", details={"paths": paths})
    if antithetic and paths % 2 != 0:
        raise ValidationError(
            "'paths' must be an even integer >= 2 when antithetic variates are enabled.",
            details={"paths": paths, "antithetic": antithetic},
        )
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValidationError("'seed' must be an integer.", details={"seed": seed})
    if not (0.0 < confidence_level < 1.0):
        raise ValidationError(
            "'confidence_level' must be strictly between 0 and 1.",
            details={"confidence_level": confidence_level},
        )
    if spot <= 0.0 or strike <= 0.0:
        raise ValidationError("spot and strike must be strictly positive.")
    if time_to_maturity < 0.0:
        raise ValidationError("time_to_maturity must be non-negative.")
    if volatility < 0.0:
        raise ValidationError("volatility must be non-negative.")

    intrinsic = intrinsic_payoff(spot, strike, option_type)
    df_r = discount_factor(risk_free_rate, time_to_maturity)

    # Zero maturity or zero volatility deterministic limits
    if time_to_maturity == 0.0 or volatility == 0.0:
        fwd_spot = spot * math.exp((risk_free_rate - dividend_yield) * time_to_maturity)
        price = df_r * intrinsic_payoff(fwd_spot, strike, option_type)
        regime = "zero_maturity_limit" if time_to_maturity == 0.0 else "zero_volatility_limit"
        elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        return MonteCarloOutput(
            price=price,
            intrinsic=intrinsic,
            time_value=price - intrinsic,
            discount_factor=df_r,
            paths=paths,
            seed=seed,
            antithetic=antithetic,
            confidence_level=confidence_level,
            standard_error=0.0,
            ci_low=price,
            ci_high=price,
            ci_half_width=0.0,
            variance_reduction_ratio=1.0,
            runtime_ms=elapsed_ms,
            regime=regime,
        )

    rng = np.random.default_rng(np.random.PCG64(seed))
    drift = (risk_free_rate - dividend_yield - 0.5 * volatility * volatility) * time_to_maturity
    diffusion = volatility * math.sqrt(time_to_maturity)

    if antithetic:
        pairs = paths // 2
        z = rng.standard_normal(pairs)
        s_pos = spot * np.exp(drift + diffusion * z)
        s_neg = spot * np.exp(drift - diffusion * z)
        pv_pos = df_r * _payoff_array(s_pos, strike, option_type)
        pv_neg = df_r * _payoff_array(s_neg, strike, option_type)
        paired_avg = 0.5 * (pv_pos + pv_neg)

        price = float(np.mean(paired_avg))
        std_err = float(np.std(paired_avg, ddof=1)) / math.sqrt(pairs) if pairs > 1 else 0.0

        # Estimate variance reduction ratio relative to uncoupled i.i.d. paths of same total count
        raw_var = 0.5 * (float(np.var(pv_pos, ddof=1)) + float(np.var(pv_neg, ddof=1))) if pairs > 1 else 0.0
        uncoupled_se_sq = raw_var / paths
        vrr = (uncoupled_se_sq / (std_err * std_err)) if std_err > 0.0 else 1.0
        regime = "mc_antithetic"
    else:
        z = rng.standard_normal(paths)
        spots_t = spot * np.exp(drift + diffusion * z)
        pv = df_r * _payoff_array(spots_t, strike, option_type)
        price = float(np.mean(pv))
        std_err = float(np.std(pv, ddof=1)) / math.sqrt(paths)
        vrr = 1.0
        regime = "mc_standard"

    z_crit = float(ndtri(0.5 + 0.5 * confidence_level))
    half_width = z_crit * std_err
    ci_low = price - half_width
    ci_high = price + half_width
    elapsed_ms = (time.perf_counter() - t_start) * 1000.0

    return MonteCarloOutput(
        price=price,
        intrinsic=intrinsic,
        time_value=price - intrinsic,
        discount_factor=df_r,
        paths=paths,
        seed=seed,
        antithetic=antithetic,
        confidence_level=confidence_level,
        standard_error=std_err,
        ci_low=ci_low,
        ci_high=ci_high,
        ci_half_width=half_width,
        variance_reduction_ratio=vrr,
        runtime_ms=elapsed_ms,
        regime=regime,
    )


def monte_carlo_coverage_study(
    spot: float,
    strike: float,
    time_to_maturity: float,
    risk_free_rate: float,
    dividend_yield: float,
    volatility: float,
    option_type: OptionType,
    trials: int = 200,
    paths_per_trial: int = 10000,
    seed_base: int = 10000,
    antithetic: bool = True,
    confidence_level: float = 0.95,
) -> dict[str, object]:
    """Verify empirical confidence interval coverage against analytic Black-Scholes."""
    if trials < 10:
        raise ValidationError("'trials' must be >= 10 for a meaningful coverage study.")

    ref = price_black_scholes(
        spot=spot,
        strike=strike,
        time_to_maturity=time_to_maturity,
        risk_free_rate=risk_free_rate,
        dividend_yield=dividend_yield,
        volatility=volatility,
        option_type=option_type,
    ).price

    covered = 0
    errors: list[float] = []
    half_widths: list[float] = []
    vrrs: list[float] = []
    t0 = time.perf_counter()

    for idx in range(trials):
        res = price_monte_carlo(
            spot=spot,
            strike=strike,
            time_to_maturity=time_to_maturity,
            risk_free_rate=risk_free_rate,
            dividend_yield=dividend_yield,
            volatility=volatility,
            option_type=option_type,
            paths=paths_per_trial,
            seed=seed_base + idx,
            antithetic=antithetic,
            confidence_level=confidence_level,
        )
        if res.ci_low <= ref <= res.ci_high:
            covered += 1
        errors.append(res.price - ref)
        half_widths.append(res.ci_half_width)
        vrrs.append(res.variance_reduction_ratio)

    total_runtime_ms = (time.perf_counter() - t0) * 1000.0
    err_arr = np.asarray(errors, dtype=np.float64)

    return {
        "reference_price": ref,
        "trials": trials,
        "paths_per_trial": paths_per_trial,
        "seed_base": seed_base,
        "antithetic": antithetic,
        "nominal_confidence_level": confidence_level,
        "covered_count": covered,
        "empirical_coverage": covered / trials,
        "mean_error": float(np.mean(err_arr)),
        "rmse": float(np.sqrt(np.mean(err_arr * err_arr))),
        "mean_ci_half_width": float(np.mean(half_widths)),
        "mean_variance_reduction_ratio": float(np.mean(vrrs)),
        "total_runtime_ms": total_runtime_ms,
    }
