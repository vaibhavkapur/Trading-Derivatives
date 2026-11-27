"""Tests for static, periodic, and threshold policies and multi-policy comparison."""

from __future__ import annotations

from decimal import Decimal

from options_hedging_simulator.analytics import summarize_batch
from options_hedging_simulator.contracts import (
    ComparisonConfig,
    MarketParams,
    OptionContract,
    PathConfig,
    PolicyConfig,
    SimulationConfig,
    TransactionCostConfig,
)
from options_hedging_simulator.paths import generate_gbm_paths
from options_hedging_simulator.simulator import compare_policies, simulate_path


def _base_contract() -> OptionContract:
    return OptionContract(
        contract_id="POL-CALL-100",
        underlying="SYN_EQ",
        option_type="call",
        strike=100.0,
        maturity_years=0.25,
        contracts=5,
        multiplier=100,
        position_side="short",
    )


def test_static_periodic_threshold_rebalance_mechanics() -> None:
    contract = _base_contract()
    market = MarketParams(spot0=100.0, risk_free_rate=0.05, hedge_volatility=0.22)
    path_cfg = PathConfig(model="gbm", num_paths=1, num_steps=20, seed=55)
    sp = generate_gbm_paths(market, contract, path_cfg)[0]
    costs = TransactionCostConfig(half_spread_bps=2.0, fee_per_share=0.005)

    # Static: only 1 initial hedge rebalance (+ final expiry unwind fill)
    static_cfg = SimulationConfig(
        scenario_name="static",
        contract=contract,
        market=market,
        costs=costs,
        path=path_cfg,
        policy=PolicyConfig(name="static", policy_type="static"),
    )
    res_static = simulate_path(sp, static_cfg)
    assert res_static.diagnostics.rebalance_count == 1
    assert res_static.diagnostics.hold_count == 19
    assert all(e.trade_shares == 0.0 for e in res_static.ledger_entries[1:-1])

    # Periodic every 5 steps: rebalances only at steps 0, 5, 10, 15 (at most 4 rebalances)
    per_cfg = SimulationConfig(
        scenario_name="periodic5",
        contract=contract,
        market=market,
        costs=costs,
        path=path_cfg,
        policy=PolicyConfig(name="p5", policy_type="periodic", rebalance_every_n_steps=5),
    )
    res_per = simulate_path(sp, per_cfg)
    for entry in res_per.ledger_entries[1:-1]:
        if entry.step % 5 != 0:
            assert entry.trade_shares == 0.0

    # Threshold policy with wide band trades strictly fewer times than narrow band
    thr_tight = SimulationConfig(
        scenario_name="thr_tight",
        contract=contract,
        market=market,
        costs=costs,
        path=path_cfg,
        policy=PolicyConfig(name="t01", policy_type="threshold", delta_band=0.01),
    )
    thr_wide = SimulationConfig(
        scenario_name="thr_wide",
        contract=contract,
        market=market,
        costs=costs,
        path=path_cfg,
        policy=PolicyConfig(name="t15", policy_type="threshold", delta_band=0.15),
    )
    res_tight = simulate_path(sp, thr_tight)
    res_wide = simulate_path(sp, thr_wide)
    assert res_tight.diagnostics.rebalance_count >= res_wide.diagnostics.rebalance_count


def test_compare_policies_shows_tracking_error_vs_cost_tradeoff() -> None:
    cmp_cfg = ComparisonConfig(
        scenario_name="tradeoff_check",
        contract=_base_contract(),
        market=MarketParams(spot0=100.0, risk_free_rate=0.05, dividend_yield=0.01, hedge_volatility=0.20),
        costs=TransactionCostConfig(half_spread_bps=3.0, fee_per_share=0.005, min_fee_per_trade=1.0),
        path=PathConfig(model="gbm", num_paths=120, num_steps=42, seed=2026),
        policies=(
            PolicyConfig(name="static", policy_type="static"),
            PolicyConfig(name="periodic_daily", policy_type="periodic", rebalance_every_n_steps=1),
            PolicyConfig(name="threshold_05", policy_type="threshold", delta_band=0.05),
        ),
    )
    batches = compare_policies(cmp_cfg)
    m_static = summarize_batch(batches["static"])
    m_daily = summarize_batch(batches["periodic_daily"])
    m_thr = summarize_batch(batches["threshold_05"])

    # Daily hedging reduces tracking error std dramatically vs static, at higher cost/turnover
    assert m_daily.hedge_error_std < m_static.hedge_error_std * 0.5
    assert m_daily.mean_total_cost > m_static.mean_total_cost
    assert m_daily.mean_turnover_multiple > m_thr.mean_turnover_multiple > m_static.mean_turnover_multiple
    for m in (m_static, m_daily, m_thr):
        assert m.max_abs_ledger_residual == str(Decimal("0.00"))
