"""Pure mark-to-market (MTM) and variation margin (VM) valuation analytics."""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Mapping, Sequence, Tuple

from derivatives_margin_engine.models import (
    MarginPolicy,
    NettingSet,
    Position,
    PositionValuation,
    SettlementPrice,
)


def value_netting_set_positions(
    netting_set: NettingSet,
    positions: Sequence[Position],
    price_map: Mapping[str, SettlementPrice],
    policy: MarginPolicy,
) -> Tuple[Tuple[PositionValuation, ...], Decimal, Decimal, Decimal, Decimal]:
    """Mark positions to market and compute exact variation margin adjusted for settlements.

    Invariant:
        variation_margin_base == daily_mtm_movement_base - settled_cash_adjustment_base

    Sign convention:
        - positive daily_mtm_movement_base: mark-to-market gain to participant account
        - negative daily_mtm_movement_base: mark-to-market loss to participant account
        - positive variation_margin_base: VM credit payable to participant
        - negative variation_margin_base: VM call owed by participant
    """
    valuations: List[PositionValuation] = []
    total_mtm = Decimal("0.00")
    total_daily_mtm_movement = Decimal("0.00")
    total_settled_adj = Decimal("0.00")
    total_vm = Decimal("0.00")

    base_ccy = netting_set.base_currency

    for pos in positions:
        sp = price_map[pos.instrument_id]
        prior_mtm_native = pos.quantity * pos.contract_multiplier * pos.prior_settlement_price
        curr_mtm_native = pos.quantity * pos.contract_multiplier * sp.price
        delta_mtm_native = curr_mtm_native - prior_mtm_native

        prior_mtm_base = policy.quantize(
            policy.convert_to_currency(prior_mtm_native, pos.currency, base_ccy)
        )
        curr_mtm_base = policy.quantize(
            policy.convert_to_currency(curr_mtm_native, pos.currency, base_ccy)
        )
        # Preserve exact additive identity in base currency
        daily_mtm_movement_base = policy.quantize(curr_mtm_base - prior_mtm_base)
        settled_adj_base = policy.quantize(
            policy.convert_to_currency(pos.settled_cash_adjustment, pos.currency, base_ccy)
        )
        vm_base = policy.quantize(daily_mtm_movement_base - settled_adj_base)

        valuations.append(
            PositionValuation(
                position_id=pos.position_id,
                netting_set_id=pos.netting_set_id,
                instrument_id=pos.instrument_id,
                risk_group=pos.risk_group,
                quantity=pos.quantity,
                contract_multiplier=pos.contract_multiplier,
                currency=pos.currency,
                base_currency=base_ccy,
                prior_price=pos.prior_settlement_price,
                current_price=sp.price,
                prior_mtm_base=prior_mtm_base,
                current_mtm_base=curr_mtm_base,
                daily_mtm_movement_base=daily_mtm_movement_base,
                settled_cash_adjustment_base=settled_adj_base,
                variation_margin_base=vm_base,
            )
        )
        total_mtm = policy.quantize(total_mtm + curr_mtm_base)
        total_daily_mtm_movement = policy.quantize(
            total_daily_mtm_movement + daily_mtm_movement_base
        )
        total_settled_adj = policy.quantize(total_settled_adj + settled_adj_base)
        total_vm = policy.quantize(total_vm + vm_base)

    # Assert fundamental VM accounting invariant before returning
    assert total_vm == policy.quantize(total_daily_mtm_movement - total_settled_adj), (
        f"VM invariant violated for {netting_set.netting_set_id}: "
        f"{total_vm} != {total_daily_mtm_movement} - {total_settled_adj}"
    )
    return (
        tuple(valuations),
        total_mtm,
        total_daily_mtm_movement,
        total_settled_adj,
        total_vm,
    )
