"""Black-Scholes-Merton pricing API and finite-difference reference oracle.

Pricing is intentionally decoupled from hedging simulation and portfolio analytics
so alternative pricers (e.g., an adapter to derivatives-pricing-engine) can implement
the PricingModel protocol without changing hedging logic.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Protocol, runtime_checkable

from .contracts import OptionContract
from .errors import UnsupportedContractError, ValidationError

_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)
_SQRT_2 = math.sqrt(2.0)
_EXPIRY_EPS = 1e-12


def norm_pdf(x: float) -> float:
    """Standard normal probability density function."""
    return _INV_SQRT_2PI * math.exp(-0.5 * x * x)


def norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function via math.erf."""
    return 0.5 * (1.0 + math.erf(x / _SQRT_2))


@dataclass(frozen=True)
class OptionQuote:
    """Per-unit European option valuation and sensitivities."""

    price: float
    delta: float
    gamma: float
    vega: float
    theta: float
    intrinsic_value: float
    spot: float
    strike: float
    time_to_expiry: float
    volatility: float


@runtime_checkable
class PricingModel(Protocol):
    """Protocol for option pricing backends consumed by the hedging simulator."""

    def price_and_greeks(
        self,
        contract: OptionContract,
        *,
        spot: float,
        time_to_expiry: float,
        rate: float,
        dividend_yield: float,
        volatility: float,
    ) -> OptionQuote:
        ...


class BlackScholesPricer:
    """Closed-form Black-Scholes-Merton pricer for European calls and puts.

    Conventions:
    - Continuous compounding for risk_free_rate (r) and dividend_yield (q).
    - time_to_expiry (tau) expressed in year fractions.
    - vega is per 1.00 (100 vol points) change in sigma.
    - theta is annualized dV/dt (= -dV/dtau).
    """

    def price_and_greeks(
        self,
        contract: OptionContract,
        *,
        spot: float,
        time_to_expiry: float,
        rate: float,
        dividend_yield: float,
        volatility: float,
    ) -> OptionQuote:
        if contract.exercise_style != "european":
            raise UnsupportedContractError(
                f"BlackScholesPricer only supports european options, got {contract.exercise_style!r}",
                field="contract.exercise_style",
            )
        if not math.isfinite(spot) or spot <= 0.0:
            raise ValidationError(f"spot must be finite and > 0, got {spot!r}", field="spot")
        if not math.isfinite(time_to_expiry) or time_to_expiry < -_EXPIRY_EPS:
            raise ValidationError(
                f"time_to_expiry must be finite and >= 0, got {time_to_expiry!r}",
                field="time_to_expiry",
            )
        if not math.isfinite(volatility) or volatility <= 0.0:
            raise ValidationError(f"volatility must be finite and > 0, got {volatility!r}", field="volatility")
        if not math.isfinite(rate) or not math.isfinite(dividend_yield):
            raise ValidationError("rate and dividend_yield must be finite", field="rate")

        strike = float(contract.strike)
        is_call = contract.option_type == "call"
        intrinsic = max(spot - strike, 0.0) if is_call else max(strike - spot, 0.0)

        tau = max(0.0, float(time_to_expiry))
        if tau <= _EXPIRY_EPS:
            if is_call:
                if spot > strike:
                    delta = 1.0
                elif spot < strike:
                    delta = 0.0
                else:
                    delta = 0.5
            else:
                if spot < strike:
                    delta = -1.0
                elif spot > strike:
                    delta = 0.0
                else:
                    delta = -0.5
            return OptionQuote(
                price=intrinsic,
                delta=delta,
                gamma=0.0,
                vega=0.0,
                theta=0.0,
                intrinsic_value=intrinsic,
                spot=spot,
                strike=strike,
                time_to_expiry=0.0,
                volatility=volatility,
            )

        sqrt_tau = math.sqrt(tau)
        vol_sqrt_tau = volatility * sqrt_tau
        d1 = (math.log(spot / strike) + (rate - dividend_yield + 0.5 * volatility * volatility) * tau) / vol_sqrt_tau
        d2 = d1 - vol_sqrt_tau

        disc_r = math.exp(-rate * tau)
        disc_q = math.exp(-dividend_yield * tau)
        pdf_d1 = norm_pdf(d1)

        if is_call:
            nd1 = norm_cdf(d1)
            nd2 = norm_cdf(d2)
            price = spot * disc_q * nd1 - strike * disc_r * nd2
            delta = disc_q * nd1
            theta = (
                -(spot * disc_q * pdf_d1 * volatility) / (2.0 * sqrt_tau)
                - rate * strike * disc_r * nd2
                + dividend_yield * spot * disc_q * nd1
            )
        else:
            n_neg_d1 = norm_cdf(-d1)
            n_neg_d2 = norm_cdf(-d2)
            price = strike * disc_r * n_neg_d2 - spot * disc_q * n_neg_d1
            delta = -disc_q * n_neg_d1
            theta = (
                -(spot * disc_q * pdf_d1 * volatility) / (2.0 * sqrt_tau)
                + rate * strike * disc_r * n_neg_d2
                - dividend_yield * spot * disc_q * n_neg_d1
            )

        gamma = (disc_q * pdf_d1) / (spot * vol_sqrt_tau)
        vega = spot * disc_q * pdf_d1 * sqrt_tau

        return OptionQuote(
            price=max(0.0, price),
            delta=delta,
            gamma=gamma,
            vega=vega,
            theta=theta,
            intrinsic_value=intrinsic,
            spot=spot,
            strike=strike,
            time_to_expiry=tau,
            volatility=volatility,
        )


def finite_difference_greeks(
    pricer: PricingModel,
    contract: OptionContract,
    *,
    spot: float,
    time_to_expiry: float,
    rate: float,
    dividend_yield: float,
    volatility: float,
    spot_rel_bump: float = 1e-4,
    vol_bump: float = 1e-4,
    time_bump: float = 1e-5,
) -> dict[str, float]:
    """Independent numerical bump-and-revalue oracle for verifying analytic Greeks."""
    ds = spot * spot_rel_bump
    base = pricer.price_and_greeks(
        contract,
        spot=spot,
        time_to_expiry=time_to_expiry,
        rate=rate,
        dividend_yield=dividend_yield,
        volatility=volatility,
    )
    up = pricer.price_and_greeks(
        contract,
        spot=spot + ds,
        time_to_expiry=time_to_expiry,
        rate=rate,
        dividend_yield=dividend_yield,
        volatility=volatility,
    )
    dn = pricer.price_and_greeks(
        contract,
        spot=spot - ds,
        time_to_expiry=time_to_expiry,
        rate=rate,
        dividend_yield=dividend_yield,
        volatility=volatility,
    )
    delta_fd = (up.price - dn.price) / (2.0 * ds)
    gamma_fd = (up.price - 2.0 * base.price + dn.price) / (ds * ds)

    v_up = pricer.price_and_greeks(
        contract,
        spot=spot,
        time_to_expiry=time_to_expiry,
        rate=rate,
        dividend_yield=dividend_yield,
        volatility=volatility + vol_bump,
    )
    v_dn = pricer.price_and_greeks(
        contract,
        spot=spot,
        time_to_expiry=time_to_expiry,
        rate=rate,
        dividend_yield=dividend_yield,
        volatility=max(1e-8, volatility - vol_bump),
    )
    vega_fd = (v_up.price - v_dn.price) / (2.0 * vol_bump)

    if time_to_expiry > 2.0 * time_bump:
        t_shorter = pricer.price_and_greeks(
            contract,
            spot=spot,
            time_to_expiry=time_to_expiry - time_bump,
            rate=rate,
            dividend_yield=dividend_yield,
            volatility=volatility,
        )
        t_longer = pricer.price_and_greeks(
            contract,
            spot=spot,
            time_to_expiry=time_to_expiry + time_bump,
            rate=rate,
            dividend_yield=dividend_yield,
            volatility=volatility,
        )
        # theta = dV/dt = -dV/dtau
        theta_fd = (t_shorter.price - t_longer.price) / (2.0 * time_bump)
    else:
        theta_fd = 0.0

    return {
        "price": base.price,
        "delta": delta_fd,
        "gamma": gamma_fd,
        "vega": vega_fd,
        "theta": theta_fd,
    }
