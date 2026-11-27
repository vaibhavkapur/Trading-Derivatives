"""Pure scenario-based Initial Margin (IM) engine with configurable offsets."""

from __future__ import annotations

from decimal import Decimal
import math
import random
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from derivatives_margin_engine.models import (
    AggregationMethod,
    IMResult,
    MarginPolicy,
    NettingSet,
    Position,
    SUPPORTED_SCHEMA_VERSION,
    Scenario,
    ScenarioSet,
    SettlementPrice,
    Shock,
    ShockType,
    TargetType,
)

ZERO = Decimal("0")


def _shocked_price(
    base_price: Decimal,
    shocks: Sequence[Shock],
    instrument_id: str,
    risk_group: str,
    allow_negative_price: bool,
) -> Decimal:
    """Apply matching scenario shocks; instrument-specific shocks override risk-group shocks."""
    inst_shocks = [
        s for s in shocks if s.target_type == TargetType.INSTRUMENT and s.target_id == instrument_id
    ]
    applicable = (
        inst_shocks
        if inst_shocks
        else [
            s
            for s in shocks
            if s.target_type == TargetType.RISK_GROUP and s.target_id == risk_group
        ]
    )
    price = base_price
    for s in applicable:
        if s.shock_type == ShockType.RELATIVE:
            price = price * (Decimal("1") + s.value)
        elif s.shock_type == ShockType.ABSOLUTE:
            price = price + s.value
    if not allow_negative_price and price < ZERO:
        return ZERO
    return price


def _aggregate_losses(
    losses: Sequence[Decimal],
    method: AggregationMethod,
    confidence_level: Decimal,
) -> Decimal:
    """Aggregate non-negative scenario losses under WORST_CASE, HISTORICAL_VAR, or EXPECTED_SHORTFALL."""
    if not losses:
        return ZERO
    ordered = sorted(losses)  # ascending
    n = len(ordered)
    if method == AggregationMethod.WORST_CASE:
        return ordered[-1]

    # Conservative upper order statistic index: ceil(alpha * N) - 1
    rank = max(1, min(n, math.ceil(float(confidence_level) * n)))
    var_idx = rank - 1
    if method == AggregationMethod.HISTORICAL_VAR:
        return ordered[var_idx]

    # EXPECTED_SHORTFALL: average of losses in tail [var_idx .. n-1]
    tail = ordered[var_idx:]
    return sum(tail, ZERO) / Decimal(len(tail))


def _resolve_cross_group_offset(
    netting_set: NettingSet,
    groups: Sequence[str],
    policy: MarginPolicy,
) -> Decimal:
    im_pol = policy.im_policy
    if len(groups) <= 1:
        return ZERO
    if netting_set.cross_group_offset_override is not None:
        raw = netting_set.cross_group_offset_override
    else:
        pair_values: List[Decimal] = []
        sorted_groups = sorted(groups)
        for i in range(len(sorted_groups)):
            for j in range(i + 1, len(sorted_groups)):
                pv = im_pol.pair_offset_for(sorted_groups[i], sorted_groups[j])
                if pv is not None:
                    pair_values.append(pv)
        if pair_values:
            raw = sum(pair_values, ZERO) / Decimal(len(pair_values))
        else:
            raw = im_pol.default_cross_group_offset
    return min(raw, im_pol.max_cross_group_offset)


def compute_initial_margin(
    netting_set: NettingSet,
    positions: Sequence[Position],
    price_map: Mapping[str, SettlementPrice],
    scenario_set: ScenarioSet,
    policy: MarginPolicy,
) -> IMResult:
    """Compute scenario-based IM with configurable intra-group and cross-group offsets."""
    im_pol = policy.im_policy
    base_ccy = netting_set.base_currency

    if not positions:
        return IMResult(
            netting_set_id=netting_set.netting_set_id,
            base_currency=base_ccy,
            aggregation_method=im_pol.aggregation_method,
            group_standalone_im=(),
            gross_im=policy.quantize(ZERO),
            joint_im=policy.quantize(ZERO),
            diversification_benefit=policy.quantize(ZERO),
            applied_cross_group_offset=ZERO,
            offset_credit=policy.quantize(ZERO),
            net_im=policy.quantize(ZERO),
            worst_scenario_id=None,
            scenario_losses=tuple((s.scenario_id, policy.quantize(ZERO)) for s in scenario_set.scenarios),
        )

    groups_sorted = sorted({p.risk_group for p in positions})
    group_scenario_losses: Dict[str, List[Decimal]] = {g: [] for g in groups_sorted}
    joint_scenario_losses: List[Tuple[str, Decimal]] = []

    for scn in scenario_set.scenarios:
        per_group_gross_loss: Dict[str, Decimal] = {g: ZERO for g in groups_sorted}
        per_group_gross_gain: Dict[str, Decimal] = {g: ZERO for g in groups_sorted}
        joint_pnl_exact = ZERO

        for pos in positions:
            sp = price_map[pos.instrument_id]
            shocked = _shocked_price(
                base_price=sp.price,
                shocks=scn.shocks,
                instrument_id=pos.instrument_id,
                risk_group=pos.risk_group,
                allow_negative_price=pos.allow_negative_price,
            )
            pnl_native = pos.quantity * pos.contract_multiplier * (shocked - sp.price)
            pnl_base = policy.convert_to_currency(pnl_native, pos.currency, base_ccy)
            joint_pnl_exact += pnl_base

            if pnl_base < ZERO:
                per_group_gross_loss[pos.risk_group] += -pnl_base
            elif pnl_base > ZERO:
                per_group_gross_gain[pos.risk_group] += pnl_base

        # Intra-group loss with configurable offset alpha_g in [0, 1]
        for g in groups_sorted:
            alpha_g = im_pol.intra_offset_for(g)
            g_loss = per_group_gross_loss[g]
            g_gain = per_group_gross_gain[g]
            eff_loss = g_loss - alpha_g * min(g_loss, g_gain)
            group_scenario_losses[g].append(max(ZERO, eff_loss))

        # Joint scenario loss (respects intra-group offsets if < 1 by netting net group PnLs when alpha=1,
        # or summing effective signed group PnLs conservatively)
        if all(im_pol.intra_offset_for(g) == Decimal("1") for g in groups_sorted):
            joint_loss = max(ZERO, -joint_pnl_exact)
        else:
            # Conservative: net group gains only up to remaining gains after intra-group offset
            total_eff_loss = ZERO
            total_residual_gain = ZERO
            for g in groups_sorted:
                alpha_g = im_pol.intra_offset_for(g)
                g_loss = per_group_gross_loss[g]
                g_gain = per_group_gross_gain[g]
                matched = min(g_loss, g_gain)
                total_eff_loss += g_loss - alpha_g * matched
                total_residual_gain += max(ZERO, g_gain - g_loss)
            joint_loss = max(ZERO, total_eff_loss - total_residual_gain)

        joint_scenario_losses.append((scn.scenario_id, policy.quantize(joint_loss)))

    group_standalone_im: List[Tuple[str, Decimal]] = []
    gross_im_raw = ZERO
    for g in groups_sorted:
        g_im = policy.quantize(
            _aggregate_losses(
                group_scenario_losses[g],
                im_pol.aggregation_method,
                im_pol.confidence_level,
            )
        )
        group_standalone_im.append((g, g_im))
        gross_im_raw += g_im

    gross_im = policy.quantize(gross_im_raw)
    joint_im = policy.quantize(
        _aggregate_losses(
            [loss for _, loss in joint_scenario_losses],
            im_pol.aggregation_method,
            im_pol.confidence_level,
        )
    )
    diversification_benefit = policy.quantize(max(ZERO, gross_im - joint_im))
    applied_offset = _resolve_cross_group_offset(netting_set, groups_sorted, policy)
    offset_credit = policy.quantize(applied_offset * diversification_benefit)
    net_im = policy.quantize(max(ZERO, gross_im - offset_credit))

    worst_scn_id: Optional[str] = None
    if joint_scenario_losses:
        worst_scn_id = max(joint_scenario_losses, key=lambda item: (item[1], item[0]))[0]

    return IMResult(
        netting_set_id=netting_set.netting_set_id,
        base_currency=base_ccy,
        aggregation_method=im_pol.aggregation_method,
        group_standalone_im=tuple(group_standalone_im),
        gross_im=gross_im,
        joint_im=joint_im,
        diversification_benefit=diversification_benefit,
        applied_cross_group_offset=applied_offset,
        offset_credit=offset_credit,
        net_im=net_im,
        worst_scenario_id=worst_scn_id,
        scenario_losses=tuple(joint_scenario_losses),
    )


def generate_seeded_scenarios(
    risk_groups: Sequence[str],
    seed: int,
    num_scenarios: int = 250,
    horizon_days: int = 5,
    annual_vols: Optional[Mapping[str, float]] = None,
    scenario_set_id: Optional[str] = None,
) -> ScenarioSet:
    """Deterministically generate seeded stochastic relative shock scenarios for reproducibility tests.

    Uses Python's standard Mersenne Twister (`random.Random(seed)`) with `gauss(0, 1)`
    scaled by `vol * sqrt(horizon_days / 252)` and rounded to 6 decimal places.
    """
    if num_scenarios < 1:
        raise ValueError("num_scenarios must be >= 1")
    rng = random.Random(seed)
    vols = dict(annual_vols or {})
    dt_scale = math.sqrt(horizon_days / 252.0)
    sorted_groups = sorted(set(risk_groups))

    scenarios: List[Scenario] = []
    for idx in range(1, num_scenarios + 1):
        shocks: List[Shock] = []
        for grp in sorted_groups:
            vol = vols.get(grp, 0.20)
            z = rng.gauss(0.0, 1.0)
            # Clip extreme shocks at -0.95 to avoid invalid prices
            rel_shock = max(-0.95, z * vol * dt_scale)
            shocks.append(
                Shock(
                    target_type=TargetType.RISK_GROUP,
                    target_id=grp,
                    shock_type=ShockType.RELATIVE,
                    value=Decimal(f"{rel_shock:.6f}"),
                )
            )
        scenarios.append(
            Scenario(
                scenario_id=f"MC_{seed}_{idx:04d}",
                description=f"Seeded stochastic scenario #{idx} (seed={seed}, horizon={horizon_days}d)",
                shocks=tuple(shocks),
            )
        )
    return ScenarioSet(
        schema_version=SUPPORTED_SCHEMA_VERSION,
        scenario_set_id=scenario_set_id or f"SEEDED-MC-{seed}-{num_scenarios}",
        description=f"Deterministic seeded stochastic scenarios (seed={seed}, N={num_scenarios})",
        scenarios=tuple(scenarios),
        seed=seed,
    )
