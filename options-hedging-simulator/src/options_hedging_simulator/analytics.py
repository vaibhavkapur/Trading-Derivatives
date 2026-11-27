"""Portfolio analytics, hedge-error quantiles, turnover, costs, and convergence metrics."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from decimal import Decimal
import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .contracts import SimulationConfig, TransactionCostConfig
from .pricing import BlackScholesPricer, PricingModel
from .simulator import BatchSimulationResult, simulate_batch


@dataclass(frozen=True)
class PolicyMetrics:
    """Aggregated hedge error, turnover, transaction cost, and diagnostic statistics."""

    policy_name: str
    policy_type: str
    num_paths: int
    initial_option_value: float
    hedge_error_mean: float
    hedge_error_std: float
    hedge_error_rmse: float
    hedge_error_mae: float
    hedge_error_min: float
    hedge_error_p05: float
    hedge_error_p25: float
    hedge_error_p50: float
    hedge_error_p75: float
    hedge_error_p95: float
    hedge_error_max: float
    hedge_error_cvar05: float
    hedge_error_std_pct_premium: float
    mean_spread_cost: float
    mean_fee_cost: float
    mean_total_cost: float
    p95_total_cost: float
    mean_gross_shares_traded: float
    mean_gross_notional_traded: float
    mean_turnover_multiple: float
    mean_rebalance_count: float
    exercise_rate: float
    total_stale_skips: int
    total_stale_fallbacks: int
    total_gap_shock_events: int
    max_abs_ledger_residual: str
    fills_digest: str

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def summarize_batch(batch: BatchSimulationResult) -> PolicyMetrics:
    """Compute hedge error quantiles, turnover, costs, and reconciliation diagnostics."""
    results = batch.path_results
    n = len(results)
    errors = np.array([float(r.terminal_hedge_error) for r in results], dtype=np.float64)
    spreads = np.array([float(r.total_spread_cost) for r in results], dtype=np.float64)
    fees = np.array([float(r.total_fee_cost) for r in results], dtype=np.float64)
    total_costs = np.array([float(r.total_transaction_cost) for r in results], dtype=np.float64)
    shares_traded = np.array([r.gross_shares_traded for r in results], dtype=np.float64)
    notional_traded = np.array([r.gross_notional_traded for r in results], dtype=np.float64)
    turnover_mult = np.array([r.turnover_multiple for r in results], dtype=np.float64)
    rebalances = np.array([r.diagnostics.rebalance_count for r in results], dtype=np.float64)
    exercised = np.array([1.0 if r.diagnostics.option_exercised else 0.0 for r in results], dtype=np.float64)

    p05, p25, p50, p75, p95 = np.quantile(errors, [0.05, 0.25, 0.50, 0.75, 0.95])
    tail_mask = errors <= p05
    cvar05 = float(np.mean(errors[tail_mask])) if np.any(tail_mask) else float(p05)

    ddof = 1 if n > 1 else 0
    err_std = float(np.std(errors, ddof=ddof))
    err_rmse = float(np.sqrt(np.mean(errors * errors)))
    err_mae = float(np.mean(np.abs(errors)))
    init_val = float(results[0].initial_option_value)
    std_pct_prem = (100.0 * err_std / init_val) if init_val > 0.0 else 0.0

    max_residual = max((abs(r.diagnostics.ledger_residual) for r in results), default=Decimal("0.00"))

    return PolicyMetrics(
        policy_name=batch.config.policy.name,
        policy_type=batch.config.policy.policy_type,
        num_paths=n,
        initial_option_value=init_val,
        hedge_error_mean=float(np.mean(errors)),
        hedge_error_std=err_std,
        hedge_error_rmse=err_rmse,
        hedge_error_mae=err_mae,
        hedge_error_min=float(np.min(errors)),
        hedge_error_p05=float(p05),
        hedge_error_p25=float(p25),
        hedge_error_p50=float(p50),
        hedge_error_p75=float(p75),
        hedge_error_p95=float(p95),
        hedge_error_max=float(np.max(errors)),
        hedge_error_cvar05=cvar05,
        hedge_error_std_pct_premium=std_pct_prem,
        mean_spread_cost=float(np.mean(spreads)),
        mean_fee_cost=float(np.mean(fees)),
        mean_total_cost=float(np.mean(total_costs)),
        p95_total_cost=float(np.quantile(total_costs, 0.95)),
        mean_gross_shares_traded=float(np.mean(shares_traded)),
        mean_gross_notional_traded=float(np.mean(notional_traded)),
        mean_turnover_multiple=float(np.mean(turnover_mult)),
        mean_rebalance_count=float(np.mean(rebalances)),
        exercise_rate=float(np.mean(exercised)),
        total_stale_skips=sum(r.diagnostics.stale_price_skips for r in results),
        total_stale_fallbacks=sum(r.diagnostics.stale_price_fallbacks for r in results),
        total_gap_shock_events=sum(r.diagnostics.gap_shock_events for r in results),
        max_abs_ledger_residual=str(max_residual),
        fills_digest=batch.fills_digest,
    )


def comparison_dataframe(batches: Dict[str, BatchSimulationResult]) -> pd.DataFrame:
    """Build a summary pandas DataFrame comparing metrics across policies."""
    rows = [summarize_batch(batch).to_dict() for batch in batches.values()]
    return pd.DataFrame(rows)


def batch_paths_dataframe(batch: BatchSimulationResult) -> pd.DataFrame:
    """Build a per-path summary pandas DataFrame for one simulation batch."""
    return pd.DataFrame([r.to_summary_dict() for r in batch.path_results])


@dataclass(frozen=True)
class ConvergencePoint:
    """One grid point in a zero-cost discrete hedging convergence study."""

    num_steps: int
    dt_years: float
    sqrt_dt: float
    hedge_error_mean: float
    hedge_error_std: float
    hedge_error_rmse: float
    hedge_error_std_pct_premium: float
    mean_rebalance_count: float
    max_abs_ledger_residual: str


@dataclass(frozen=True)
class ConvergenceReport:
    """Convergence study measuring zero-cost hedge error std vs step refinement."""

    scenario_name: str
    num_paths: int
    seed: int
    points: Tuple[ConvergencePoint, ...]
    empirical_loglog_slope_vs_steps: float
    theoretical_slope_vs_steps: float
    monotone_std_reduction: bool

    def to_dict(self) -> Dict[str, object]:
        return {
            "scenario_name": self.scenario_name,
            "num_paths": self.num_paths,
            "seed": self.seed,
            "empirical_loglog_slope_vs_steps": self.empirical_loglog_slope_vs_steps,
            "theoretical_slope_vs_steps": self.theoretical_slope_vs_steps,
            "monotone_std_reduction": self.monotone_std_reduction,
            "points": [asdict(p) for p in self.points],
        }


def run_zero_cost_convergence(
    base_config: SimulationConfig,
    step_counts: Sequence[int],
    *,
    pricer: Optional[PricingModel] = None,
) -> ConvergenceReport:
    """Run zero-cost frictionless periodic hedging across increasing step counts.

    Under continuous-time Black-Scholes with realized_vol == hedge_vol and zero
    transaction costs, the discrete rebalancing error standard deviation scales
    as O(N^{-1/2}) = O(sqrt(dt)).
    """
    pricer_impl = pricer or BlackScholesPricer()
    zero_costs = TransactionCostConfig(
        half_spread_bps=0.0,
        fee_per_share=0.0,
        fee_per_trade=0.0,
        min_fee_per_trade=0.0,
        settlement_spread_bps=0.0,
        cash_borrow_spread_bps=0.0,
        share_lot_size=0.0,
    )
    sorted_steps = sorted(set(int(s) for s in step_counts))
    points: List[ConvergencePoint] = []
    maturity = base_config.contract.maturity_years

    for n_steps in sorted_steps:
        cfg_n = replace(
            base_config,
            scenario_name=f"{base_config.scenario_name}_n{n_steps}",
            costs=zero_costs,
            path=replace(base_config.path, num_steps=n_steps),
            policy=replace(base_config.policy, policy_type="periodic", rebalance_every_n_steps=1, min_trade_shares=0.0),
        )
        batch = simulate_batch(cfg_n, pricer=pricer_impl, store_entries_limit=1)
        metrics = summarize_batch(batch)
        dt = maturity / n_steps
        points.append(
            ConvergencePoint(
                num_steps=n_steps,
                dt_years=dt,
                sqrt_dt=math.sqrt(dt),
                hedge_error_mean=metrics.hedge_error_mean,
                hedge_error_std=metrics.hedge_error_std,
                hedge_error_rmse=metrics.hedge_error_rmse,
                hedge_error_std_pct_premium=metrics.hedge_error_std_pct_premium,
                mean_rebalance_count=metrics.mean_rebalance_count,
                max_abs_ledger_residual=metrics.max_abs_ledger_residual,
            )
        )

    log_n = np.log(np.array([p.num_steps for p in points], dtype=np.float64))
    log_std = np.log(np.maximum(np.array([p.hedge_error_std for p in points], dtype=np.float64), 1e-12))
    slope = float(np.polyfit(log_n, log_std, 1)[0]) if len(points) >= 2 else 0.0
    monotone = all(points[i].hedge_error_std > points[i + 1].hedge_error_std for i in range(len(points) - 1))

    return ConvergenceReport(
        scenario_name=base_config.scenario_name,
        num_paths=base_config.path.num_paths,
        seed=base_config.path.seed,
        points=tuple(points),
        empirical_loglog_slope_vs_steps=slope,
        theoretical_slope_vs_steps=-0.5,
        monotone_std_reduction=monotone,
    )
