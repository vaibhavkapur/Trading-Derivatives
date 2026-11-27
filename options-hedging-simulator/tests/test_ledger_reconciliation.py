"""Domain correctness tests: self-financing cash and inventory reconciliation."""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path

import pytest

from options_hedging_simulator.contracts import (
    MarketParams,
    OptionContract,
    PathConfig,
    PolicyConfig,
    PricingConventions,
    SimulationConfig,
    TransactionCostConfig,
)
from options_hedging_simulator.errors import LedgerImbalanceError
from options_hedging_simulator.ledger import SelfFinancingLedger
from options_hedging_simulator.paths import generate_gbm_paths, load_historical_bars
from options_hedging_simulator.simulator import simulate_path

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "data" / "fixtures"


def test_deterministic_3step_matches_independent_oracle_exactly() -> None:
    expected = json.loads((FIXTURES_DIR / "deterministic_3step_expected.json").read_text(encoding="utf-8"))
    spot_path = load_historical_bars(FIXTURES_DIR / "deterministic_3step_bars.csv")

    config = SimulationConfig(
        scenario_name="deterministic_3step_oracle",
        contract=OptionContract(
            contract_id="DET-CALL-100",
            underlying="SYN_EQ",
            option_type="call",
            strike=100.0,
            maturity_years=0.25,
            contracts=1,
            multiplier=100,
            position_side="short",
            settlement_type="cash",
        ),
        market=MarketParams(spot0=100.0, risk_free_rate=0.05, dividend_yield=0.0, hedge_volatility=0.20),
        costs=TransactionCostConfig(),
        path=PathConfig(model="historical_bars", bars_path=str(FIXTURES_DIR / "deterministic_3step_bars.csv")),
        policy=PolicyConfig(name="every_step", policy_type="periodic", rebalance_every_n_steps=1),
    )

    result = simulate_path(spot_path, config, store_entries=True)
    assert str(result.terminal_hedge_error) == expected["terminal_hedge_error"]
    assert result.diagnostics.ledger_residual == Decimal("0.00")
    assert len(result.ledger_entries) == len(expected["steps"])

    for entry, exp_step in zip(result.ledger_entries, expected["steps"]):
        assert entry.step == exp_step["step"]
        assert entry.option_price == pytest.approx(exp_step["bs_price"], abs=1e-7)
        assert entry.option_delta == pytest.approx(exp_step["bs_delta"], abs=1e-7)
        assert str(entry.financing_interest) == exp_step["financing"]
        assert entry.trade_shares == pytest.approx(exp_step["trade_shares"], abs=1e-7)
        assert str(entry.trade_mid_cash) == exp_step["trade_mid_cash"]
        assert str(entry.option_cash_flow) == exp_step["option_cash_flow"]
        assert str(entry.cash_after) == exp_step["cash_after"]
        assert entry.shares_after == pytest.approx(exp_step["shares_after"], abs=1e-7)


@pytest.mark.parametrize("option_type", ["call", "put"])
@pytest.mark.parametrize("position_side", ["short", "long"])
@pytest.mark.parametrize("currency", ["USD", "JPY"])
def test_exact_reconciliation_with_costs_dividends_and_borrow_spread(
    option_type: str,
    position_side: str,
    currency: str,
) -> None:
    conventions = PricingConventions(currency=currency)
    contract = OptionContract(
        contract_id=f"REC-{option_type}-{position_side}-{currency}",
        underlying="SYN_EQ",
        option_type=option_type,
        strike=100.0,
        maturity_years=0.25,
        contracts=5,
        multiplier=100,
        position_side=position_side,
        settlement_type="cash",
        currency=currency,
    )
    market = MarketParams(spot0=100.0, risk_free_rate=0.04, dividend_yield=0.02, hedge_volatility=0.25)
    costs = TransactionCostConfig(
        half_spread_bps=4.0,
        fee_per_share=0.01,
        fee_per_trade=1.0,
        min_fee_per_trade=2.0,
        cash_borrow_spread_bps=75.0,
        share_lot_size=1.0,
    )
    path_cfg = PathConfig(model="gbm", num_paths=5, num_steps=30, seed=101)
    config = SimulationConfig(
        scenario_name="full_friction_recon",
        contract=contract,
        market=market,
        costs=costs,
        path=path_cfg,
        policy=PolicyConfig(name="daily", policy_type="periodic", rebalance_every_n_steps=1),
        conventions=conventions,
    )

    paths = generate_gbm_paths(market, contract, path_cfg)
    for sp in paths:
        res = simulate_path(sp, config, store_entries=True)
        assert res.diagnostics.ledger_residual == Decimal("0").quantize(conventions.currency_quantum)
        assert res.ledger_entries[-1].shares_after == 0.0
        assert res.total_transaction_cost == res.total_spread_cost + res.total_fee_cost
        assert res.total_transaction_cost > Decimal("0")


@pytest.mark.parametrize("option_type", ["call", "put"])
@pytest.mark.parametrize("position_side", ["short", "long"])
def test_cash_vs_physical_settlement_equivalence_at_zero_cost_and_spread_mechanics(
    option_type: str,
    position_side: str,
) -> None:
    import numpy as np
    from options_hedging_simulator.paths import SpotPath

    # Path that enters expiry already deep ITM (|delta_{T-1}| > 0.5) so physical delivery
    # requires unwinding only a tiny residual compared to liquidating the full hedge block.
    if option_type == "call":
        itm_spots = np.array([100.0, 108.0, 114.0, 116.0], dtype=np.float64)
    else:
        itm_spots = np.array([100.0, 92.0, 86.0, 84.0], dtype=np.float64)

    spot_path = SpotPath(
        path_id=f"deep_itm_{option_type}",
        model="historical_bars",
        seed=None,
        times=np.array([0.0, 1.0 / 12.0, 2.0 / 12.0, 0.25], dtype=np.float64),
        spots=itm_spots,
        is_stale=np.zeros(4, dtype=bool),
        staleness_seconds=np.zeros(4, dtype=np.float64),
        gap_shocks=np.zeros(4, dtype=np.float64),
    )

    market = MarketParams(spot0=100.0, risk_free_rate=0.05, dividend_yield=0.0, hedge_volatility=0.20)
    policy = PolicyConfig(name="daily", policy_type="periodic", rebalance_every_n_steps=1)

    def make_cfg(settlement_type: str, spread_bps: float) -> SimulationConfig:
        return SimulationConfig(
            scenario_name=f"settle_{settlement_type}",
            contract=OptionContract(
                contract_id="S1",
                underlying="SYN_EQ",
                option_type=option_type,
                strike=100.0,
                maturity_years=0.25,
                contracts=2,
                multiplier=100,
                position_side=position_side,
                settlement_type=settlement_type,
            ),
            market=market,
            costs=TransactionCostConfig(half_spread_bps=spread_bps, settlement_spread_bps=spread_bps),
            path=PathConfig(model="gbm", num_paths=1, num_steps=3),
            policy=policy,
        )

    res_cash_0 = simulate_path(spot_path, make_cfg("cash", 0.0))
    res_phys_0 = simulate_path(spot_path, make_cfg("physical", 0.0))
    assert res_cash_0.diagnostics.option_exercised is True
    assert res_phys_0.diagnostics.option_exercised is True
    # Zero-cost cash and physical settlement must agree within currency-cent rounding
    assert abs(res_cash_0.terminal_hedge_error - res_phys_0.terminal_hedge_error) <= Decimal("0.02")

    # Entering expiry deep ITM (|delta| > 0.5), physical delivery avoids unwinding the full block
    res_cash_spr = simulate_path(spot_path, make_cfg("cash", 10.0))
    res_phys_spr = simulate_path(spot_path, make_cfg("physical", 10.0))
    assert res_phys_spr.total_spread_cost < res_cash_spr.total_spread_cost
    assert res_phys_spr.diagnostics.ledger_residual == Decimal("0.00")
    assert res_cash_spr.diagnostics.ledger_residual == Decimal("0.00")


def test_ledger_rejects_negative_transaction_cost() -> None:
    ledger = SelfFinancingLedger(Decimal("0.01"))
    with pytest.raises(LedgerImbalanceError):
        ledger.record_step(
            path_id="bad",
            step=0,
            time_years=0.0,
            spot=100.0,
            option_price=5.0,
            option_delta=0.5,
            option_sign=-1,
            total_units=100,
            target_shares=50.0,
            trade_shares=50.0,
            exercise_share_delivery=0.0,
            exec_price=100.0,
            financing_interest=Decimal("0.00"),
            dividend_cash=Decimal("0.00"),
            trade_mid_cash=Decimal("-5000.00"),
            spread_cost=Decimal("-1.00"),
            fee_cost=Decimal("0.00"),
            option_cash_flow=Decimal("500.00"),
            event_type="INIT",
        )
