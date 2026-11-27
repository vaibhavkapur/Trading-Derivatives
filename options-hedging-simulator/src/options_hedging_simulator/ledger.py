"""Self-financing cash and inventory ledger with exact currency-precision reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Dict, List, Optional

from .errors import LedgerImbalanceError

ZERO = Decimal("0")


def quantize_currency(value: float | Decimal, quantum: Decimal) -> Decimal:
    """Quantize a monetary value to exact currency minor-unit precision."""
    dec = value if isinstance(value, Decimal) else Decimal(str(value))
    return dec.quantize(quantum, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class FillRecord:
    """Immutable record of an executed underlying hedge trade."""

    path_id: str
    step: int
    time_years: float
    side: str  # "BUY" or "SELL"
    quantity: float  # positive share quantity
    signed_quantity: float  # + for BUY, - for SELL
    mid_price: float
    exec_price: float
    mid_cash_flow: Decimal
    spread_cost: Decimal
    fee_cost: Decimal
    reason: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "path_id": self.path_id,
            "step": self.step,
            "time_years": round(self.time_years, 10),
            "side": self.side,
            "quantity": round(self.quantity, 10),
            "signed_quantity": round(self.signed_quantity, 10),
            "mid_price": round(self.mid_price, 10),
            "exec_price": round(self.exec_price, 10),
            "mid_cash_flow": str(self.mid_cash_flow),
            "spread_cost": str(self.spread_cost),
            "fee_cost": str(self.fee_cost),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class LedgerEntry:
    """Single step row in the self-financing hedging ledger."""

    step: int
    time_years: float
    spot: float
    option_price: float
    option_delta: float
    target_shares: float
    shares_before: float
    trade_shares: float
    exercise_share_delivery: float
    shares_after: float
    exec_price: float
    cash_before: Decimal
    financing_interest: Decimal
    dividend_cash: Decimal
    trade_mid_cash: Decimal
    spread_cost: Decimal
    fee_cost: Decimal
    option_cash_flow: Decimal
    cash_after: Decimal
    portfolio_mtm: Decimal
    event_type: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "step": self.step,
            "time_years": self.time_years,
            "spot": self.spot,
            "option_price": self.option_price,
            "option_delta": self.option_delta,
            "target_shares": self.target_shares,
            "shares_before": self.shares_before,
            "trade_shares": self.trade_shares,
            "exercise_share_delivery": self.exercise_share_delivery,
            "shares_after": self.shares_after,
            "exec_price": self.exec_price,
            "cash_before": str(self.cash_before),
            "financing_interest": str(self.financing_interest),
            "dividend_cash": str(self.dividend_cash),
            "trade_mid_cash": str(self.trade_mid_cash),
            "spread_cost": str(self.spread_cost),
            "fee_cost": str(self.fee_cost),
            "option_cash_flow": str(self.option_cash_flow),
            "cash_after": str(self.cash_after),
            "portfolio_mtm": str(self.portfolio_mtm),
            "event_type": self.event_type,
        }


class SelfFinancingLedger:
    """Tracks cash, underlying inventory, and exact accounting reconciliation.

    Accounting identity enforced on every step:
        cash_after == cash_before
                      + financing_interest
                      + dividend_cash
                      + trade_mid_cash
                      - spread_cost
                      - fee_cost
                      + option_cash_flow

    Inventory identity enforced on every step:
        shares_after == shares_before + trade_shares + exercise_share_delivery
    """

    def __init__(self, currency_quantum: Decimal, *, store_entries: bool = True) -> None:
        self.quantum = currency_quantum
        self.store_entries = store_entries
        self.cash: Decimal = quantize_currency(ZERO, self.quantum)
        self.shares: float = 0.0
        self.entries: List[LedgerEntry] = []
        self.fills: List[FillRecord] = []

        # Running totals for O(1) audit even when store_entries is False
        self.total_financing: Decimal = quantize_currency(ZERO, self.quantum)
        self.total_dividends: Decimal = quantize_currency(ZERO, self.quantum)
        self.total_trade_mid_cash: Decimal = quantize_currency(ZERO, self.quantum)
        self.total_spread_cost: Decimal = quantize_currency(ZERO, self.quantum)
        self.total_fee_cost: Decimal = quantize_currency(ZERO, self.quantum)
        self.total_option_cash_flow: Decimal = quantize_currency(ZERO, self.quantum)
        self.gross_shares_traded: float = 0.0
        self.gross_notional_traded: float = 0.0

    def record_step(
        self,
        *,
        path_id: str,
        step: int,
        time_years: float,
        spot: float,
        option_price: float,
        option_delta: float,
        option_sign: int,
        total_units: int,
        target_shares: float,
        trade_shares: float,
        exercise_share_delivery: float,
        exec_price: float,
        financing_interest: Decimal,
        dividend_cash: Decimal,
        trade_mid_cash: Decimal,
        spread_cost: Decimal,
        fee_cost: Decimal,
        option_cash_flow: Decimal,
        event_type: str,
        fill_reason: Optional[str] = None,
        is_settled: bool = False,
    ) -> LedgerEntry:
        cash_before = self.cash
        shares_before = self.shares

        fin = quantize_currency(financing_interest, self.quantum)
        div = quantize_currency(dividend_cash, self.quantum)
        mid_cf = quantize_currency(trade_mid_cash, self.quantum)
        spr = quantize_currency(spread_cost, self.quantum)
        fee = quantize_currency(fee_cost, self.quantum)
        opt_cf = quantize_currency(option_cash_flow, self.quantum)

        if spr < ZERO or fee < ZERO:
            raise LedgerImbalanceError(f"Transaction costs must be >= 0 (spread={spr}, fee={fee})")

        cash_after = cash_before + fin + div + mid_cf - spr - fee + opt_cf
        shares_after = shares_before + trade_shares + exercise_share_delivery
        if abs(shares_after) < 1e-12:
            shares_after = 0.0

        # Step-level exact accounting verification
        expected_cash_after = cash_before + fin + div + mid_cf - spr - fee + opt_cf
        if cash_after != expected_cash_after:
            raise LedgerImbalanceError(
                f"Step {step} cash imbalance: {cash_after} != {expected_cash_after}"
            )

        option_mtm = 0.0 if is_settled else (option_sign * option_price * total_units)
        portfolio_mtm = quantize_currency(
            float(cash_after) + shares_after * spot + option_mtm,
            self.quantum,
        )

        self.cash = cash_after
        self.shares = shares_after
        self.total_financing += fin
        self.total_dividends += div
        self.total_trade_mid_cash += mid_cf
        self.total_spread_cost += spr
        self.total_fee_cost += fee
        self.total_option_cash_flow += opt_cf

        if abs(trade_shares) > 1e-12:
            qty = abs(trade_shares)
            self.gross_shares_traded += qty
            self.gross_notional_traded += qty * spot
            self.fills.append(
                FillRecord(
                    path_id=path_id,
                    step=step,
                    time_years=time_years,
                    side="BUY" if trade_shares > 0.0 else "SELL",
                    quantity=qty,
                    signed_quantity=trade_shares,
                    mid_price=spot,
                    exec_price=exec_price,
                    mid_cash_flow=mid_cf,
                    spread_cost=spr,
                    fee_cost=fee,
                    reason=fill_reason or event_type,
                )
            )

        entry = LedgerEntry(
            step=step,
            time_years=time_years,
            spot=spot,
            option_price=option_price,
            option_delta=option_delta,
            target_shares=target_shares,
            shares_before=shares_before,
            trade_shares=trade_shares,
            exercise_share_delivery=exercise_share_delivery,
            shares_after=shares_after,
            exec_price=exec_price,
            cash_before=cash_before,
            financing_interest=fin,
            dividend_cash=div,
            trade_mid_cash=mid_cf,
            spread_cost=spr,
            fee_cost=fee,
            option_cash_flow=opt_cf,
            cash_after=cash_after,
            portfolio_mtm=portfolio_mtm,
            event_type=event_type,
        )
        if self.store_entries:
            self.entries.append(entry)
        return entry

    def verify_reconciliation(self, *, expect_flat_inventory: bool = True) -> Decimal:
        """Audit full cumulative cash and inventory reconciliation.

        Returns exact residual (Decimal("0.00") when balanced) or raises LedgerImbalanceError.
        """
        reconstructed_cash = (
            self.total_option_cash_flow
            + self.total_financing
            + self.total_dividends
            + self.total_trade_mid_cash
            - self.total_spread_cost
            - self.total_fee_cost
        )
        residual = self.cash - reconstructed_cash
        if residual != ZERO:
            raise LedgerImbalanceError(
                f"Cumulative cash ledger imbalance: cash={self.cash}, reconstructed={reconstructed_cash}, residual={residual}"
            )

        if self.entries:
            running_cash = quantize_currency(ZERO, self.quantum)
            running_shares = 0.0
            for row in self.entries:
                if row.cash_before != running_cash:
                    raise LedgerImbalanceError(
                        f"Discontinuity at step {row.step}: cash_before={row.cash_before} != prev cash_after={running_cash}"
                    )
                if abs(row.shares_before - running_shares) > 1e-9:
                    raise LedgerImbalanceError(
                        f"Inventory discontinuity at step {row.step}: shares_before={row.shares_before} != {running_shares}"
                    )
                expected_after = (
                    row.cash_before
                    + row.financing_interest
                    + row.dividend_cash
                    + row.trade_mid_cash
                    - row.spread_cost
                    - row.fee_cost
                    + row.option_cash_flow
                )
                if row.cash_after != expected_after:
                    raise LedgerImbalanceError(
                        f"Row {row.step} cash mismatch: {row.cash_after} != {expected_after}"
                    )
                running_cash = row.cash_after
                running_shares = row.shares_after

            if running_cash != self.cash:
                raise LedgerImbalanceError(f"Final entry cash {running_cash} != ledger cash {self.cash}")

        if expect_flat_inventory and abs(self.shares) > 1e-9:
            raise LedgerImbalanceError(f"Expected flat terminal inventory, remaining shares={self.shares}")

        return residual
