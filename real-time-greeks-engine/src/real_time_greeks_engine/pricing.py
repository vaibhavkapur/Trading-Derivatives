"""Pure domain Black-Scholes-Merton analytic Greeks and bump-and-revalue fallback."""

from __future__ import annotations

import math
from typing import Callable

from real_time_greeks_engine.config import PricingConfig
from real_time_greeks_engine.errors import UnsupportedInstrumentError, ValidationError
from real_time_greeks_engine.models import (
    DayCountConvention,
    ExerciseStyle,
    OptionInstrument,
    OptionType,
    PositionGreeks,
    PricingMethod,
    ReliabilityFlag,
    UnitGreeks,
)

_INV_SQRT_2 = 1.0 / math.sqrt(2.0)
_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)


def norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function using math.erf."""
    return 0.5 * (1.0 + math.erf(x * _INV_SQRT_2))


def norm_pdf(x: float) -> float:
    """Standard normal probability density function."""
    return _INV_SQRT_2PI * math.exp(-0.5 * x * x)


def bsm_price(
    option_type: OptionType,
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    rate: float,
    dividend_yield: float,
    vol: float,
) -> float:
    """Exact Black-Scholes-Merton European option price per unit of underlying."""
    if spot <= 0.0 or not math.isfinite(spot):
        raise ValidationError(f"spot must be finite and > 0 (got {spot})")
    if strike <= 0.0 or not math.isfinite(strike):
        raise ValidationError(f"strike must be finite and > 0 (got {strike})")
    if vol <= 0.0 or not math.isfinite(vol):
        raise ValidationError(f"vol must be finite and > 0 (got {vol})")
    if not math.isfinite(time_to_expiry_years):
        raise ValidationError(f"time_to_expiry_years must be finite (got {time_to_expiry_years})")
    if not math.isfinite(rate) or not math.isfinite(dividend_yield):
        raise ValidationError("rate and dividend_yield must be finite")

    if time_to_expiry_years <= 0.0:
        if option_type is OptionType.CALL:
            return max(spot - strike, 0.0)
        return max(strike - spot, 0.0)

    sqrt_t = math.sqrt(time_to_expiry_years)
    vol_sqrt_t = vol * sqrt_t
    if vol_sqrt_t < 1e-12:
        forward = spot * math.exp((rate - dividend_yield) * time_to_expiry_years)
        disc = math.exp(-rate * time_to_expiry_years)
        intrinsic_fwd = (forward - strike) if option_type is OptionType.CALL else (strike - forward)
        return max(intrinsic_fwd, 0.0) * disc

    d1 = (math.log(spot / strike) + (rate - dividend_yield + 0.5 * vol * vol) * time_to_expiry_years) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    disc_q = math.exp(-dividend_yield * time_to_expiry_years)
    disc_r = math.exp(-rate * time_to_expiry_years)

    if option_type is OptionType.CALL:
        return spot * disc_q * norm_cdf(d1) - strike * disc_r * norm_cdf(d2)
    return strike * disc_r * norm_cdf(-d2) - spot * disc_q * norm_cdf(-d1)


def assess_reliability_flags(
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    vol: float,
    day_count: DayCountConvention,
    pricing_config: PricingConfig,
) -> list[ReliabilityFlag]:
    """Identify numerical boundaries where sensitivities may become discontinuous or unreliable."""
    flags: list[ReliabilityFlag] = []
    tte_days = time_to_expiry_years * day_count.days_per_year
    if time_to_expiry_years <= 0.0 or tte_days < pricing_config.min_reliable_tte_days:
        flags.append(ReliabilityFlag.NEAR_EXPIRY)

    if time_to_expiry_years > 0.0:
        vol_sqrt_t = vol * math.sqrt(time_to_expiry_years)
        if vol_sqrt_t < pricing_config.min_sigma_sqrt_t:
            flags.append(ReliabilityFlag.LOW_VOL)

    if spot > 0.0 and strike > 0.0:
        log_moneyness = abs(math.log(spot / strike))
        if log_moneyness > pricing_config.extreme_moneyness_log_threshold:
            flags.append(ReliabilityFlag.EXTREME_MONEYNESS)

    return flags


def analytic_bsm_greeks(
    option_type: OptionType,
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    rate: float,
    dividend_yield: float,
    vol: float,
    day_count: DayCountConvention = DayCountConvention.ACT_365F,
) -> UnitGreeks:
    """Compute exact closed-form Black-Scholes-Merton unit price and Greeks.

    Unit conventions match GreeksUnitSpec:
    - delta: dV/dS
    - gamma: d2V/dS2
    - vega:  0.01 * dV/dsigma (per +1% vol)
    - theta: (1 / day_count.days_per_year) * dV/dt (per +1 calendar day)
    - rho:   0.01 * dV/dr (per +1% continuous rate)
    - vanna: 0.01 * d2V/(dS dsigma) (per +1% vol)
    - charm: (1 / day_count.days_per_year) * dDelta/dt (per +1 calendar day)
    - volga: 0.0001 * d2V/dsigma2 (change in 1%-vega per +1% vol)
    """
    price = bsm_price(option_type, spot, strike, time_to_expiry_years, rate, dividend_yield, vol)
    if time_to_expiry_years <= 0.0:
        return UnitGreeks(
            price=price,
            delta=0.0,
            gamma=0.0,
            vega=0.0,
            theta=0.0,
            rho=0.0,
            vanna=0.0,
            charm=0.0,
            volga=0.0,
        )

    sqrt_t = math.sqrt(time_to_expiry_years)
    vol_sqrt_t = vol * sqrt_t
    if vol_sqrt_t < 1e-12:
        disc_q = math.exp(-dividend_yield * time_to_expiry_years)
        forward = spot * math.exp((rate - dividend_yield) * time_to_expiry_years)
        if option_type is OptionType.CALL:
            delta = disc_q if forward > strike else 0.0
        else:
            delta = -disc_q if forward < strike else 0.0
        return UnitGreeks(
            price=price,
            delta=delta,
            gamma=0.0,
            vega=0.0,
            theta=0.0,
            rho=0.0,
            vanna=0.0,
            charm=0.0,
            volga=0.0,
        )

    d1 = (math.log(spot / strike) + (rate - dividend_yield + 0.5 * vol * vol) * time_to_expiry_years) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    disc_q = math.exp(-dividend_yield * time_to_expiry_years)
    disc_r = math.exp(-rate * time_to_expiry_years)
    pdf_d1 = norm_pdf(d1)
    days_per_year = day_count.days_per_year

    gamma = (disc_q * pdf_d1) / (spot * vol_sqrt_t)
    vega_raw = spot * disc_q * pdf_d1 * sqrt_t
    vega = 0.01 * vega_raw
    vanna = 0.01 * (-disc_q * pdf_d1 * d2 / vol)
    volga = 0.0001 * (vega_raw * d1 * d2 / vol)

    common_theta_term = -(spot * disc_q * pdf_d1 * vol) / (2.0 * sqrt_t)
    common_charm_term = -disc_q * pdf_d1 * (
        2.0 * (rate - dividend_yield) * time_to_expiry_years - d2 * vol_sqrt_t
    ) / (2.0 * time_to_expiry_years * vol_sqrt_t)

    if option_type is OptionType.CALL:
        nd1 = norm_cdf(d1)
        nd2 = norm_cdf(d2)
        delta = disc_q * nd1
        theta_annual = (
            common_theta_term
            + dividend_yield * spot * disc_q * nd1
            - rate * strike * disc_r * nd2
        )
        rho = 0.01 * (strike * time_to_expiry_years * disc_r * nd2)
        charm_annual = dividend_yield * disc_q * nd1 + common_charm_term
    else:
        n_neg_d1 = norm_cdf(-d1)
        n_neg_d2 = norm_cdf(-d2)
        delta = -disc_q * n_neg_d1
        theta_annual = (
            common_theta_term
            - dividend_yield * spot * disc_q * n_neg_d1
            + rate * strike * disc_r * n_neg_d2
        )
        rho = 0.01 * (-strike * time_to_expiry_years * disc_r * n_neg_d2)
        charm_annual = -dividend_yield * disc_q * n_neg_d1 + common_charm_term

    return UnitGreeks(
        price=price,
        delta=delta,
        gamma=gamma,
        vega=vega,
        theta=theta_annual / days_per_year,
        rho=rho,
        vanna=vanna,
        charm=charm_annual / days_per_year,
        volga=volga,
    )


PriceCallable = Callable[[float, float, float, float], float]


def bump_and_revalue_greeks(
    price_fn: PriceCallable,
    spot: float,
    time_to_expiry_years: float,
    rate: float,
    vol: float,
    day_count: DayCountConvention = DayCountConvention.ACT_365F,
    pricing_config: PricingConfig | None = None,
) -> UnitGreeks:
    """Compute central finite-difference Greeks for any price_fn(spot, tte_years, rate, vol).

    Handles boundary clamping when time_to_expiry_years or vol is smaller than the bump step.
    """
    cfg = pricing_config or PricingConfig()
    base_price = price_fn(spot, time_to_expiry_years, rate, vol)
    if time_to_expiry_years <= 0.0:
        return UnitGreeks(
            price=base_price,
            delta=0.0,
            gamma=0.0,
            vega=0.0,
            theta=0.0,
            rho=0.0,
            vanna=0.0,
            charm=0.0,
            volga=0.0,
        )

    days_per_year = day_count.days_per_year

    # Spot central difference for delta and gamma
    h_s = max(spot * cfg.spot_rel_bump, 1e-8)
    v_sp = price_fn(spot + h_s, time_to_expiry_years, rate, vol)
    v_sm = price_fn(spot - h_s, time_to_expiry_years, rate, vol)
    delta = (v_sp - v_sm) / (2.0 * h_s)
    gamma = (v_sp - 2.0 * base_price + v_sm) / (h_s * h_s)

    # Volatility central difference for vega and volga
    h_v = min(cfg.vol_abs_bump, 0.25 * vol)
    v_vp = price_fn(spot, time_to_expiry_years, rate, vol + h_v)
    v_vm = price_fn(spot, time_to_expiry_years, rate, vol - h_v)
    vega = 0.01 * (v_vp - v_vm) / (2.0 * h_v)
    volga = 0.0001 * (v_vp - 2.0 * base_price + v_vm) / (h_v * h_v)

    # Rate central difference for rho
    h_r = cfg.rate_abs_bump
    v_rp = price_fn(spot, time_to_expiry_years, rate + h_r, vol)
    v_rm = price_fn(spot, time_to_expiry_years, rate - h_r, vol)
    rho = 0.01 * (v_rp - v_rm) / (2.0 * h_r)

    # Time central/one-sided difference for theta (per +1 calendar day)
    h_days = min(cfg.time_days_bump, 0.25 * time_to_expiry_years * days_per_year)
    h_t = h_days / days_per_year
    if h_t > 1e-12:
        v_tm = price_fn(spot, time_to_expiry_years - h_t, rate, vol)
        v_tp = price_fn(spot, time_to_expiry_years + h_t, rate, vol)
        # Advancing calendar time by +h_days reduces T by h_t
        theta = (v_tm - v_tp) / (2.0 * h_days)
    else:
        theta = 0.0

    # Mixed second derivatives: vanna and charm
    v_sp_vp = price_fn(spot + h_s, time_to_expiry_years, rate, vol + h_v)
    v_sp_vm = price_fn(spot + h_s, time_to_expiry_years, rate, vol - h_v)
    v_sm_vp = price_fn(spot - h_s, time_to_expiry_years, rate, vol + h_v)
    v_sm_vm = price_fn(spot - h_s, time_to_expiry_years, rate, vol - h_v)
    vanna = 0.01 * (v_sp_vp - v_sp_vm - v_sm_vp + v_sm_vm) / (4.0 * h_s * h_v)

    if h_t > 1e-12:
        delta_t_fwd = (
            price_fn(spot + h_s, time_to_expiry_years - h_t, rate, vol)
            - price_fn(spot - h_s, time_to_expiry_years - h_t, rate, vol)
        ) / (2.0 * h_s)
        delta_t_bwd = (
            price_fn(spot + h_s, time_to_expiry_years + h_t, rate, vol)
            - price_fn(spot - h_s, time_to_expiry_years + h_t, rate, vol)
        ) / (2.0 * h_s)
        charm = (delta_t_fwd - delta_t_bwd) / (2.0 * h_days)
    else:
        charm = 0.0

    return UnitGreeks(
        price=base_price,
        delta=delta,
        gamma=gamma,
        vega=vega,
        theta=theta,
        rho=rho,
        vanna=vanna,
        charm=charm,
        volga=volga,
    )


def scale_unit_greeks_to_position(
    unit: UnitGreeks,
    *,
    quantity: float,
    contract_multiplier: float,
    spot: float,
) -> PositionGreeks:
    """Scale unit Greeks to position-level risk metrics with explicit unit conversions."""
    scale = quantity * contract_multiplier
    delta_shares = unit.delta * scale
    gamma_shares = unit.gamma * scale
    return PositionGreeks(
        pv=unit.price * scale,
        delta_shares=delta_shares,
        dollar_delta=delta_shares * spot,
        gamma_shares=gamma_shares,
        dollar_gamma_1pct=gamma_shares * spot * spot * 0.01,
        vega=unit.vega * scale,
        theta=unit.theta * scale,
        rho=unit.rho * scale,
        vanna=unit.vanna * scale,
        charm=unit.charm * scale,
        volga=unit.volga * scale,
    )


def compute_instrument_greeks(
    instrument: OptionInstrument,
    *,
    spot: float,
    time_to_expiry_years: float,
    rate: float,
    dividend_yield: float,
    vol: float,
    pricing_config: PricingConfig,
    method: PricingMethod | None = None,
    custom_price_fn: PriceCallable | None = None,
) -> tuple[UnitGreeks, PricingMethod, list[ReliabilityFlag]]:
    """Compute unit Greeks with explicit unsupported-case guards and bump-and-revalue fallback."""
    if instrument.exercise_style is not ExerciseStyle.EUROPEAN and custom_price_fn is None:
        raise UnsupportedInstrumentError(
            f"Exercise style {instrument.exercise_style.value} is not supported by BSM pricer "
            f"without an explicit custom pricer adapter.",
            details={
                "instrument_id": instrument.instrument_id,
                "exercise_style": instrument.exercise_style.value,
            },
        )

    flags = assess_reliability_flags(
        spot=spot,
        strike=instrument.strike,
        time_to_expiry_years=time_to_expiry_years,
        vol=vol,
        day_count=instrument.day_count,
        pricing_config=pricing_config,
    )

    chosen_method = method or PricingMethod(pricing_config.default_method)

    # Custom pricer always falls back to bump-and-revalue
    if custom_price_fn is not None:
        if not pricing_config.fallback_to_bump_and_revalue:
            raise UnsupportedInstrumentError(
                "Custom price callable requires fallback_to_bump_and_revalue=True",
                details={"instrument_id": instrument.instrument_id},
            )
        unit = bump_and_revalue_greeks(
            custom_price_fn,
            spot=spot,
            time_to_expiry_years=time_to_expiry_years,
            rate=rate,
            vol=vol,
            day_count=instrument.day_count,
            pricing_config=pricing_config,
        )
        if ReliabilityFlag.FALLBACK_BUMP_AND_REVALUE not in flags:
            flags.append(ReliabilityFlag.FALLBACK_BUMP_AND_REVALUE)
        return unit, PricingMethod.BUMP_AND_REVALUE, flags or [ReliabilityFlag.OK]

    if chosen_method is PricingMethod.BUMP_AND_REVALUE:
        def _bsm_fn(s: float, t: float, r: float, v: float) -> float:
            return bsm_price(instrument.option_type, s, instrument.strike, t, r, dividend_yield, v)

        unit = bump_and_revalue_greeks(
            _bsm_fn,
            spot=spot,
            time_to_expiry_years=time_to_expiry_years,
            rate=rate,
            vol=vol,
            day_count=instrument.day_count,
            pricing_config=pricing_config,
        )
        if ReliabilityFlag.FALLBACK_BUMP_AND_REVALUE not in flags:
            flags.append(ReliabilityFlag.FALLBACK_BUMP_AND_REVALUE)
        return unit, PricingMethod.BUMP_AND_REVALUE, flags or [ReliabilityFlag.OK]

    unit = analytic_bsm_greeks(
        option_type=instrument.option_type,
        spot=spot,
        strike=instrument.strike,
        time_to_expiry_years=time_to_expiry_years,
        rate=rate,
        dividend_yield=dividend_yield,
        vol=vol,
        day_count=instrument.day_count,
    )
    return unit, PricingMethod.ANALYTIC_BSM, flags or [ReliabilityFlag.OK]
