"""Pure domain simulation engine for discrete options delta hedging."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import math
from typing import Dict, List, Optional, Sequence, Tuple

from .contracts import (
    ComparisonConfig,
    OptionContract,
    PricingConventions,
    SimulationConfig,
    TransactionCostConfig,
)
from .errors import StalePriceError, ValidationError
from .ledger import FillRecord, LedgerEntry, SelfFinancingLedger, quantize_currency
from .paths import SpotPath, generate_paths
from .policies import HedgePolicy, build_policy
from .pricing import BlackScholesPricer, PricingModel


@dataclass(frozen=True)
class PathDiagnostics:
    """Structured diagnostic counters for a single simulated path."""

    rebalance_count: int
    hold_count: int
    stale_price_skips: int
    stale_price_fallbacks: int
    stale_expiry_settlement: bool
    gap_shock_events: int
    option_exercised: bool
    settlement_type: str
    ledger_residual: Decimal

    def to_dict(self) -> Dict[str, object]:
        return {
            "rebalance_count": self.rebalance_count,
            "hold_count": self.hold_count,
            "stale_price_skips": self.stale_price_skips,
            "stale_price_fallbacks": self.stale_price_fallbacks,
            "stale_expiry_settlement": self.stale_expiry_settlement,
            "gap_shock_events": self.gap_shock_events,
            "option_exercised": self.option_exercised,
            "settlement_type": self.settlement_type,
            "ledger_residual": str(self.ledger_residual),
        }


@dataclass(frozen=True)
class PathSimulationResult:
    """Complete result of delta-hedging a single spot path."""

    path_id: str
    policy_name: str
    initial_option_value: Decimal
    terminal_spot: float
    terminal_option_payoff: Decimal
    terminal_hedge_error: Decimal
    total_spread_cost: Decimal
    total_fee_cost: Decimal
    total_transaction_cost: Decimal
    total_financing: Decimal
    total_dividends: Decimal
    gross_shares_traded: float
    gross_notional_traded: float
    turnover_multiple: float
    diagnostics: PathDiagnostics
    fills: Tuple[FillRecord, ...]
    ledger_entries: Tuple[LedgerEntry, ...]

    def to_summary_dict(self) -> Dict[str, object]:
        return {
            "path_id": self.path_id,
            "policy_name": self.policy_name,
            "initial_option_value": str(self.initial_option_value),
            "terminal_spot": self.terminal_spot,
            "terminal_option_payoff": str(self.terminal_option_payoff),
            "terminal_hedge_error": str(self.terminal_hedge_error),
            "total_spread_cost": str(self.total_spread_cost),
            "total_fee_cost": str(self.total_fee_cost),
            "total_transaction_cost": str(self.total_transaction_cost),
            "total_financing": str(self.total_financing),
            "total_dividends": str(self.total_dividends),
            "gross_shares_traded": self.gross_shares_traded,
            "gross_notional_traded": self.gross_notional_traded,
            "turnover_multiple": self.turnover_multiple,
            "rebalance_count": self.diagnostics.rebalance_count,
            "stale_price_skips": self.diagnostics.stale_price_skips,
            "stale_price_fallbacks": self.diagnostics.stale_price_fallbacks,
            "gap_shock_events": self.diagnostics.gap_shock_events,
            "option_exercised": self.diagnostics.option_exercised,
            "ledger_residual": str(self.diagnostics.ledger_residual),
        }


@dataclass(frozen=True)
class BatchSimulationResult:
    """Aggregated simulation output across all paths for one SimulationConfig."""

    run_id: str
    config_hash: str
    config: SimulationConfig
    path_results: Tuple[PathSimulationResult, ...]
    fills_digest: str


def _accrual_factor(rate: float, dt: float, compounding: str) -> float:
    if dt <= 0.0 or rate == 0.0:
        return 0.0
    if compounding == "continuous":
        return math.expm1(rate * dt)
    return rate * dt


def _round_lot(trade_shares: float, lot_size: float) -> float:
    if lot_size <= 0.0 or abs(trade_shares) <= 1e-12:
        return trade_shares
    return round(trade_shares / lot_size) * lot_size


def _compute_trade_costs(
    trade_shares: float,
    spot: float,
    costs: TransactionCostConfig,
    *,
    spread_bps: float,
    quantum: Decimal,
) -> Tuple[float, Decimal, Decimal, Decimal]:
    """Compute execution price, mid cash flow, spread cost, and fee cost."""
    if abs(trade_shares) <= 1e-12:
        zero = quantize_currency(0, quantum)
        return spot, zero, zero, zero

    sign = 1.0 if trade_shares > 0.0 else -1.0
    spread_frac = spread_bps / 10000.0
    exec_price = spot * (1.0 + sign * spread_frac)
    qty = abs(trade_shares)

    mid_cash = quantize_currency(-trade_shares * spot, quantum)
    spread_cost = quantize_currency(qty * spot * spread_frac, quantum)
    raw_fee = costs.fee_per_trade + qty * costs.fee_per_share
    fee_val = max(costs.min_fee_per_trade, raw_fee) if (raw_fee > 0.0 or costs.min_fee_per_trade > 0.0) else 0.0
    fee_cost = quantize_currency(fee_val, quantum)
    return exec_price, mid_cash, spread_cost, fee_cost


def _compute_fills_digest(results: Sequence[PathSimulationResult]) -> str:
    """Deterministic SHA-256 digest over all fills for exact replay verification."""
    hasher = hashlib.sha256()
    for res in results:
        for fill in res.fills:
            payload = json.dumps(fill.to_dict(), sort_keys=True, separators=(",", ":"))
            hasher.update(payload.encode("utf-8"))
            hasher.update(b"\n")
    return hasher.hexdigest()


def simulate_path(
    path: SpotPath,
    config: SimulationConfig,
    *,
    pricer: Optional[PricingModel] = None,
    policy: Optional[HedgePolicy] = None,
    store_entries: bool = True,
) -> PathSimulationResult:
    """Simulate discrete delta hedging over a single SpotPath with exact ledger reconciliation."""
    pricer_impl = pricer or BlackScholesPricer()
    policy_impl = policy or build_policy(config.policy)

    contract: OptionContract = config.contract
    market = config.market
    costs = config.costs
    conventions: PricingConventions = config.conventions
    quantum = conventions.currency_quantum

    n_steps = path.num_steps
    maturity = float(path.times[-1])
    total_units = contract.total_units
    option_sign = contract.option_sign
    hedge_direction = -option_sign  # +1 for short option, -1 for long option

    if path.is_stale[0]:
        raise StalePriceError(
            f"Initial market price at step 0 is stale (staleness={path.staleness_seconds[0]}s)",
            step=0,
            staleness_seconds=float(path.staleness_seconds[0]),
        )

    ledger = SelfFinancingLedger(quantum, store_entries=store_entries)

    rebalance_count = 0
    hold_count = 0
    stale_skips = 0
    stale_fallbacks = 0
    stale_expiry = False
    gap_shock_events = 0

    # --- Step 0: Option inception and initial delta hedge ---
    t0 = float(path.times[0])
    s0 = float(path.spots[0])
    tau0 = max(0.0, maturity - t0)
    quote0 = pricer_impl.price_and_greeks(
        contract,
        spot=s0,
        time_to_expiry=tau0,
        rate=market.risk_free_rate,
        dividend_yield=market.dividend_yield,
        volatility=market.hedge_volatility,
    )
    initial_option_value = quantize_currency(quote0.price * total_units, quantum)
    # Short option receives +premium; long option pays -premium
    init_option_cf = quantize_currency(-option_sign * quote0.price * total_units, quantum)

    raw_target0 = hedge_direction * total_units * quote0.delta
    trade0 = _round_lot(raw_target0, costs.share_lot_size)
    exec_p0, mid_cf0, spread0, fee0 = _compute_trade_costs(
        trade0,
        s0,
        costs,
        spread_bps=costs.half_spread_bps,
        quantum=quantum,
    )
    zero = quantize_currency(0, quantum)
    ledger.record_step(
        path_id=path.path_id,
        step=0,
        time_years=t0,
        spot=s0,
        option_price=quote0.price,
        option_delta=quote0.delta,
        option_sign=option_sign,
        total_units=total_units,
        target_shares=raw_target0,
        trade_shares=trade0,
        exercise_share_delivery=0.0,
        exec_price=exec_p0,
        financing_interest=zero,
        dividend_cash=zero,
        trade_mid_cash=mid_cf0,
        spread_cost=spread0,
        fee_cost=fee0,
        option_cash_flow=init_option_cf,
        event_type="INIT",
        fill_reason="initial_hedge",
        is_settled=False,
    )
    if abs(trade0) > 1e-12:
        rebalance_count += 1

    last_valid_spot = s0
    prev_spot = s0
    prev_time = t0

    # --- Intermediate steps: 1 .. N-1 ---
    for step in range(1, n_steps):
        t_now = float(path.times[step])
        dt = t_now - prev_time
        tau = max(0.0, maturity - t_now)
        raw_spot = float(path.spots[step])
        stale_flag = bool(path.is_stale[step])
        staleness_sec = float(path.staleness_seconds[step])

        # Cash financing & continuous dividend accrual over [prev_time, t_now]
        eff_rate = market.risk_free_rate
        if ledger.cash < 0 and costs.cash_borrow_spread_bps > 0.0:
            eff_rate += costs.cash_borrow_spread_bps / 10000.0
        fin_factor = _accrual_factor(eff_rate, dt, conventions.compounding)
        financing = quantize_currency(float(ledger.cash) * fin_factor, quantum)

        ref_spot_for_div = last_valid_spot if stale_flag else raw_spot
        div_factor = _accrual_factor(market.dividend_yield, dt, conventions.compounding)
        div_cash = quantize_currency(ledger.shares * 0.5 * (prev_spot + ref_spot_for_div) * div_factor, quantum)

        # Gap shock diagnostics
        if abs(float(path.gap_shocks[step])) > 1e-9 or (
            not stale_flag and abs(math.log(raw_spot / prev_spot)) >= 0.05
        ):
            gap_shock_events += 1

        if stale_flag:
            if config.path.stale_price_policy == "error":
                raise StalePriceError(
                    f"Stale price encountered at step {step} (staleness={staleness_sec}s)",
                    step=step,
                    staleness_seconds=staleness_sec,
                )
            if config.path.stale_price_policy == "skip":
                stale_skips += 1
                quote_ref = pricer_impl.price_and_greeks(
                    contract,
                    spot=last_valid_spot,
                    time_to_expiry=tau,
                    rate=market.risk_free_rate,
                    dividend_yield=market.dividend_yield,
                    volatility=market.hedge_volatility,
                )
                target_shares = hedge_direction * total_units * quote_ref.delta
                ledger.record_step(
                    path_id=path.path_id,
                    step=step,
                    time_years=t_now,
                    spot=last_valid_spot,
                    option_price=quote_ref.price,
                    option_delta=quote_ref.delta,
                    option_sign=option_sign,
                    total_units=total_units,
                    target_shares=target_shares,
                    trade_shares=0.0,
                    exercise_share_delivery=0.0,
                    exec_price=last_valid_spot,
                    financing_interest=financing,
                    dividend_cash=div_cash,
                    trade_mid_cash=zero,
                    spread_cost=zero,
                    fee_cost=zero,
                    option_cash_flow=zero,
                    event_type="STALE_SKIP",
                    is_settled=False,
                )
                prev_time = t_now
                continue
            # stale_price_policy == "use_last"
            stale_fallbacks += 1
            eff_spot = last_valid_spot
        else:
            eff_spot = raw_spot
            last_valid_spot = raw_spot

        quote = pricer_impl.price_and_greeks(
            contract,
            spot=eff_spot,
            time_to_expiry=tau,
            rate=market.risk_free_rate,
            dividend_yield=market.dividend_yield,
            volatility=market.hedge_volatility,
        )
        target_shares = hedge_direction * total_units * quote.delta
        current_unit_delta = ledger.shares / (hedge_direction * total_units)

        decision = policy_impl.decide(
            step=step,
            num_steps=n_steps,
            current_unit_delta=current_unit_delta,
            target_unit_delta=quote.delta,
            current_shares=ledger.shares,
            target_shares=target_shares,
        )

        if decision.rebalance:
            raw_trade = target_shares - ledger.shares
            trade = _round_lot(raw_trade, costs.share_lot_size)
            if abs(trade) < config.policy.min_trade_shares:
                trade = 0.0
        else:
            trade = 0.0

        exec_p, mid_cf, spr, fee = _compute_trade_costs(
            trade,
            eff_spot,
            costs,
            spread_bps=costs.half_spread_bps,
            quantum=quantum,
        )

        if abs(trade) > 1e-12:
            rebalance_count += 1
            ev_type = "STALE_USE_LAST" if stale_flag else "REBALANCE"
        else:
            hold_count += 1
            ev_type = "STALE_USE_LAST" if stale_flag else "HOLD"

        ledger.record_step(
            path_id=path.path_id,
            step=step,
            time_years=t_now,
            spot=eff_spot,
            option_price=quote.price,
            option_delta=quote.delta,
            option_sign=option_sign,
            total_units=total_units,
            target_shares=target_shares,
            trade_shares=trade,
            exercise_share_delivery=0.0,
            exec_price=exec_p,
            financing_interest=financing,
            dividend_cash=div_cash,
            trade_mid_cash=mid_cf,
            spread_cost=spr,
            fee_cost=fee,
            option_cash_flow=zero,
            event_type=ev_type,
            fill_reason=decision.reason,
            is_settled=False,
        )
        prev_spot = eff_spot
        prev_time = t_now

    # --- Final step N: Expiry settlement and residual unwind ---
    t_end = float(path.times[n_steps])
    dt_end = t_end - prev_time
    raw_spot_end = float(path.spots[n_steps])
    stale_end = bool(path.is_stale[n_steps])
    if stale_end:
        if config.path.stale_price_policy == "error":
            raise StalePriceError(
                f"Stale price encountered at expiry step {n_steps} (staleness={path.staleness_seconds[n_steps]}s)",
                step=n_steps,
                staleness_seconds=float(path.staleness_seconds[n_steps]),
            )
        stale_expiry = True
        s_end = last_valid_spot
    else:
        s_end = raw_spot_end

    if abs(float(path.gap_shocks[n_steps])) > 1e-9 or (
        not stale_end and abs(math.log(s_end / prev_spot)) >= 0.05
    ):
        gap_shock_events += 1

    eff_rate_end = market.risk_free_rate
    if ledger.cash < 0 and costs.cash_borrow_spread_bps > 0.0:
        eff_rate_end += costs.cash_borrow_spread_bps / 10000.0
    fin_end = quantize_currency(
        float(ledger.cash) * _accrual_factor(eff_rate_end, dt_end, conventions.compounding),
        quantum,
    )
    div_end = quantize_currency(
        ledger.shares * 0.5 * (prev_spot + s_end) * _accrual_factor(market.dividend_yield, dt_end, conventions.compounding),
        quantum,
    )

    quote_end = pricer_impl.price_and_greeks(
        contract,
        spot=s_end,
        time_to_expiry=0.0,
        rate=market.risk_free_rate,
        dividend_yield=market.dividend_yield,
        volatility=market.hedge_volatility,
    )
    payoff_per_unit = quote_end.intrinsic_value
    exercised = payoff_per_unit > 0.0
    terminal_option_payoff = quantize_currency(payoff_per_unit * total_units, quantum)

    if contract.settlement_type == "cash":
        exercise_delivery = 0.0
        option_settle_cf = quantize_currency(option_sign * payoff_per_unit * total_units, quantum)
        unwind_trade = -ledger.shares
        settle_event = "SETTLE_CASH"
    else:
        # Physical exercise / assignment
        if exercised:
            if contract.option_type == "call":
                # Long call receives +Q shares and pays -K*Q; short call delivers -Q shares and receives +K*Q
                exercise_delivery = float(option_sign * total_units)
                option_settle_cf = quantize_currency(-option_sign * contract.strike * total_units, quantum)
            else:
                # Long put delivers -Q shares and receives +K*Q; short put receives +Q shares and pays -K*Q
                exercise_delivery = float(-option_sign * total_units)
                option_settle_cf = quantize_currency(option_sign * contract.strike * total_units, quantum)
            residual_shares = ledger.shares + exercise_delivery
            unwind_trade = -residual_shares
            settle_event = "SETTLE_PHYSICAL_EXERCISED"
        else:
            exercise_delivery = 0.0
            option_settle_cf = zero
            unwind_trade = -ledger.shares
            settle_event = "SETTLE_PHYSICAL_UNEXERCISED"

    exec_p_end, mid_cf_end, spr_end, fee_end = _compute_trade_costs(
        unwind_trade,
        s_end,
        costs,
        spread_bps=costs.effective_settlement_spread_bps,
        quantum=quantum,
    )

    ledger.record_step(
        path_id=path.path_id,
        step=n_steps,
        time_years=t_end,
        spot=s_end,
        option_price=payoff_per_unit,
        option_delta=quote_end.delta,
        option_sign=option_sign,
        total_units=total_units,
        target_shares=0.0,
        trade_shares=unwind_trade,
        exercise_share_delivery=exercise_delivery,
        exec_price=exec_p_end,
        financing_interest=fin_end,
        dividend_cash=div_end,
        trade_mid_cash=mid_cf_end,
        spread_cost=spr_end,
        fee_cost=fee_end,
        option_cash_flow=option_settle_cf,
        event_type=settle_event,
        fill_reason="expiry_unwind",
        is_settled=True,
    )

    residual = ledger.verify_reconciliation(expect_flat_inventory=True)

    contract_notional = market.spot0 * total_units
    turnover_multiple = ledger.gross_notional_traded / contract_notional if contract_notional > 0.0 else 0.0
    total_tx_cost = ledger.total_spread_cost + ledger.total_fee_cost

    diagnostics = PathDiagnostics(
        rebalance_count=rebalance_count,
        hold_count=hold_count,
        stale_price_skips=stale_skips,
        stale_price_fallbacks=stale_fallbacks,
        stale_expiry_settlement=stale_expiry,
        gap_shock_events=gap_shock_events,
        option_exercised=exercised,
        settlement_type=contract.settlement_type,
        ledger_residual=residual,
    )

    return PathSimulationResult(
        path_id=path.path_id,
        policy_name=policy_impl.name,
        initial_option_value=initial_option_value,
        terminal_spot=s_end,
        terminal_option_payoff=terminal_option_payoff,
        terminal_hedge_error=ledger.cash,
        total_spread_cost=ledger.total_spread_cost,
        total_fee_cost=ledger.total_fee_cost,
        total_transaction_cost=total_tx_cost,
        total_financing=ledger.total_financing,
        total_dividends=ledger.total_dividends,
        gross_shares_traded=ledger.gross_shares_traded,
        gross_notional_traded=ledger.gross_notional_traded,
        turnover_multiple=turnover_multiple,
        diagnostics=diagnostics,
        fills=tuple(ledger.fills),
        ledger_entries=tuple(ledger.entries),
    )


def simulate_batch(
    config: SimulationConfig,
    *,
    paths: Optional[Sequence[SpotPath]] = None,
    pricer: Optional[PricingModel] = None,
    store_entries_limit: int = 5,
    base_dir=None,
) -> BatchSimulationResult:
    """Run a batch simulation over generated or provided SpotPaths."""
    sim_paths = list(paths) if paths is not None else generate_paths(
        config.market,
        config.contract,
        config.path,
        base_dir=base_dir,
        day_count=config.conventions.day_count,
    )
    if not sim_paths:
        raise ValidationError("No paths available for simulation", field="paths")

    pricer_impl = pricer or BlackScholesPricer()
    policy_impl = build_policy(config.policy)

    results: List[PathSimulationResult] = []
    for idx, sp in enumerate(sim_paths):
        results.append(
            simulate_path(
                sp,
                config,
                pricer=pricer_impl,
                policy=policy_impl,
                store_entries=(idx < store_entries_limit),
            )
        )

    cfg_hash = config.config_hash()
    fills_digest = _compute_fills_digest(results)
    run_id = f"{config.scenario_name}_{cfg_hash[:8]}_s{config.path.seed}"
    return BatchSimulationResult(
        run_id=run_id,
        config_hash=cfg_hash,
        config=config,
        path_results=tuple(results),
        fills_digest=fills_digest,
    )


def compare_policies(
    comparison_config: ComparisonConfig,
    *,
    paths: Optional[Sequence[SpotPath]] = None,
    pricer: Optional[PricingModel] = None,
    store_entries_limit: int = 3,
    base_dir=None,
) -> Dict[str, BatchSimulationResult]:
    """Evaluate multiple hedging policies on the exact same set of seeded paths."""
    shared_paths = list(paths) if paths is not None else generate_paths(
        comparison_config.market,
        comparison_config.contract,
        comparison_config.path,
        base_dir=base_dir,
        day_count=comparison_config.conventions.day_count,
    )
    pricer_impl = pricer or BlackScholesPricer()
    batches: Dict[str, BatchSimulationResult] = {}
    for sim_cfg in comparison_config.to_simulation_configs():
        batches[sim_cfg.policy.name] = simulate_batch(
            sim_cfg,
            paths=shared_paths,
            pricer=pricer_impl,
            store_entries_limit=store_entries_limit,
        )
    return batches
