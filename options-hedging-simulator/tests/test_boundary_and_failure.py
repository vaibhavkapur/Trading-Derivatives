"""Boundary and failure behavior tests: gap shocks, stale prices, and expiry settlement."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from options_hedging_simulator.contracts import (
    MarketParams,
    OptionContract,
    PathConfig,
    PolicyConfig,
    SimulationConfig,
    TransactionCostConfig,
)
from options_hedging_simulator.errors import StalePriceError
from options_hedging_simulator.paths import SpotPath, load_historical_bars
from options_hedging_simulator.simulator import simulate_path

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "data" / "fixtures"


def _bars_config(bars_file: str, *, stale_policy: str = "skip", option_type: str = "put", settlement_type: str = "cash") -> SimulationConfig:
    return SimulationConfig(
        scenario_name=f"boundary_{Path(bars_file).stem}_{stale_policy}",
        contract=OptionContract(
            contract_id="BND-1",
            underlying="SYN_EQ",
            option_type=option_type,
            strike=100.0,
            maturity_years=0.25,
            contracts=1,
            multiplier=100,
            position_side="short",
            settlement_type=settlement_type,
        ),
        market=MarketParams(spot0=100.0, risk_free_rate=0.05, hedge_volatility=0.20),
        costs=TransactionCostConfig(half_spread_bps=2.0, fee_per_share=0.005),
        path=PathConfig(
            model="historical_bars",
            bars_path=str(FIXTURES_DIR / bars_file),
            stale_price_policy=stale_policy,
        ),
        policy=PolicyConfig(name="every_step", policy_type="periodic", rebalance_every_n_steps=1),
    )


def test_gap_shock_produces_hedge_loss_and_exact_reconciliation() -> None:
    sp = load_historical_bars(FIXTURES_DIR / "gap_shock_bars.csv")
    cfg = _bars_config("gap_shock_bars.csv", option_type="put", settlement_type="physical")
    res = simulate_path(sp, cfg)

    # Short put suffers a discrete negative gamma / gap loss when spot drops -18% instantaneously
    assert res.diagnostics.gap_shock_events >= 1
    assert res.terminal_hedge_error < Decimal("0.00")
    assert res.diagnostics.option_exercised is True
    assert res.diagnostics.ledger_residual == Decimal("0.00")
    assert res.ledger_entries[-1].shares_after == 0.0


def test_stale_price_policies_error_skip_and_use_last() -> None:
    sp = load_historical_bars(FIXTURES_DIR / "stale_price_bars.csv")

    # 1. Strict error policy raises StalePriceError with step context
    with pytest.raises(StalePriceError) as exc_info:
        simulate_path(sp, _bars_config("stale_price_bars.csv", stale_policy="error", option_type="call"))
    assert exc_info.value.step == 2
    assert exc_info.value.staleness_seconds == 172800.0

    # 2. Skip policy skips trading on step 2 while still accruing cash financing
    res_skip = simulate_path(sp, _bars_config("stale_price_bars.csv", stale_policy="skip", option_type="call"))
    assert res_skip.diagnostics.stale_price_skips == 1
    assert res_skip.ledger_entries[2].event_type == "STALE_SKIP"
    assert res_skip.ledger_entries[2].trade_shares == 0.0
    assert res_skip.diagnostics.ledger_residual == Decimal("0.00")

    # 3. Use-last policy falls back to last valid spot
    res_last = simulate_path(sp, _bars_config("stale_price_bars.csv", stale_policy="use_last", option_type="call"))
    assert res_last.diagnostics.stale_price_fallbacks == 1
    assert res_last.ledger_entries[2].event_type == "STALE_USE_LAST"
    assert res_last.diagnostics.ledger_residual == Decimal("0.00")


def test_stale_initial_and_stale_expiry_boundary_behavior() -> None:
    cfg_skip = _bars_config("stale_price_bars.csv", stale_policy="skip", option_type="call")
    cfg_err = _bars_config("stale_price_bars.csv", stale_policy="error", option_type="call")

    # Stale initial price always raises StalePriceError
    stale_init = SpotPath(
        path_id="stale_init",
        model="historical_bars",
        seed=None,
        times=np.array([0.0, 0.125, 0.25]),
        spots=np.array([100.0, 102.0, 104.0]),
        is_stale=np.array([True, False, False]),
        staleness_seconds=np.array([90000.0, 0.0, 0.0]),
        gap_shocks=np.zeros(3),
    )
    with pytest.raises(StalePriceError):
        simulate_path(stale_init, cfg_skip)

    # Stale expiry price: raises under error, flags stale_expiry_settlement under skip
    stale_end = SpotPath(
        path_id="stale_end",
        model="historical_bars",
        seed=None,
        times=np.array([0.0, 0.125, 0.25]),
        spots=np.array([100.0, 103.0, 103.0]),
        is_stale=np.array([False, False, True]),
        staleness_seconds=np.array([0.0, 0.0, 100000.0]),
        gap_shocks=np.zeros(3),
    )
    with pytest.raises(StalePriceError):
        simulate_path(stale_end, cfg_err)
    res_end = simulate_path(stale_end, cfg_skip)
    assert res_end.diagnostics.stale_expiry_settlement is True
    assert res_end.terminal_spot == 103.0
    assert res_end.diagnostics.ledger_residual == Decimal("0.00")


def test_expiry_settlement_exact_atm_pin_and_otm_unexercised() -> None:
    atm_path = SpotPath(
        path_id="atm_pin",
        model="historical_bars",
        seed=None,
        times=np.array([0.0, 0.125, 0.25]),
        spots=np.array([100.0, 101.0, 100.0]),
        is_stale=np.array([False, False, False]),
        staleness_seconds=np.zeros(3),
        gap_shocks=np.zeros(3),
    )
    cfg_phys = _bars_config("deterministic_3step_bars.csv", option_type="call", settlement_type="physical")
    res = simulate_path(atm_path, cfg_phys)
    assert res.diagnostics.option_exercised is False
    assert res.terminal_option_payoff == Decimal("0.00")
    assert res.ledger_entries[-1].event_type == "SETTLE_PHYSICAL_UNEXERCISED"
    assert res.ledger_entries[-1].shares_after == 0.0
    assert res.diagnostics.ledger_residual == Decimal("0.00")
