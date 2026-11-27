"""Tests for seeded GBM, Merton jump-diffusion, and historical bar path loading."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from options_hedging_simulator.contracts import DeterministicShock, JumpParams, MarketParams, OptionContract, PathConfig
from options_hedging_simulator.errors import ValidationError
from options_hedging_simulator.paths import generate_gbm_paths, generate_merton_jump_paths, load_historical_bars

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "data" / "fixtures"


def test_gbm_reproducibility_and_moments() -> None:
    contract = OptionContract(contract_id="C1", underlying="SYN", option_type="call", strike=100.0, maturity_years=0.5)
    market = MarketParams(spot0=100.0, risk_free_rate=0.05, dividend_yield=0.01, hedge_volatility=0.20)
    cfg = PathConfig(model="gbm", num_paths=2000, num_steps=50, seed=12345)

    paths_a = generate_gbm_paths(market, contract, cfg)
    paths_b = generate_gbm_paths(market, contract, cfg)
    paths_c = generate_gbm_paths(market, contract, PathConfig(model="gbm", num_paths=2000, num_steps=50, seed=99999))

    assert np.array_equal(paths_a[0].spots, paths_b[0].spots)
    assert not np.array_equal(paths_a[0].spots, paths_c[0].spots)

    log_rets = np.array([math.log(p.spots[-1] / p.spots[0]) for p in paths_a])
    expected_mean = (market.effective_drift - 0.5 * 0.20**2) * 0.5
    expected_std = 0.20 * math.sqrt(0.5)
    assert float(np.mean(log_rets)) == pytest.approx(expected_mean, abs=0.015)
    assert float(np.std(log_rets, ddof=1)) == pytest.approx(expected_std, abs=0.015)


def test_merton_jump_reproducibility_and_deterministic_shock() -> None:
    contract = OptionContract(contract_id="C2", underlying="SYN", option_type="put", strike=100.0, maturity_years=0.25)
    market = MarketParams(spot0=100.0, risk_free_rate=0.05, hedge_volatility=0.20)
    jp = JumpParams(
        jump_intensity=5.0,
        jump_mean=-0.05,
        jump_std=0.04,
        deterministic_shocks=(DeterministicShock(step=5, pct_return=-0.15),),
    )
    cfg = PathConfig(model="merton_jump", num_paths=25, num_steps=20, seed=777, jump_params=jp)

    p1 = generate_merton_jump_paths(market, contract, cfg)
    p2 = generate_merton_jump_paths(market, contract, cfg)
    assert np.array_equal(p1[0].spots, p2[0].spots)
    assert all(p.gap_shocks[5] <= -0.149 or abs(p.gap_shocks[5]) > 0.0 for p in p1)
    assert all(np.all(p.spots > 0.0) for p in p1)


def test_historical_bar_loading_and_validation(tmp_path: Path) -> None:
    sp = load_historical_bars(FIXTURES_DIR / "historical_permitted_bars_sample.csv")
    assert sp.num_steps == 21
    assert sp.spots[0] == 100.0
    assert not np.any(sp.is_stale)

    stale = load_historical_bars(FIXTURES_DIR / "stale_price_bars.csv")
    assert bool(stale.is_stale[2]) is True
    assert stale.staleness_seconds[2] == 172800.0

    bad_csv = tmp_path / "non_monotonic.csv"
    bad_csv.write_text(
        "timestamp_utc,spot\n2026-01-02T16:00:00Z,100.0\n2026-01-02T16:00:00Z,101.0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_historical_bars(bad_csv)
