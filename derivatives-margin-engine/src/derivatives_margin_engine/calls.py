"""Pure shortfall, margin call generation, MTA/rounding rules, and deterministic IDs."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_CEILING
import hashlib
from typing import List, Optional, Tuple

from derivatives_margin_engine.models import (
    CallType,
    MarginCall,
    MarginPolicy,
    NettingSet,
)

ZERO = Decimal("0")


def make_idempotency_key(
    valuation_date: date,
    netting_set_id: str,
    call_type: CallType,
    revision: int,
) -> str:
    return f"{valuation_date.isoformat()}|{netting_set_id}|{call_type.value}|rev{revision}"


def make_call_id(idempotency_key: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:12].upper()
    return f"MC-{digest}"


def _apply_mta_and_rounding(
    raw_amount: Decimal,
    mta: Decimal,
    rounding_increment: Decimal,
    policy: MarginPolicy,
) -> Decimal:
    """Apply Minimum Transfer Amount and ceiling rounding increment to a margin call."""
    if raw_amount <= ZERO:
        return policy.quantize(ZERO)
    if mta > ZERO and raw_amount < mta:
        return policy.quantize(ZERO)
    if rounding_increment > ZERO:
        multiples = (raw_amount / rounding_increment).to_integral_value(rounding=ROUND_CEILING)
        return policy.quantize(multiples * rounding_increment)
    return policy.quantize(raw_amount)


def generate_margin_calls(
    netting_set: NettingSet,
    valuation_date: date,
    revision: int,
    net_im: Decimal,
    eligible_collateral_value: Decimal,
    variation_margin: Decimal,
    policy: MarginPolicy,
    is_settlement_correction: bool = False,
) -> Tuple[Decimal, Decimal, Decimal, Decimal, Decimal, Tuple[MarginCall, ...]]:
    """Compute IM shortfall/excess, VM call/credit, and deterministic MarginCall objects.

    Returns:
        (im_shortfall, im_excess, vm_call, vm_credit, total_call_due, margin_calls)
    """
    mc_pol = policy.margin_call_policy
    im_shortfall = policy.quantize(max(ZERO, net_im - eligible_collateral_value))
    im_excess = policy.quantize(max(ZERO, eligible_collateral_value - net_im))

    raw_vm_call = policy.quantize(max(ZERO, -variation_margin))
    raw_vm_credit = policy.quantize(max(ZERO, variation_margin))

    # Optional policy: offset VM call with IM excess only if VM is not strictly cash-only
    if mc_pol.allow_im_excess_offset_vm and not mc_pol.vm_cash_only and im_excess > ZERO:
        offset = min(im_excess, raw_vm_call)
        raw_vm_call = policy.quantize(raw_vm_call - offset)

    calls: List[MarginCall] = []

    if is_settlement_correction:
        # Settlement correction posts explicit incremental VM adjustment (debit or credit)
        if variation_margin != ZERO:
            direction = "CREDIT" if variation_margin > ZERO else "DEBIT"
            corr_amount = policy.quantize(abs(variation_margin))
            idem_key = make_idempotency_key(
                valuation_date,
                netting_set.netting_set_id,
                CallType.SETTLEMENT_CORRECTION,
                revision,
            )
            prev_vm_key = make_idempotency_key(
                valuation_date,
                netting_set.netting_set_id,
                CallType.VM_CALL if variation_margin > ZERO else CallType.VM_CREDIT,
                revision - 1,
            )
            calls.append(
                MarginCall(
                    call_id=make_call_id(idem_key),
                    idempotency_key=idem_key,
                    valuation_date=valuation_date,
                    netting_set_id=netting_set.netting_set_id,
                    account_id=netting_set.account_id,
                    call_type=CallType.SETTLEMENT_CORRECTION,
                    currency=netting_set.base_currency,
                    amount=corr_amount,
                    revision=revision,
                    reason=(
                        f"SETTLEMENT_CORRECTION_{direction} for rev {revision} "
                        f"(delta_vm={variation_margin})"
                    ),
                    reference_call_id=make_call_id(prev_vm_key),
                )
            )
        vm_call_final = raw_vm_call
        vm_credit_final = raw_vm_credit
        im_call_final = policy.quantize(ZERO)
    else:
        im_call_final = _apply_mta_and_rounding(
            im_shortfall,
            mc_pol.minimum_transfer_amount,
            mc_pol.rounding_increment,
            policy,
        )
        vm_call_final = _apply_mta_and_rounding(
            raw_vm_call,
            mc_pol.minimum_transfer_amount,
            mc_pol.rounding_increment,
            policy,
        )
        vm_credit_final = raw_vm_credit

        if im_call_final > ZERO:
            idem_key = make_idempotency_key(
                valuation_date,
                netting_set.netting_set_id,
                CallType.IM_CALL,
                revision,
            )
            calls.append(
                MarginCall(
                    call_id=make_call_id(idem_key),
                    idempotency_key=idem_key,
                    valuation_date=valuation_date,
                    netting_set_id=netting_set.netting_set_id,
                    account_id=netting_set.account_id,
                    call_type=CallType.IM_CALL,
                    currency=netting_set.base_currency,
                    amount=im_call_final,
                    revision=revision,
                    reason=(
                        f"IM shortfall {im_shortfall} (net_im={net_im}, "
                        f"eligible_collateral={eligible_collateral_value})"
                    ),
                )
            )

        if vm_call_final > ZERO:
            idem_key = make_idempotency_key(
                valuation_date,
                netting_set.netting_set_id,
                CallType.VM_CALL,
                revision,
            )
            calls.append(
                MarginCall(
                    call_id=make_call_id(idem_key),
                    idempotency_key=idem_key,
                    valuation_date=valuation_date,
                    netting_set_id=netting_set.netting_set_id,
                    account_id=netting_set.account_id,
                    call_type=CallType.VM_CALL,
                    currency=netting_set.base_currency,
                    amount=vm_call_final,
                    revision=revision,
                    reason=f"Daily VM loss transfer due (variation_margin={variation_margin})",
                )
            )

        if vm_credit_final > ZERO:
            idem_key = make_idempotency_key(
                valuation_date,
                netting_set.netting_set_id,
                CallType.VM_CREDIT,
                revision,
            )
            calls.append(
                MarginCall(
                    call_id=make_call_id(idem_key),
                    idempotency_key=idem_key,
                    valuation_date=valuation_date,
                    netting_set_id=netting_set.netting_set_id,
                    account_id=netting_set.account_id,
                    call_type=CallType.VM_CREDIT,
                    currency=netting_set.base_currency,
                    amount=vm_credit_final,
                    revision=revision,
                    reason=f"Daily VM gain credit payable (variation_margin={variation_margin})",
                )
            )

    total_call_due = policy.quantize(im_call_final + vm_call_final)
    return (
        im_shortfall,
        im_excess,
        vm_call_final,
        vm_credit_final,
        total_call_due,
        tuple(calls),
    )
