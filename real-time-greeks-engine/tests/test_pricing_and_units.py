"""Unit tests for analytic BSM Greeks, bump-and-revalue agreement, units, and boundary flags."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from real_time_greeks_engine.config import PricingConfig
from real_time_greeks_engine.errors import UnsupportedInstrumentError, ValidationError
from real_time_greeks_engine.models import (
    DayCountConvention,
    ExerciseStyle,
    OptionInstrument,
    OptionPosition,
    OptionType,
    PricingMethod,
    ReliabilityFlag,
    SpotTick,
)
from real_time_greeks_engine.pricing import (
    analytic_bsm_greeks,
    bsm_price,
    bump_and_revalue_greeks,
    compute_instrument_greeks,
    scale_unit_greeks_to_position,
)

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures"


def test_hull_textbook_reference_atm_call_and_put_call_parity() -> None:
    """Verify exact BSM price/delta against hand-calculated reference and put-call parity."""
    s, k, t, r, q, v = 100.0, 100.0, 1.0, 0.05, 0.0, 0.20
    call = analytic_bsm_greeks(OptionType.CALL, s, k, t, r, q, v)
    put = analytic_bsm_greeks(OptionType.PUT, s, k, t, r, q, v)

    # Hand-calculated / textbook reference: d1 = 0.35, d2 = 0.15
    assert call.price == pytest.approx(10.450583572185565, abs=1e-10)
    assert call.delta == pytest.approx(0.6368306511756191, abs=1e-10)
    assert call.gamma == pytest.approx(0.018762017345846895, abs=1e-10)
    assert call.vega == pytest.approx(0.3752403469169379, abs=1e-10)

    # Put-call parity
    parity_rhs = s * math.exp(-q * t) - k * math.exp(-r * t)
    assert (call.price - put.price) == pytest.approx(parity_rhs, abs=1e-12)
    assert (call.delta - put.delta) == pytest.approx(math.exp(-q * t), abs=1e-12)
    assert call.gamma == pytest.approx(put.gamma, abs=1e-12)
    assert call.vega == pytest.approx(put.vega, abs=1e-12)
    assert call.vanna == pytest.approx(put.vanna, abs=1e-12)
    assert call.volga == pytest.approx(put.volga, abs=1e-12)


@pytest.mark.parametrize(
    ("opt_type", "s", "k", "t", "r", "q", "v", "dc"),
    [
        (OptionType.CALL, 100.0, 100.0, 1.0, 0.05, 0.0, 0.20, DayCountConvention.ACT_365F),
        (OptionType.PUT, 100.0, 95.0, 0.5, 0.04, 0.02, 0.25, DayCountConvention.ACT_365F),
        (OptionType.CALL, 450.0, 420.0, 0.25, 0.045, 0.015, 0.30, DayCountConvention.ACT_360),
        (OptionType.PUT, 5000.0, 5200.0, 1.5, 0.048, 0.014, 0.18, DayCountConvention.ACT_365F),
        (OptionType.CALL, 200.0, 230.0, 0.75, 0.052, 0.005, 0.32, DayCountConvention.ACT_360),
    ],
)
def test_analytic_vs_finite_difference_agreement_within_1e4(
    opt_type: OptionType,
    s: float,
    k: float,
    t: float,
    r: float,
    q: float,
    v: float,
    dc: DayCountConvention,
) -> None:
    """Acceptance check: analytic vs bump-and-revalue relative error <= 1e-4 away from singularities."""
    cfg = PricingConfig()
    an = analytic_bsm_greeks(opt_type, s, k, t, r, q, v, dc)

    def _price(sp: float, tte: float, rt: float, vol: float) -> float:
        return bsm_price(opt_type, sp, k, tte, rt, q, vol)

    fd = bump_and_revalue_greeks(_price, s, t, r, v, dc, cfg)

    for field in ("price", "delta", "gamma", "vega", "theta", "rho", "vanna", "charm", "volga"):
        av = getattr(an, field)
        fv = getattr(fd, field)
        denom = max(abs(av), 1e-8)
        rel_err = abs(av - fv) / denom
        assert rel_err <= 1e-4, f"{field} rel_err={rel_err:.3e} (analytic={av}, fd={fv})"


def test_bump_and_revalue_fallback_custom_pricer() -> None:
    """Custom non-BSM payoff falls back cleanly to bump-and-revalue with explicit flag."""
    cfg = PricingConfig()
    inst = OptionInstrument(
        instrument_id="ACME-CUSTOM-AMER",
        underlying="ACME",
        option_type=OptionType.CALL,
        strike=100.0,
        expiry_utc=datetime(2027, 6, 1, 14, 30, tzinfo=timezone.utc),
        exercise_style=ExerciseStyle.AMERICAN,
    )

    def _smooth_custom_pricer(spot: float, tte: float, rate: float, vol: float) -> float:
        # Synthetic smooth surrogate price for testing custom fallback adapter
        return bsm_price(OptionType.CALL, spot, 100.0, tte, rate, 0.01, vol) * 1.02

    unit, method, flags = compute_instrument_greeks(
        inst,
        spot=100.0,
        time_to_expiry_years=0.5,
        rate=0.05,
        dividend_yield=0.01,
        vol=0.25,
        pricing_config=cfg,
        custom_price_fn=_smooth_custom_pricer,
    )
    assert method is PricingMethod.BUMP_AND_REVALUE
    assert ReliabilityFlag.FALLBACK_BUMP_AND_REVALUE in flags
    assert unit.price > 0.0
    assert 0.4 < unit.delta < 0.7


def test_boundary_flags_near_expiry_expired_low_vol_and_moneyness() -> None:
    cfg = PricingConfig()
    base_inst = OptionInstrument(
        instrument_id="ACME-C100",
        underlying="ACME",
        option_type=OptionType.CALL,
        strike=100.0,
        expiry_utc=datetime(2026, 6, 1, 20, 30, tzinfo=timezone.utc),
    )

    # Near expiry (6 hours = 0.25 days < 1.0 day)
    _, _, flags_near = compute_instrument_greeks(
        base_inst,
        spot=100.0,
        time_to_expiry_years=0.25 / 365.0,
        rate=0.05,
        dividend_yield=0.0,
        vol=0.20,
        pricing_config=cfg,
    )
    assert ReliabilityFlag.NEAR_EXPIRY in flags_near

    # Expired ITM call -> intrinsic only, Greeks zeroed
    unit_exp, _, flags_exp = compute_instrument_greeks(
        base_inst,
        spot=112.0,
        time_to_expiry_years=-0.01,
        rate=0.05,
        dividend_yield=0.0,
        vol=0.20,
        pricing_config=cfg,
    )
    assert unit_exp.price == pytest.approx(12.0)
    assert unit_exp.delta == 0.0
    assert unit_exp.gamma == 0.0
    assert ReliabilityFlag.NEAR_EXPIRY in flags_exp

    # Low vol
    _, _, flags_low_vol = compute_instrument_greeks(
        base_inst,
        spot=100.0,
        time_to_expiry_years=0.25,
        rate=0.05,
        dividend_yield=0.0,
        vol=0.0005,
        pricing_config=cfg,
    )
    assert ReliabilityFlag.LOW_VOL in flags_low_vol

    # Extreme moneyness
    _, _, flags_money = compute_instrument_greeks(
        base_inst,
        spot=300.0,
        time_to_expiry_years=0.5,
        rate=0.05,
        dividend_yield=0.0,
        vol=0.20,
        pricing_config=cfg,
    )
    assert ReliabilityFlag.EXTREME_MONEYNESS in flags_money


def test_position_scaling_units_and_sign() -> None:
    unit = analytic_bsm_greeks(OptionType.CALL, 100.0, 100.0, 1.0, 0.05, 0.0, 0.20)
    long_pg = scale_unit_greeks_to_position(unit, quantity=10.0, contract_multiplier=100.0, spot=100.0)
    short_pg = scale_unit_greeks_to_position(unit, quantity=-10.0, contract_multiplier=100.0, spot=100.0)

    assert long_pg.pv == pytest.approx(unit.price * 1000.0)
    assert long_pg.delta_shares == pytest.approx(unit.delta * 1000.0)
    assert long_pg.dollar_delta == pytest.approx(unit.delta * 1000.0 * 100.0)
    assert long_pg.dollar_gamma_1pct == pytest.approx(unit.gamma * 1000.0 * 100.0 * 100.0 * 0.01)
    assert short_pg.pv == pytest.approx(-long_pg.pv)
    assert short_pg.dollar_delta == pytest.approx(-long_pg.dollar_delta)
    assert short_pg.vega == pytest.approx(-long_pg.vega)


def test_invalid_fixtures_and_unsupported_american_raise_explicitly() -> None:
    raw = json.loads((FIXTURES / "invalid_and_boundary.json").read_text(encoding="utf-8"))
    cfg = PricingConfig()
    for case in raw["invalid_inputs"]:
        if case["expected_error"] == "ValidationError":
            with pytest.raises(PydanticValidationError):
                if case["kind"] == "spot_tick":
                    SpotTick.model_validate(case["payload"])
                else:
                    OptionPosition.model_validate(case["payload"])
        elif case["expected_error"] == "UnsupportedInstrumentError":
            pos = OptionPosition.model_validate(case["payload"])
            with pytest.raises(UnsupportedInstrumentError):
                compute_instrument_greeks(
                    pos.instrument,
                    spot=100.0,
                    time_to_expiry_years=0.5,
                    rate=0.05,
                    dividend_yield=0.0,
                    vol=0.20,
                    pricing_config=cfg,
                )

    with pytest.raises(ValidationError):
        bsm_price(OptionType.CALL, -1.0, 100.0, 1.0, 0.05, 0.0, 0.2)
