"""Tests for Black-Scholes-Merton pricing and finite-difference Greek oracle."""

from __future__ import annotations

import math

import pytest

from options_hedging_simulator.contracts import OptionContract
from options_hedging_simulator.errors import ValidationError
from options_hedging_simulator.pricing import BlackScholesPricer, finite_difference_greeks


def test_black_scholes_textbook_benchmark() -> None:
    pricer = BlackScholesPricer()
    call = OptionContract(
        contract_id="BENCH-CALL",
        underlying="SYN",
        option_type="call",
        strike=100.0,
        maturity_years=1.0,
    )
    quote = pricer.price_and_greeks(
        call,
        spot=100.0,
        time_to_expiry=1.0,
        rate=0.05,
        dividend_yield=0.0,
        volatility=0.20,
    )
    assert quote.price == pytest.approx(10.450583572185565, abs=1e-10)
    assert quote.delta == pytest.approx(0.6368306511756191, abs=1e-10)
    assert quote.gamma == pytest.approx(0.018762017345846895, abs=1e-10)
    assert quote.vega == pytest.approx(37.52403469169379, abs=1e-8)


@pytest.mark.parametrize("spot", [85.0, 100.0, 120.0])
@pytest.mark.parametrize("div_yield", [0.0, 0.025])
def test_put_call_parity(spot: float, div_yield: float) -> None:
    pricer = BlackScholesPricer()
    r = 0.045
    tau = 0.5
    vol = 0.25
    strike = 100.0
    call = OptionContract(contract_id="C", underlying="SYN", option_type="call", strike=strike, maturity_years=tau)
    put = OptionContract(contract_id="P", underlying="SYN", option_type="put", strike=strike, maturity_years=tau)

    c = pricer.price_and_greeks(call, spot=spot, time_to_expiry=tau, rate=r, dividend_yield=div_yield, volatility=vol)
    p = pricer.price_and_greeks(put, spot=spot, time_to_expiry=tau, rate=r, dividend_yield=div_yield, volatility=vol)

    lhs = c.price - p.price
    rhs = spot * math.exp(-div_yield * tau) - strike * math.exp(-r * tau)
    assert lhs == pytest.approx(rhs, abs=1e-10)
    assert c.delta - p.delta == pytest.approx(math.exp(-div_yield * tau), abs=1e-10)
    assert c.gamma == pytest.approx(p.gamma, abs=1e-10)
    assert c.vega == pytest.approx(p.vega, abs=1e-10)


@pytest.mark.parametrize("option_type", ["call", "put"])
@pytest.mark.parametrize("spot", [92.0, 100.0, 112.0])
def test_analytic_greeks_match_finite_difference_oracle(option_type: str, spot: float) -> None:
    pricer = BlackScholesPricer()
    contract = OptionContract(
        contract_id=f"FD-{option_type}",
        underlying="SYN",
        option_type=option_type,
        strike=100.0,
        maturity_years=0.35,
    )
    analytic = pricer.price_and_greeks(
        contract,
        spot=spot,
        time_to_expiry=0.35,
        rate=0.05,
        dividend_yield=0.015,
        volatility=0.24,
    )
    fd = finite_difference_greeks(
        pricer,
        contract,
        spot=spot,
        time_to_expiry=0.35,
        rate=0.05,
        dividend_yield=0.015,
        volatility=0.24,
    )
    assert analytic.delta == pytest.approx(fd["delta"], abs=1e-5)
    assert analytic.gamma == pytest.approx(fd["gamma"], abs=1e-5)
    assert analytic.vega == pytest.approx(fd["vega"], abs=1e-3)
    assert analytic.theta == pytest.approx(fd["theta"], abs=1e-3)


def test_expiry_boundary_and_invalid_inputs() -> None:
    pricer = BlackScholesPricer()
    call = OptionContract(contract_id="EXP-C", underlying="SYN", option_type="call", strike=100.0, maturity_years=0.25)
    put = OptionContract(contract_id="EXP-P", underlying="SYN", option_type="put", strike=100.0, maturity_years=0.25)

    q_itm = pricer.price_and_greeks(call, spot=110.0, time_to_expiry=0.0, rate=0.05, dividend_yield=0.0, volatility=0.2)
    assert q_itm.price == 10.0 and q_itm.delta == 1.0 and q_itm.gamma == 0.0

    q_atm = pricer.price_and_greeks(call, spot=100.0, time_to_expiry=0.0, rate=0.05, dividend_yield=0.0, volatility=0.2)
    assert q_atm.price == 0.0 and q_atm.delta == 0.5

    q_put_itm = pricer.price_and_greeks(put, spot=90.0, time_to_expiry=0.0, rate=0.05, dividend_yield=0.0, volatility=0.2)
    assert q_put_itm.price == 10.0 and q_put_itm.delta == -1.0

    with pytest.raises(ValidationError):
        pricer.price_and_greeks(call, spot=-5.0, time_to_expiry=0.1, rate=0.05, dividend_yield=0.0, volatility=0.2)
    with pytest.raises(ValidationError):
        pricer.price_and_greeks(call, spot=100.0, time_to_expiry=-0.1, rate=0.05, dividend_yield=0.0, volatility=0.2)
