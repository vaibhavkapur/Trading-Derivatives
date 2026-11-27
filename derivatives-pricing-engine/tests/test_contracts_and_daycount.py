"""Unit tests for data contracts, timestamps, day-count conventions, and discounting."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from derivatives_pricing_engine import (
    DayCountConvention,
    ExerciseStyle,
    MarketSnapshot,
    OptionContract,
    OptionType,
    PricingMethod,
    PricingRequest,
    UnsupportedFeatureError,
    ValidationError,
)
from derivatives_pricing_engine.analytics.day_count import discount_factor, year_fraction


def test_day_count_conventions_exact_one_year() -> None:
    t0 = datetime(2026, 1, 15, 16, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2027, 1, 15, 16, 0, 0, tzinfo=timezone.utc)  # 365 days

    assert year_fraction(t0, t1, DayCountConvention.ACT_365F) == pytest.approx(1.0, abs=1e-15)
    assert year_fraction(t0, t1, DayCountConvention.ACT_360) == pytest.approx(365.0 / 360.0, abs=1e-15)
    assert year_fraction(t0, t1, DayCountConvention.ACT_365_25) == pytest.approx(365.0 / 365.25, abs=1e-15)
    assert year_fraction(t0, t1, DayCountConvention.THIRTY_360) == pytest.approx(1.0, abs=1e-15)
    assert year_fraction(t0, t0, DayCountConvention.ACT_365F) == 0.0


def test_timestamp_normalization_across_timezones() -> None:
    contract = OptionContract(
        contract_id="TZ-TEST",
        underlying="SYNTH-EQ",
        currency="USD",
        option_type="call",
        exercise_style="european",
        strike=100.0,
        expiry_timestamp="2027-01-15T11:00:00-05:00",
    )
    market = MarketSnapshot(
        snapshot_id="SNAP-TZ",
        valuation_timestamp="2026-01-15T16:00:00Z",
        spot=100.0,
        risk_free_rate=0.05,
        dividend_yield=0.0,
        volatility=0.2,
    )
    assert contract.expiry_timestamp == datetime(2027, 1, 15, 16, 0, 0, tzinfo=timezone.utc)
    assert year_fraction(market.valuation_timestamp, contract.expiry_timestamp) == pytest.approx(1.0, abs=1e-15)


def test_discount_factor_and_negative_rate() -> None:
    assert discount_factor(0.05, 2.0) == pytest.approx(math.exp(-0.10), abs=1e-15)
    assert discount_factor(-0.01, 1.0) == pytest.approx(math.exp(0.01), abs=1e-15)
    assert discount_factor(0.05, 0.0) == 1.0


def test_contract_and_market_validation_errors() -> None:
    with pytest.raises(ValidationError):
        OptionContract(
            contract_id="BAD-CCY",
            underlying="SYNTH-EQ",
            currency="usd",
            option_type=OptionType.CALL,
            exercise_style=ExerciseStyle.EUROPEAN,
            strike=100.0,
            expiry_timestamp="2027-01-15T16:00:00Z",
        )

    with pytest.raises(ValidationError):
        OptionContract(
            contract_id="BAD-STRIKE",
            underlying="SYNTH-EQ",
            currency="USD",
            option_type=OptionType.CALL,
            exercise_style=ExerciseStyle.EUROPEAN,
            strike=0.0,
            expiry_timestamp="2027-01-15T16:00:00Z",
        )

    with pytest.raises(UnsupportedFeatureError):
        OptionContract(
            contract_id="BAD-STYLE",
            underlying="SYNTH-EQ",
            currency="USD",
            option_type=OptionType.CALL,
            exercise_style="asian",  # type: ignore[arg-type]
            strike=100.0,
            expiry_timestamp="2027-01-15T16:00:00Z",
        )

    with pytest.raises(ValidationError):
        MarketSnapshot(
            snapshot_id="BAD-NAN",
            valuation_timestamp="2026-01-15T16:00:00Z",
            spot=float("nan"),
            risk_free_rate=0.05,
            dividend_yield=0.0,
            volatility=0.2,
        )


def test_pricing_request_rejects_expired_contract() -> None:
    c = OptionContract(
        contract_id="EXP-1",
        underlying="SYNTH-EQ",
        currency="USD",
        option_type=OptionType.CALL,
        exercise_style=ExerciseStyle.EUROPEAN,
        strike=100.0,
        expiry_timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    m = MarketSnapshot(
        snapshot_id="SNAP-1",
        valuation_timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=1),
        spot=100.0,
        risk_free_rate=0.05,
        dividend_yield=0.0,
        volatility=0.2,
    )
    with pytest.raises(ValidationError):
        PricingRequest(contract=c, market=m, method=PricingMethod.BLACK_SCHOLES)
