"""Analytic Black-Scholes-Merton pricer for European vanilla options."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from scipy.special import ndtr

from ..contracts import ExerciseStyle, OptionType
from ..errors import UnsupportedFeatureError, ValidationError
from .day_count import discount_factor, intrinsic_payoff

_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    return float(ndtr(x))


def _norm_pdf(x: float) -> float:
    return _INV_SQRT_2PI * math.exp(-0.5 * x * x)


@dataclass(frozen=True)
class BlackScholesOutput:
    """Closed-form valuation and sensitivities for a European option."""

    price: float
    intrinsic: float
    time_value: float
    discount_factor: float
    dividend_discount_factor: float
    forward: float
    d1: Optional[float]
    d2: Optional[float]
    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float
    regime: str


def price_black_scholes(
    spot: float,
    strike: float,
    time_to_maturity: float,
    risk_free_rate: float,
    dividend_yield: float,
    volatility: float,
    option_type: OptionType,
    exercise_style: ExerciseStyle = ExerciseStyle.EUROPEAN,
) -> BlackScholesOutput:
    """Price a European option using the exact Black-Scholes-Merton formula.

    Units:
    - `price`, `intrinsic`, `time_value`: per 1 unit of underlying in contract currency.
    - `delta`: dV / dS (dimensionless).
    - `gamma`: d^2V / dS^2 (1 / currency).
    - `vega`: dV / d(sigma) per 1.0 absolute volatility unit (divide by 100 for 1% vol move).
    - `theta`: dV / dt = -dV / dT per year (divide by 365 for calendar-day decay).
    - `rho`: dV / dr per 1.0 absolute rate unit (divide by 100 for 1% rate move).
    """
    if exercise_style != ExerciseStyle.EUROPEAN:
        raise UnsupportedFeatureError(
            "Analytic Black-Scholes only supports European exercise; use 'binomial' or 'pde_fd' for American options.",
            details={"exercise_style": exercise_style.value, "method": "black_scholes"},
        )
    if spot <= 0.0 or strike <= 0.0:
        raise ValidationError("spot and strike must be strictly positive.")
    if time_to_maturity < 0.0:
        raise ValidationError("time_to_maturity must be non-negative.")
    if volatility < 0.0:
        raise ValidationError("volatility must be non-negative.")

    intrinsic = intrinsic_payoff(spot, strike, option_type)
    df_r = discount_factor(risk_free_rate, time_to_maturity)
    df_q = discount_factor(dividend_yield, time_to_maturity)
    forward = spot * math.exp((risk_free_rate - dividend_yield) * time_to_maturity)

    # Exact zero-maturity limit (T == 0)
    if time_to_maturity == 0.0:
        if option_type == OptionType.CALL:
            delta = 1.0 if spot > strike else (0.5 if spot == strike else 0.0)
        else:
            delta = -1.0 if spot < strike else (-0.5 if spot == strike else 0.0)
        return BlackScholesOutput(
            price=intrinsic,
            intrinsic=intrinsic,
            time_value=0.0,
            discount_factor=1.0,
            dividend_discount_factor=1.0,
            forward=spot,
            d1=None,
            d2=None,
            delta=delta,
            gamma=0.0,
            vega=0.0,
            theta=0.0,
            rho=0.0,
            regime="zero_maturity_limit",
        )

    # Exact zero-volatility deterministic forward limit (sigma == 0, T > 0)
    if volatility == 0.0:
        disc_spot = spot * df_q
        disc_strike = strike * df_r
        if option_type == OptionType.CALL:
            price = max(disc_spot - disc_strike, 0.0)
            if disc_spot > disc_strike:
                delta = df_q
                theta = -risk_free_rate * disc_strike + dividend_yield * disc_spot
                rho = strike * time_to_maturity * df_r
            elif disc_spot == disc_strike:
                delta = 0.5 * df_q
                theta = 0.0
                rho = 0.0
            else:
                delta = 0.0
                theta = 0.0
                rho = 0.0
        else:
            price = max(disc_strike - disc_spot, 0.0)
            if disc_strike > disc_spot:
                delta = -df_q
                theta = risk_free_rate * disc_strike - dividend_yield * disc_spot
                rho = -strike * time_to_maturity * df_r
            elif disc_strike == disc_spot:
                delta = -0.5 * df_q
                theta = 0.0
                rho = 0.0
            else:
                delta = 0.0
                theta = 0.0
                rho = 0.0

        return BlackScholesOutput(
            price=price,
            intrinsic=intrinsic,
            time_value=price - intrinsic,
            discount_factor=df_r,
            dividend_discount_factor=df_q,
            forward=forward,
            d1=None,
            d2=None,
            delta=delta,
            gamma=0.0,
            vega=0.0,
            theta=theta,
            rho=rho,
            regime="zero_volatility_limit",
        )

    sqrt_t = math.sqrt(time_to_maturity)
    vol_sqrt_t = volatility * sqrt_t
    d1 = (math.log(spot / strike) + (risk_free_rate - dividend_yield + 0.5 * volatility * volatility) * time_to_maturity) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t

    pdf_d1 = _norm_pdf(d1)
    disc_spot = spot * df_q
    disc_strike = strike * df_r

    if option_type == OptionType.CALL:
        nd1 = _norm_cdf(d1)
        nd2 = _norm_cdf(d2)
        price = disc_spot * nd1 - disc_strike * nd2
        delta = df_q * nd1
        theta = -(disc_spot * pdf_d1 * volatility) / (2.0 * sqrt_t) - risk_free_rate * disc_strike * nd2 + dividend_yield * disc_spot * nd1
        rho = strike * time_to_maturity * df_r * nd2
    else:
        n_neg_d1 = _norm_cdf(-d1)
        n_neg_d2 = _norm_cdf(-d2)
        price = disc_strike * n_neg_d2 - disc_spot * n_neg_d1
        delta = -df_q * n_neg_d1
        theta = -(disc_spot * pdf_d1 * volatility) / (2.0 * sqrt_t) + risk_free_rate * disc_strike * n_neg_d2 - dividend_yield * disc_spot * n_neg_d1
        rho = -strike * time_to_maturity * df_r * n_neg_d2

    gamma = (df_q * pdf_d1) / (spot * vol_sqrt_t)
    vega = disc_spot * pdf_d1 * sqrt_t

    # Clamp numerical noise below zero for extreme deep OTM
    price = max(price, 0.0)

    return BlackScholesOutput(
        price=price,
        intrinsic=intrinsic,
        time_value=price - intrinsic,
        discount_factor=df_r,
        dividend_discount_factor=df_q,
        forward=forward,
        d1=d1,
        d2=d2,
        delta=delta,
        gamma=gamma,
        vega=vega,
        theta=theta,
        rho=rho,
        regime="analytic_standard",
    )
