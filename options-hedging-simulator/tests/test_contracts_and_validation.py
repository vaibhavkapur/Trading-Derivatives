"""Tests for canonical input contracts, validation, and negative error fixtures."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from options_hedging_simulator.contracts import (
    ComparisonConfig,
    DeterministicShock,
    JumpParams,
    MarketParams,
    OptionContract,
    PathConfig,
    PolicyConfig,
    PricingConventions,
    SimulationConfig,
    TransactionCostConfig,
)
from options_hedging_simulator.errors import (
    ConfigurationError,
    UnsupportedContractError,
    ValidationError,
)
from options_hedging_simulator.io import load_simulation_config

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "data" / "fixtures"


def test_invalid_contract_fixtures_raise_expected_errors() -> None:
    payload = json.loads((FIXTURES_DIR / "invalid_contract_fixture.json").read_text(encoding="utf-8"))
    error_map = {
        "UnsupportedContractError": UnsupportedContractError,
        "ValidationError": ValidationError,
    }
    for case in payload["cases"]:
        exc_type = error_map[case["expected_error"]]
        with pytest.raises(exc_type):
            OptionContract(**case["contract"])


def test_currency_quantum_and_mismatch_validation() -> None:
    jpy = PricingConventions(currency="JPY")
    assert str(jpy.currency_quantum) == "1"

    usd = PricingConventions(currency="USD")
    assert str(usd.currency_quantum) == "0.01"

    with pytest.raises(ValidationError):
        PricingConventions(currency="USD", currency_decimals=4)

    with pytest.raises(ValidationError):
        SimulationConfig(
            scenario_name="mismatch",
            contract=OptionContract(
                contract_id="C1", underlying="SYN", option_type="call", strike=100.0, maturity_years=0.25, currency="EUR"
            ),
            market=MarketParams(spot0=100.0, risk_free_rate=0.03, hedge_volatility=0.20),
            costs=TransactionCostConfig(),
            path=PathConfig(),
            policy=PolicyConfig(name="p1", policy_type="static"),
            conventions=PricingConventions(currency="USD"),
        )


def test_market_and_cost_boundary_validation() -> None:
    with pytest.raises(ValidationError):
        MarketParams(spot0=0.0, risk_free_rate=0.05, hedge_volatility=0.2)
    with pytest.raises(ValidationError):
        MarketParams(spot0=100.0, risk_free_rate=math.nan, hedge_volatility=0.2)
    with pytest.raises(ValidationError):
        MarketParams(spot0=100.0, risk_free_rate=0.05, hedge_volatility=-0.1)
    with pytest.raises(ValidationError):
        TransactionCostConfig(half_spread_bps=-1.0)
    with pytest.raises(ValidationError):
        TransactionCostConfig(half_spread_bps=10000.0)
    with pytest.raises(ValidationError):
        PathConfig(
            model="merton_jump",
            num_steps=10,
            jump_params=JumpParams(deterministic_shocks=(DeterministicShock(step=11, pct_return=-0.1),)),
        )


def test_config_loading_missing_and_malformed(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_simulation_config(tmp_path / "does_not_exist.json")

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_simulation_config(bad_json)

    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text(json.dumps({"scenario_name": "only_name"}), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_simulation_config(incomplete)


def test_comparison_config_rejects_duplicate_policy_names() -> None:
    with pytest.raises(ValidationError):
        ComparisonConfig(
            scenario_name="dup",
            contract=OptionContract(contract_id="C1", underlying="SYN", option_type="call", strike=100.0, maturity_years=0.25),
            market=MarketParams(spot0=100.0, risk_free_rate=0.05, hedge_volatility=0.20),
            costs=TransactionCostConfig(),
            path=PathConfig(),
            policies=(
                PolicyConfig(name="same", policy_type="static"),
                PolicyConfig(name="same", policy_type="periodic"),
            ),
        )
