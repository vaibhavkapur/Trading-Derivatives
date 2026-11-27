"""Black-76 option pricing, analytic Greeks, arbitrage bounds, and robust IV inversion."""

from __future__ import annotations

import math
from typing import Tuple

from scipy.optimize import brentq
from scipy.special import ndtr

from volatility_surface_engine.models import InputBoundsError

_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)


def _norm_pdf(x: float) -> float:
    return _INV_SQRT_2PI * math.exp(-0.5 * x * x)


def _norm_cdf(x: float) -> float:
    return float(ndtr(x))


def _validate_common_inputs(
    forward: float,
    strike: float,
    time_to_expiry: float,
    discount_factor: float,
    option_type: str,
) -> str:
    opt = option_type.lower()
    if opt not in ("call", "put"):
        raise InputBoundsError(f"Unsupported option_type '{option_type}'; expected 'call' or 'put'.")
    if not math.isfinite(forward) or forward <= 0.0:
        raise InputBoundsError(f"Forward must be finite and positive, got {forward}.")
    if not math.isfinite(strike) or strike <= 0.0:
        raise InputBoundsError(f"Strike must be finite and positive, got {strike}.")
    if not math.isfinite(time_to_expiry) or time_to_expiry <= 0.0:
        raise InputBoundsError(f"Time to expiry must be finite and positive, got {time_to_expiry}.")
    if not math.isfinite(discount_factor) or discount_factor <= 0.0:
        raise InputBoundsError(f"Discount factor must be finite and positive, got {discount_factor}.")
    return opt


def black76_price_bounds(
    forward: float,
    strike: float,
    discount_factor: float,
    option_type: str,
) -> Tuple[float, float]:
    """Return strict no-arbitrage (lower_bound, upper_bound) for a European Black-76 option."""
    opt = option_type.lower()
    if opt == "call":
        lower = discount_factor * max(forward - strike, 0.0)
        upper = discount_factor * forward
    elif opt == "put":
        lower = discount_factor * max(strike - forward, 0.0)
        upper = discount_factor * strike
    else:
        raise InputBoundsError(f"Unsupported option_type '{option_type}'.")
    return lower, upper


def black76_price(
    forward: float,
    strike: float,
    time_to_expiry: float,
    vol: float,
    discount_factor: float,
    option_type: str,
) -> float:
    """Price a European call or put under the Black-76 model."""
    opt = _validate_common_inputs(forward, strike, time_to_expiry, discount_factor, option_type)
    if not math.isfinite(vol) or vol < 0.0:
        raise InputBoundsError(f"Volatility must be finite and non-negative, got {vol}.")
    if vol == 0.0:
        return black76_price_bounds(forward, strike, discount_factor, opt)[0]

    std_dev = vol * math.sqrt(time_to_expiry)
    d1 = (math.log(forward / strike) + 0.5 * std_dev * std_dev) / std_dev
    d2 = d1 - std_dev
    if opt == "call":
        return discount_factor * (forward * _norm_cdf(d1) - strike * _norm_cdf(d2))
    return discount_factor * (strike * _norm_cdf(-d2) - forward * _norm_cdf(-d1))


def black76_vega(
    forward: float,
    strike: float,
    time_to_expiry: float,
    vol: float,
    discount_factor: float,
) -> float:
    """Analytic Black-76 vega (dPrice / dVol, identical for calls and puts)."""
    _validate_common_inputs(forward, strike, time_to_expiry, discount_factor, "call")
    if not math.isfinite(vol) or vol <= 0.0:
        return 0.0
    sqrt_t = math.sqrt(time_to_expiry)
    std_dev = vol * sqrt_t
    d1 = (math.log(forward / strike) + 0.5 * std_dev * std_dev) / std_dev
    return discount_factor * forward * sqrt_t * _norm_pdf(d1)


def black76_delta(
    forward: float,
    strike: float,
    time_to_expiry: float,
    vol: float,
    discount_factor: float,
    option_type: str,
) -> float:
    """Analytic Black-76 forward delta (dPrice / dForward)."""
    opt = _validate_common_inputs(forward, strike, time_to_expiry, discount_factor, option_type)
    if not math.isfinite(vol) or vol <= 0.0:
        raise InputBoundsError(f"Volatility must be finite and positive for delta, got {vol}.")
    std_dev = vol * math.sqrt(time_to_expiry)
    d1 = (math.log(forward / strike) + 0.5 * std_dev * std_dev) / std_dev
    if opt == "call":
        return discount_factor * _norm_cdf(d1)
    return -discount_factor * _norm_cdf(-d1)


def black76_gamma(
    forward: float,
    strike: float,
    time_to_expiry: float,
    vol: float,
    discount_factor: float,
) -> float:
    """Analytic Black-76 forward gamma (d^2 Price / dForward^2)."""
    _validate_common_inputs(forward, strike, time_to_expiry, discount_factor, "call")
    if not math.isfinite(vol) or vol <= 0.0:
        raise InputBoundsError(f"Volatility must be finite and positive for gamma, got {vol}.")
    std_dev = vol * math.sqrt(time_to_expiry)
    d1 = (math.log(forward / strike) + 0.5 * std_dev * std_dev) / std_dev
    return discount_factor * _norm_pdf(d1) / (forward * std_dev)


def implied_volatility_black76(
    price: float,
    forward: float,
    strike: float,
    time_to_expiry: float,
    discount_factor: float,
    option_type: str,
    vol_lower: float = 1e-5,
    vol_upper: float = 5.0,
    tol: float = 1e-10,
    max_iter: int = 100,
    min_vega: float = 0.0,
) -> float:
    """Invert Black-76 option price to annualized implied volatility.

    Enforces strict no-arbitrage price bounds and never estimates implied volatility
    when inputs are outside valid bounds. Uses safeguarded Newton-Raphson with a
    guaranteed Brent fallback on [vol_lower, vol_upper].
    """
    opt = _validate_common_inputs(forward, strike, time_to_expiry, discount_factor, option_type)
    if not math.isfinite(price):
        raise InputBoundsError(f"Option price must be finite, got {price}.")
    if not (0.0 < vol_lower < vol_upper):
        raise InputBoundsError(f"Solver bounds must satisfy 0 < vol_lower < vol_upper, got [{vol_lower}, {vol_upper}].")

    lower_bound, upper_bound = black76_price_bounds(forward, strike, discount_factor, opt)
    if price <= lower_bound or price >= upper_bound:
        raise InputBoundsError(
            f"Option price {price:.8g} lies outside strict no-arbitrage bounds "
            f"({lower_bound:.8g}, {upper_bound:.8g}) for {opt} K={strike}, F={forward}, T={time_to_expiry}.",
            details={
                "price": price,
                "lower_bound": lower_bound,
                "upper_bound": upper_bound,
                "option_type": opt,
                "strike": strike,
                "forward": forward,
                "time_to_expiry": time_to_expiry,
            },
        )

    # Convert to equivalent OTM option via put-call parity for maximum numerical accuracy
    # OTM price == time value = price - intrinsic_lower_bound
    otm_type = "put" if strike < forward else "call"
    otm_price = price - lower_bound

    p_low = black76_price(forward, strike, time_to_expiry, vol_lower, discount_factor, otm_type)
    p_high = black76_price(forward, strike, time_to_expiry, vol_upper, discount_factor, otm_type)

    if otm_price < p_low:
        raise InputBoundsError(
            f"Option price {price:.8g} is below minimum solver-bound price at vol={vol_lower}.",
            details={"price": price, "otm_price": otm_price, "p_low": p_low, "vol_lower": vol_lower},
        )
    if otm_price > p_high:
        raise InputBoundsError(
            f"Option price {price:.8g} exceeds maximum solver-bound price at vol={vol_upper}.",
            details={"price": price, "otm_price": otm_price, "p_high": p_high, "vol_upper": vol_upper},
        )

    # Safeguarded Newton-Raphson with strict volatility-space convergence and Brent fallback
    log_fk = abs(math.log(forward / strike))
    if log_fk > 1e-8:
        guess = math.sqrt(2.0 * log_fk / time_to_expiry)
    else:
        guess = math.sqrt(2.0 * math.pi / time_to_expiry) * (otm_price / (discount_factor * forward))
    vol = min(max(guess, vol_lower * 1.1), vol_upper * 0.9)

    lo = vol_lower
    hi = vol_upper
    converged_vol = None

    for _ in range(max_iter):
        p_val = black76_price(forward, strike, time_to_expiry, vol, discount_factor, otm_type)
        diff = p_val - otm_price
        if diff < 0.0:
            lo = vol
        else:
            hi = vol

        if (hi - lo) <= tol * max(1.0, vol):
            converged_vol = 0.5 * (lo + hi)
            break

        vega = black76_vega(forward, strike, time_to_expiry, vol, discount_factor)
        if vega > 1e-14:
            step = diff / vega
            if abs(step) <= tol * max(1.0, vol) and abs(diff) <= tol * max(1e-12, otm_price):
                converged_vol = vol - step
                break
            candidate = vol - step
            if lo < candidate < hi:
                vol = candidate
                continue

        vol = 0.5 * (lo + hi)

    if converged_vol is None:
        converged_vol = float(
            brentq(
                lambda v: black76_price(forward, strike, time_to_expiry, v, discount_factor, otm_type) - otm_price,
                lo,
                hi,
                xtol=tol,
                rtol=tol,
                maxiter=max_iter,
            )
        )

    if min_vega > 0.0:
        final_vega = black76_vega(forward, strike, time_to_expiry, converged_vol, discount_factor)
        if final_vega < min_vega:
            raise InputBoundsError(
                f"Solved implied vol {converged_vol:.6f} has near-zero vega {final_vega:.6e} < min_vega {min_vega:.6e}.",
                details={"implied_vol": converged_vol, "vega": final_vega, "min_vega": min_vega},
            )

    return float(converged_vol)
