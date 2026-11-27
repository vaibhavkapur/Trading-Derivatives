"""Pure collateral eligibility, haircut valuation, expiry, and concentration logic."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

from derivatives_margin_engine.models import (
    CollateralLot,
    CollateralValuationResult,
    EligibilityStatus,
    HaircutRule,
    MarginPolicy,
    NettingSet,
)

ZERO = Decimal("0")
ONE = Decimal("1")


def _match_haircut_rule(
    asset_class: str,
    residual_days: Optional[int],
    rules: Sequence[HaircutRule],
) -> Optional[HaircutRule]:
    effective_days = 0 if residual_days is None else residual_days
    for rule in rules:
        if rule.asset_class != asset_class:
            continue
        if effective_days < rule.min_maturity_days:
            continue
        if rule.max_maturity_days is not None and effective_days > rule.max_maturity_days:
            continue
        return rule
    return None


def value_collateral_inventory(
    netting_set: NettingSet,
    lots: Sequence[CollateralLot],
    valuation_date: date,
    policy: MarginPolicy,
) -> Tuple[Tuple[CollateralValuationResult, ...], Decimal, Decimal, int]:
    """Evaluate collateral eligibility, expiry, haircuts, FX mismatch add-on, and concentration caps.

    Returns:
        (valuations, gross_collateral_value_base, eligible_collateral_value_base, ineligible_count)
    """
    cp = policy.collateral_policy
    base_ccy = netting_set.base_currency
    preliminary: List[CollateralValuationResult] = []
    gross_total = policy.quantize(ZERO)

    for lot in lots:
        mv_base = policy.quantize(
            policy.convert_to_currency(lot.market_value, lot.currency, base_ccy)
        )
        gross_total = policy.quantize(gross_total + mv_base)

        residual_days: Optional[int] = None
        if lot.maturity_date is not None:
            residual_days = (lot.maturity_date - valuation_date).days

        # 1. Expiry and minimum residual maturity check
        if residual_days is not None and residual_days <= 0:
            preliminary.append(
                CollateralValuationResult(
                    lot_id=lot.lot_id,
                    netting_set_id=lot.netting_set_id,
                    asset_class=lot.asset_class,
                    security_id=lot.security_id,
                    currency=lot.currency,
                    base_currency=base_ccy,
                    market_value_native=policy.quantize(lot.market_value),
                    market_value_base=mv_base,
                    residual_maturity_days=residual_days,
                    base_haircut=ONE,
                    fx_haircut_addon=ZERO,
                    effective_haircut=ONE,
                    post_haircut_value_base=policy.quantize(ZERO),
                    status=EligibilityStatus.EXPIRED,
                    reason=f"Collateral matured on {lot.maturity_date.isoformat()} (residual_days={residual_days})",
                )
            )
            continue

        if residual_days is not None and residual_days < cp.min_residual_maturity_days:
            preliminary.append(
                CollateralValuationResult(
                    lot_id=lot.lot_id,
                    netting_set_id=lot.netting_set_id,
                    asset_class=lot.asset_class,
                    security_id=lot.security_id,
                    currency=lot.currency,
                    base_currency=base_ccy,
                    market_value_native=policy.quantize(lot.market_value),
                    market_value_base=mv_base,
                    residual_maturity_days=residual_days,
                    base_haircut=ONE,
                    fx_haircut_addon=ZERO,
                    effective_haircut=ONE,
                    post_haircut_value_base=policy.quantize(ZERO),
                    status=EligibilityStatus.BELOW_MIN_RESIDUAL_MATURITY,
                    reason=(
                        f"Residual maturity {residual_days}d < policy minimum "
                        f"{cp.min_residual_maturity_days}d"
                    ),
                )
            )
            continue

        # 2. Match haircut schedule rule
        rule = _match_haircut_rule(lot.asset_class, residual_days, cp.haircut_schedule)
        if rule is None or not rule.eligible:
            preliminary.append(
                CollateralValuationResult(
                    lot_id=lot.lot_id,
                    netting_set_id=lot.netting_set_id,
                    asset_class=lot.asset_class,
                    security_id=lot.security_id,
                    currency=lot.currency,
                    base_currency=base_ccy,
                    market_value_native=policy.quantize(lot.market_value),
                    market_value_base=mv_base,
                    residual_maturity_days=residual_days,
                    base_haircut=ONE,
                    fx_haircut_addon=ZERO,
                    effective_haircut=ONE,
                    post_haircut_value_base=policy.quantize(ZERO),
                    status=EligibilityStatus.INELIGIBLE_ASSET_CLASS,
                    reason=f"Asset class '{lot.asset_class}' is not eligible under policy '{policy.policy_id}'",
                )
            )
            continue

        # 3. Apply base haircut + FX mismatch add-on (capped at 1.00)
        fx_addon = cp.fx_mismatch_haircut_addon if lot.currency != base_ccy else ZERO
        eff_haircut = min(ONE, rule.haircut + fx_addon)
        post_val = policy.quantize(mv_base * (ONE - eff_haircut))

        preliminary.append(
            CollateralValuationResult(
                lot_id=lot.lot_id,
                netting_set_id=lot.netting_set_id,
                asset_class=lot.asset_class,
                security_id=lot.security_id,
                currency=lot.currency,
                base_currency=base_ccy,
                market_value_native=policy.quantize(lot.market_value),
                market_value_base=mv_base,
                residual_maturity_days=residual_days,
                base_haircut=rule.haircut,
                fx_haircut_addon=fx_addon,
                effective_haircut=eff_haircut,
                post_haircut_value_base=post_val,
                status=EligibilityStatus.ELIGIBLE,
                reason="Eligible under haircut schedule",
            )
        )

    # 4. Optional single asset-class concentration cap
    pre_cap_eligible = sum(
        (v.post_haircut_value_base for v in preliminary if v.status == EligibilityStatus.ELIGIBLE),
        ZERO,
    )
    cap_ratio = cp.max_single_asset_class_concentration
    final_results: List[CollateralValuationResult] = []

    if cap_ratio < ONE and pre_cap_eligible > ZERO:
        class_totals: Dict[str, Decimal] = {}
        for v in preliminary:
            if v.status == EligibilityStatus.ELIGIBLE:
                class_totals[v.asset_class] = class_totals.get(v.asset_class, ZERO) + v.post_haircut_value_base
        max_per_class = policy.quantize(pre_cap_eligible * cap_ratio)
        class_remaining: Dict[str, Decimal] = {k: max_per_class for k in class_totals}
        for v in preliminary:
            if v.status != EligibilityStatus.ELIGIBLE:
                final_results.append(v)
                continue
            rem = class_remaining[v.asset_class]
            if v.post_haircut_value_base <= rem:
                class_remaining[v.asset_class] = rem - v.post_haircut_value_base
                final_results.append(v)
            else:
                capped_val = policy.quantize(max(ZERO, rem))
                class_remaining[v.asset_class] = ZERO
                final_results.append(
                    CollateralValuationResult(
                        lot_id=v.lot_id,
                        netting_set_id=v.netting_set_id,
                        asset_class=v.asset_class,
                        security_id=v.security_id,
                        currency=v.currency,
                        base_currency=v.base_currency,
                        market_value_native=v.market_value_native,
                        market_value_base=v.market_value_base,
                        residual_maturity_days=v.residual_maturity_days,
                        base_haircut=v.base_haircut,
                        fx_haircut_addon=v.fx_haircut_addon,
                        effective_haircut=v.effective_haircut,
                        post_haircut_value_base=capped_val,
                        status=EligibilityStatus.CONCENTRATION_CAPPED,
                        reason=(
                            f"Asset class '{v.asset_class}' capped at {cap_ratio * Decimal('100')}% "
                            f"concentration limit"
                        ),
                    )
                )
    else:
        final_results = preliminary

    eligible_total = policy.quantize(sum((v.post_haircut_value_base for v in final_results), ZERO))
    ineligible_count = sum(
        1
        for v in final_results
        if v.status
        in (
            EligibilityStatus.INELIGIBLE_ASSET_CLASS,
            EligibilityStatus.EXPIRED,
            EligibilityStatus.BELOW_MIN_RESIDUAL_MATURITY,
        )
    )
    return tuple(final_results), gross_total, eligible_total, ineligible_count
