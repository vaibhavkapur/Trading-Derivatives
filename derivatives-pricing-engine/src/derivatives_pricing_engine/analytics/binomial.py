"""Cox-Ross-Rubinstein (CRR) binomial tree pricer and convergence analyzer."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

from ..contracts import ExerciseStyle, OptionType
from ..errors import NumericalStabilityError, ValidationError
from .black_scholes import price_black_scholes
from .day_count import discount_factor, intrinsic_payoff


@dataclass(frozen=True)
class BinomialOutput:
    """Valuation result and diagnostics from a CRR binomial tree."""

    price: float
    intrinsic: float
    time_value: float
    discount_factor: float
    steps: int
    up_factor: Optional[float]
    down_factor: Optional[float]
    risk_neutral_prob: Optional[float]
    early_exercise_nodes: int
    delta: Optional[float]
    gamma: Optional[float]
    theta: Optional[float]
    runtime_ms: float
    regime: str


def _payoff_array(spots: np.ndarray, strike: float, option_type: OptionType) -> np.ndarray:
    if option_type == OptionType.CALL:
        return np.maximum(spots - strike, 0.0)
    return np.maximum(strike - spots, 0.0)


def price_binomial(
    spot: float,
    strike: float,
    time_to_maturity: float,
    risk_free_rate: float,
    dividend_yield: float,
    volatility: float,
    option_type: OptionType,
    exercise_style: ExerciseStyle = ExerciseStyle.AMERICAN,
    steps: int = 400,
) -> BinomialOutput:
    """Price a European or American vanilla option using a CRR binomial lattice."""
    t_start = time.perf_counter()

    if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1:
        raise ValidationError("'steps' must be an integer >= 1.", details={"steps": steps})
    if spot <= 0.0 or strike <= 0.0:
        raise ValidationError("spot and strike must be strictly positive.")
    if time_to_maturity < 0.0:
        raise ValidationError("time_to_maturity must be non-negative.")
    if volatility < 0.0:
        raise ValidationError("volatility must be non-negative.")

    intrinsic = intrinsic_payoff(spot, strike, option_type)
    df_r = discount_factor(risk_free_rate, time_to_maturity)

    # Zero-maturity limit
    if time_to_maturity == 0.0:
        if option_type == OptionType.CALL:
            delta = 1.0 if spot > strike else (0.5 if spot == strike else 0.0)
        else:
            delta = -1.0 if spot < strike else (-0.5 if spot == strike else 0.0)
        elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        return BinomialOutput(
            price=intrinsic,
            intrinsic=intrinsic,
            time_value=0.0,
            discount_factor=1.0,
            steps=steps,
            up_factor=None,
            down_factor=None,
            risk_neutral_prob=None,
            early_exercise_nodes=0,
            delta=delta,
            gamma=0.0,
            theta=0.0,
            runtime_ms=elapsed_ms,
            regime="zero_maturity_limit",
        )

    # Zero-volatility deterministic trajectory limit
    if volatility == 0.0:
        if exercise_style == ExerciseStyle.EUROPEAN:
            fwd_spot = spot * math.exp((risk_free_rate - dividend_yield) * time_to_maturity)
            price = df_r * intrinsic_payoff(fwd_spot, strike, option_type)
            early_nodes = 0
        else:
            times = np.linspace(0.0, time_to_maturity, steps + 1)
            spots_path = spot * np.exp((risk_free_rate - dividend_yield) * times)
            discounted_payoffs = np.exp(-risk_free_rate * times) * _payoff_array(spots_path, strike, option_type)
            best_idx = int(np.argmax(discounted_payoffs))
            price = float(discounted_payoffs[best_idx])
            early_nodes = 1 if best_idx < steps and price > float(discounted_payoffs[-1]) + 1e-14 else 0

        elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        return BinomialOutput(
            price=price,
            intrinsic=intrinsic,
            time_value=price - intrinsic,
            discount_factor=df_r,
            steps=steps,
            up_factor=1.0,
            down_factor=1.0,
            risk_neutral_prob=None,
            early_exercise_nodes=early_nodes,
            delta=None,
            gamma=None,
            theta=None,
            runtime_ms=elapsed_ms,
            regime="zero_volatility_limit",
        )

    dt = time_to_maturity / steps
    u = math.exp(volatility * math.sqrt(dt))
    d = 1.0 / u
    growth = math.exp((risk_free_rate - dividend_yield) * dt)
    p = (growth - d) / (u - d)

    if not (0.0 < p < 1.0):
        drift = abs(risk_free_rate - dividend_yield)
        min_steps = int(math.floor(time_to_maturity * (drift / volatility) ** 2)) + 1
        raise NumericalStabilityError(
            "CRR risk-neutral probability outside (0, 1); increase step count for no-arbitrage stability.",
            details={
                "steps": steps,
                "risk_neutral_prob": p,
                "up_factor": u,
                "down_factor": d,
                "min_recommended_steps": min_steps,
            },
        )

    disc_step = math.exp(-risk_free_rate * dt)
    one_minus_p = 1.0 - p

    # Terminal layer j = 0..steps -> S_j = S0 * u^(2j - steps)
    j = np.arange(steps + 1, dtype=np.float64)
    terminal_spots = spot * np.power(u, 2.0 * j - steps)
    values = _payoff_array(terminal_spots, strike, option_type)

    early_exercise_nodes = 0
    v2: Optional[np.ndarray] = None
    v1: Optional[np.ndarray] = None

    for step in range(steps - 1, -1, -1):
        continuation = disc_step * (p * values[1:] + one_minus_p * values[:-1])
        if exercise_style == ExerciseStyle.AMERICAN:
            exponents = 2.0 * np.arange(step + 1, dtype=np.float64) - step
            layer_spots = spot * np.power(u, exponents)
            layer_intrinsic = _payoff_array(layer_spots, strike, option_type)
            exercised = layer_intrinsic > continuation + 1e-14
            early_exercise_nodes += int(np.count_nonzero(exercised))
            values = np.maximum(continuation, layer_intrinsic)
        else:
            values = continuation

        if step == 2:
            v2 = values.copy()
        elif step == 1:
            v1 = values.copy()

    price = float(values[0])

    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    if v1 is not None:
        s_u = spot * u
        s_d = spot * d
        delta = float((v1[1] - v1[0]) / (s_u - s_d))
    if v2 is not None and steps >= 2:
        s_uu = spot * u * u
        s_ud = spot
        s_dd = spot * d * d
        delta_up = (v2[2] - v2[1]) / (s_uu - s_ud)
        delta_dn = (v2[1] - v2[0]) / (s_ud - s_dd)
        gamma = float((delta_up - delta_dn) / (0.5 * (s_uu - s_dd)))
        theta = float((v2[1] - price) / (2.0 * dt))

    elapsed_ms = (time.perf_counter() - t_start) * 1000.0
    return BinomialOutput(
        price=price,
        intrinsic=intrinsic,
        time_value=price - intrinsic,
        discount_factor=df_r,
        steps=steps,
        up_factor=u,
        down_factor=d,
        risk_neutral_prob=p,
        early_exercise_nodes=early_exercise_nodes,
        delta=delta,
        gamma=gamma,
        theta=theta,
        runtime_ms=elapsed_ms,
        regime="crr_standard",
    )


def binomial_convergence_study(
    spot: float,
    strike: float,
    time_to_maturity: float,
    risk_free_rate: float,
    dividend_yield: float,
    volatility: float,
    option_type: OptionType,
    exercise_style: ExerciseStyle,
    step_counts: Sequence[int] = (50, 100, 200, 400, 800),
    reference_price: Optional[float] = None,
    reference_steps: int = 2000,
) -> dict[str, object]:
    """Run CRR binomial pricing across multiple step counts and measure convergence."""
    if len(step_counts) < 2:
        raise ValidationError("step_counts must contain at least 2 resolutions.")
    sorted_steps = sorted(step_counts)

    if reference_price is not None:
        ref_val = float(reference_price)
        ref_source = "external_reference"
    elif exercise_style == ExerciseStyle.EUROPEAN:
        ref_val = price_black_scholes(
            spot=spot,
            strike=strike,
            time_to_maturity=time_to_maturity,
            risk_free_rate=risk_free_rate,
            dividend_yield=dividend_yield,
            volatility=volatility,
            option_type=option_type,
        ).price
        ref_source = "black_scholes_analytic"
    else:
        ref_val = price_binomial(
            spot=spot,
            strike=strike,
            time_to_maturity=time_to_maturity,
            risk_free_rate=risk_free_rate,
            dividend_yield=dividend_yield,
            volatility=volatility,
            option_type=option_type,
            exercise_style=exercise_style,
            steps=max(reference_steps, sorted_steps[-1] * 2),
        ).price
        ref_source = f"crr_high_res_{max(reference_steps, sorted_steps[-1] * 2)}"

    rows: list[dict[str, object]] = []
    prev_abs_err: Optional[float] = None
    prev_n: Optional[int] = None

    for n in sorted_steps:
        out = price_binomial(
            spot=spot,
            strike=strike,
            time_to_maturity=time_to_maturity,
            risk_free_rate=risk_free_rate,
            dividend_yield=dividend_yield,
            volatility=volatility,
            option_type=option_type,
            exercise_style=exercise_style,
            steps=n,
        )
        signed_err = out.price - ref_val
        abs_err = abs(signed_err)
        order: Optional[float] = None
        if prev_abs_err is not None and prev_n is not None and abs_err > 0.0 and prev_abs_err > 0.0:
            order = math.log(prev_abs_err / abs_err) / math.log(n / prev_n)

        rows.append(
            {
                "steps": n,
                "price": out.price,
                "signed_error": signed_err,
                "abs_error": abs_err,
                "rel_error": abs_err / max(abs(ref_val), 1e-12),
                "empirical_order": order,
                "early_exercise_nodes": out.early_exercise_nodes,
                "runtime_ms": out.runtime_ms,
            }
        )
        prev_abs_err = abs_err
        prev_n = n

    return {
        "method": "binomial",
        "option_type": option_type.value,
        "exercise_style": exercise_style.value,
        "reference_price": ref_val,
        "reference_source": ref_source,
        "resolutions": rows,
    }
