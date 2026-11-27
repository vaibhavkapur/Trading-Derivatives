"""Input and configuration validation enforcing explicit data contracts."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Dict, Sequence, Set

from derivatives_margin_engine.errors import DuplicateCollateralError, ValidationError
from derivatives_margin_engine.models import (
    CollateralLot,
    MarginPolicy,
    NettingSet,
    Position,
    ROUNDING_MODES,
    SUPPORTED_SCHEMA_VERSION,
    ScenarioSet,
    SettlementPrice,
)

ZERO = Decimal("0")
ONE = Decimal("1")


def validate_policy(policy: MarginPolicy) -> None:
    if policy.schema_version != SUPPORTED_SCHEMA_VERSION:
        raise ValidationError(
            "SCHEMA_VERSION_UNSUPPORTED",
            f"Unsupported policy schema_version '{policy.schema_version}', expected '{SUPPORTED_SCHEMA_VERSION}'",
            {"schema_version": policy.schema_version},
        )
    if policy.decimal_scale < 0 or policy.decimal_scale > 8:
        raise ValidationError(
            "INVALID_CONFIG",
            f"decimal_scale must be between 0 and 8, got {policy.decimal_scale}",
        )
    if policy.rounding_mode not in ROUNDING_MODES:
        raise ValidationError(
            "INVALID_CONFIG",
            f"Unsupported rounding_mode '{policy.rounding_mode}'",
        )
    if policy.base_currency not in policy.supported_currencies:
        raise ValidationError(
            "INVALID_CONFIG",
            f"base_currency '{policy.base_currency}' must be in supported_currencies",
        )
    rates = dict(policy.fx_rates_to_base)
    for ccy in policy.supported_currencies:
        if ccy not in rates or rates[ccy] <= ZERO:
            raise ValidationError(
                "INVALID_CONFIG",
                f"Supported currency '{ccy}' must have a strictly positive FX rate",
            )
    im = policy.im_policy
    if not (ZERO < im.confidence_level <= ONE):
        raise ValidationError(
            "INVALID_CONFIG",
            f"IM confidence_level must be in (0, 1], got {im.confidence_level}",
        )
    for name, val in [
        ("default_intra_group_offset", im.default_intra_group_offset),
        ("default_cross_group_offset", im.default_cross_group_offset),
        ("max_cross_group_offset", im.max_cross_group_offset),
    ]:
        if not (ZERO <= val <= ONE):
            raise ValidationError("INVALID_CONFIG", f"{name} must be in [0, 1], got {val}")
    for grp, val in im.group_intra_offsets:
        if not (ZERO <= val <= ONE):
            raise ValidationError(
                "INVALID_CONFIG", f"group_intra_offsets['{grp}'] must be in [0, 1], got {val}"
            )
    for pair, val in im.pair_cross_group_offsets:
        if not (ZERO <= val <= ONE):
            raise ValidationError(
                "INVALID_CONFIG", f"pair_cross_group_offsets['{pair}'] must be in [0, 1], got {val}"
            )
    mc = policy.margin_call_policy
    if mc.minimum_transfer_amount < ZERO:
        raise ValidationError("INVALID_CONFIG", "minimum_transfer_amount cannot be negative")
    if mc.rounding_increment < ZERO:
        raise ValidationError("INVALID_CONFIG", "rounding_increment cannot be negative")

    cp = policy.collateral_policy
    if cp.min_residual_maturity_days < 0:
        raise ValidationError("INVALID_CONFIG", "min_residual_maturity_days cannot be negative")
    if not (ZERO <= cp.fx_mismatch_haircut_addon <= ONE):
        raise ValidationError("INVALID_CONFIG", "fx_mismatch_haircut_addon must be in [0, 1]")
    if not (ZERO < cp.max_single_asset_class_concentration <= ONE):
        raise ValidationError(
            "INVALID_CONFIG", "max_single_asset_class_concentration must be in (0, 1]"
        )
    for rule in cp.haircut_schedule:
        if not (ZERO <= rule.haircut <= ONE):
            raise ValidationError(
                "INVALID_HAIRCUT",
                f"Haircut for '{rule.asset_class}' must be in [0, 1], got {rule.haircut}",
                {"asset_class": rule.asset_class, "haircut": str(rule.haircut)},
            )
        if rule.min_maturity_days < 0:
            raise ValidationError("INVALID_CONFIG", "Rule min_maturity_days cannot be negative")
        if rule.max_maturity_days is not None and rule.max_maturity_days < rule.min_maturity_days:
            raise ValidationError(
                "INVALID_CONFIG",
                f"Rule max_maturity_days < min_maturity_days for {rule.asset_class}",
            )


def validate_scenario_set(scenario_set: ScenarioSet) -> None:
    if scenario_set.schema_version != SUPPORTED_SCHEMA_VERSION:
        raise ValidationError(
            "SCHEMA_VERSION_UNSUPPORTED",
            f"Unsupported scenario schema_version '{scenario_set.schema_version}'",
        )
    if not scenario_set.scenarios:
        raise ValidationError("INVALID_SCENARIO", "ScenarioSet must contain at least one scenario")
    seen_ids: Set[str] = set()
    for scn in scenario_set.scenarios:
        if not scn.scenario_id:
            raise ValidationError("INVALID_SCENARIO", "Scenario must have a non-empty scenario_id")
        if scn.scenario_id in seen_ids:
            raise ValidationError(
                "DUPLICATE_SCENARIO_ID",
                f"Duplicate scenario_id '{scn.scenario_id}'",
            )
        seen_ids.add(scn.scenario_id)


def validate_inputs(
    valuation_date: date,
    revision: int,
    policy: MarginPolicy,
    scenario_set: ScenarioSet,
    netting_sets: Sequence[NettingSet],
    positions: Sequence[Position],
    settlement_prices: Sequence[SettlementPrice],
    collateral_inventory: Sequence[CollateralLot],
) -> Dict[str, SettlementPrice]:
    """Validate all inputs and return instrument_id -> SettlementPrice map."""
    validate_policy(policy)
    validate_scenario_set(scenario_set)

    if revision < 1:
        raise ValidationError("INVALID_REVISION", f"revision must be >= 1, got {revision}")
    if not netting_sets:
        raise ValidationError("INVALID_INPUT", "At least one netting set is required")

    supported_ccy = set(policy.supported_currencies)
    ns_map: Dict[str, NettingSet] = {}
    for ns in netting_sets:
        if ns.netting_set_id in ns_map:
            raise ValidationError(
                "DUPLICATE_NETTING_SET",
                f"Duplicate netting_set_id '{ns.netting_set_id}'",
            )
        if ns.base_currency not in supported_ccy:
            raise ValidationError(
                "UNSUPPORTED_CURRENCY",
                f"Netting set '{ns.netting_set_id}' base currency '{ns.base_currency}' is unsupported",
                {"currency": ns.base_currency},
            )
        if ns.cross_group_offset_override is not None:
            if not (ZERO <= ns.cross_group_offset_override <= ONE):
                raise ValidationError(
                    "INVALID_INPUT",
                    f"cross_group_offset_override must be in [0, 1] for '{ns.netting_set_id}'",
                )
        ns_map[ns.netting_set_id] = ns

    price_map: Dict[str, SettlementPrice] = {}
    for sp in settlement_prices:
        if sp.valuation_date != valuation_date:
            raise ValidationError(
                "PRICE_DATE_MISMATCH",
                f"Settlement price for '{sp.instrument_id}' has date {sp.valuation_date}, expected {valuation_date}",
                {"instrument_id": sp.instrument_id},
            )
        if sp.currency not in supported_ccy:
            raise ValidationError(
                "UNSUPPORTED_CURRENCY",
                f"Settlement price currency '{sp.currency}' is unsupported",
                {"currency": sp.currency},
            )
        if sp.revision < 1:
            raise ValidationError(
                "INVALID_PRICE",
                f"Settlement price revision must be >= 1 for '{sp.instrument_id}'",
            )
        if sp.instrument_id in price_map:
            raise ValidationError(
                "DUPLICATE_SETTLEMENT_PRICE",
                f"Duplicate settlement price for instrument '{sp.instrument_id}'",
            )
        price_map[sp.instrument_id] = sp

    seen_positions: Set[str] = set()
    for pos in positions:
        if pos.position_id in seen_positions:
            raise ValidationError(
                "DUPLICATE_POSITION",
                f"Duplicate position_id '{pos.position_id}'",
            )
        seen_positions.add(pos.position_id)
        if pos.netting_set_id not in ns_map:
            raise ValidationError(
                "UNKNOWN_NETTING_SET",
                f"Position '{pos.position_id}' references unknown netting_set_id '{pos.netting_set_id}'",
            )
        if pos.contract_multiplier <= ZERO:
            raise ValidationError(
                "INVALID_POSITION",
                f"Position '{pos.position_id}' contract_multiplier must be > 0, got {pos.contract_multiplier}",
                {"position_id": pos.position_id},
            )
        if pos.currency not in supported_ccy:
            raise ValidationError(
                "UNSUPPORTED_CURRENCY",
                f"Position '{pos.position_id}' currency '{pos.currency}' is unsupported",
                {"currency": pos.currency},
            )
        if pos.instrument_id not in price_map:
            raise ValidationError(
                "MISSING_SETTLEMENT_PRICE",
                f"No settlement price provided for instrument '{pos.instrument_id}' on {valuation_date}",
                {"instrument_id": pos.instrument_id, "valuation_date": valuation_date.isoformat()},
            )
        sp = price_map[pos.instrument_id]
        if sp.currency != pos.currency:
            raise ValidationError(
                "CURRENCY_MISMATCH",
                f"Position '{pos.position_id}' currency '{pos.currency}' != settlement price currency '{sp.currency}'",
            )
        if not pos.allow_negative_price and (pos.prior_settlement_price < ZERO or sp.price < ZERO):
            raise ValidationError(
                "INVALID_PRICE",
                f"Negative price not allowed for position '{pos.position_id}' ({pos.instrument_id})",
            )

    # Enforce NO DOUBLE-COUNTED COLLATERAL across the entire inventory.
    lot_allocations: Dict[str, str] = {}
    for lot in collateral_inventory:
        if lot.lot_id in lot_allocations:
            raise DuplicateCollateralError(
                lot.lot_id,
                [lot_allocations[lot.lot_id], lot.netting_set_id],
            )
        lot_allocations[lot.lot_id] = lot.netting_set_id
        if lot.netting_set_id not in ns_map:
            raise ValidationError(
                "UNKNOWN_NETTING_SET",
                f"Collateral lot '{lot.lot_id}' references unknown netting_set_id '{lot.netting_set_id}'",
            )
        if lot.currency not in supported_ccy:
            raise ValidationError(
                "UNSUPPORTED_CURRENCY",
                f"Collateral lot '{lot.lot_id}' currency '{lot.currency}' is unsupported",
                {"currency": lot.currency},
            )
        if lot.market_value < ZERO:
            raise ValidationError(
                "INVALID_COLLATERAL",
                f"Collateral lot '{lot.lot_id}' market_value cannot be negative: {lot.market_value}",
            )

    return price_map
