"""Integration scenario test: zero-cost convergence as hedge interval shrinks."""

from __future__ import annotations

from options_hedging_simulator.analytics import run_zero_cost_convergence
from options_hedging_simulator.contracts import (
    MarketParams,
    OptionContract,
    PathConfig,
    PolicyConfig,
    SimulationConfig,
    TransactionCostConfig,
)


def test_zero_cost_convergence_as_hedge_interval_shrinks() -> None:
    base_cfg = SimulationConfig(
        scenario_name="integration_zero_cost_convergence",
        contract=OptionContract(
            contract_id="CONV-CALL-100",
            underlying="SYN_EQ",
            option_type="call",
            strike=100.0,
            maturity_years=0.25,
            contracts=10,
            multiplier=100,
            position_side="short",
            settlement_type="cash",
        ),
        market=MarketParams(
            spot0=100.0,
            risk_free_rate=0.05,
            dividend_yield=0.0,
            hedge_volatility=0.20,
            realized_volatility=0.20,
            drift=0.05,
        ),
        costs=TransactionCostConfig(),
        path=PathConfig(model="gbm", num_paths=200, num_steps=16, seed=314159),
        policy=PolicyConfig(name="daily", policy_type="periodic", rebalance_every_n_steps=1),
    )

    report = run_zero_cost_convergence(base_cfg, [8, 16, 32, 64, 128])
    assert report.monotone_std_reduction is True
    # Theoretical Black-Scholes discrete hedging std scales as O(N^{-0.5})
    assert -0.65 <= report.empirical_loglog_slope_vs_steps <= -0.35
    # Finest grid should have less than 35% of coarsest grid's tracking error std
    assert report.points[-1].hedge_error_std < 0.35 * report.points[0].hedge_error_std
    for pt in report.points:
        assert pt.max_abs_ledger_residual == "0.00"
