"""Canonical immutable domain models, Decimal quantization helpers, and serialization."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import date
from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_CEILING,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
)
from enum import Enum
import hashlib
import json
from typing import Any, Dict, List, Mapping, Optional, Tuple

from derivatives_margin_engine.errors import ValidationError

SUPPORTED_SCHEMA_VERSION = "1.0.0"

ROUNDING_MODES: Dict[str, str] = {
    "ROUND_HALF_UP": ROUND_HALF_UP,
    "ROUND_HALF_EVEN": ROUND_HALF_EVEN,
    "ROUND_CEILING": ROUND_CEILING,
}


def to_decimal(value: Any, field_name: str = "value") -> Decimal:
    """Strictly convert strings, ints, or Decimals to Decimal; reject floats to avoid binary drift."""
    if isinstance(value, Decimal):
        if value.is_nan() or value.is_infinite():
            raise ValidationError(
                "INVALID_DECIMAL",
                f"Field '{field_name}' must be a finite decimal, got {value}",
                {"field": field_name},
            )
        return value
    if isinstance(value, bool):
        raise ValidationError(
            "INVALID_DECIMAL",
            f"Field '{field_name}' cannot be boolean",
            {"field": field_name},
        )
    if isinstance(value, float):
        raise ValidationError(
            "FLOAT_NOT_ALLOWED",
            f"Field '{field_name}' must be passed as string, int, or Decimal (not float)",
            {"field": field_name},
        )
    if isinstance(value, (int, str)):
        try:
            d = Decimal(str(value).strip())
        except InvalidOperation as exc:
            raise ValidationError(
                "INVALID_DECIMAL",
                f"Field '{field_name}' is not a valid decimal: {value!r}",
                {"field": field_name, "value": repr(value)},
            ) from exc
        if d.is_nan() or d.is_infinite():
            raise ValidationError(
                "INVALID_DECIMAL",
                f"Field '{field_name}' must be finite",
                {"field": field_name},
            )
        return d
    raise ValidationError(
        "INVALID_DECIMAL",
        f"Unsupported type for decimal field '{field_name}': {type(value).__name__}",
        {"field": field_name},
    )


def parse_date(value: Any, field_name: str = "date") -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValidationError(
                "INVALID_DATE",
                f"Field '{field_name}' must be ISO YYYY-MM-DD, got {value!r}",
                {"field": field_name, "value": value},
            ) from exc
    raise ValidationError(
        "INVALID_DATE",
        f"Field '{field_name}' must be ISO date string or date object",
        {"field": field_name},
    )


def quantize_amount(
    value: Decimal,
    scale: int = 2,
    rounding_mode: str = "ROUND_HALF_UP",
) -> Decimal:
    if rounding_mode not in ROUNDING_MODES:
        raise ValidationError(
            "INVALID_CONFIG",
            f"Unsupported rounding mode '{rounding_mode}'",
            {"rounding_mode": rounding_mode},
        )
    quantum = Decimal(1).scaleb(-scale)
    return value.quantize(quantum, rounding=ROUNDING_MODES[rounding_mode])


def canonical_json(obj: Any) -> str:
    """Serialize domain objects deterministically for hashing and reporting."""

    def _default(item: Any) -> Any:
        if isinstance(item, Decimal):
            return str(item)
        if isinstance(item, date):
            return item.isoformat()
        if isinstance(item, Enum):
            return item.value
        if dataclasses.is_dataclass(item):
            return dataclasses.asdict(item)
        raise TypeError(f"Object of type {type(item).__name__} is not JSON serializable")

    return json.dumps(obj, default=_default, sort_keys=True, separators=(",", ":"))


def content_hash(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


class AggregationMethod(str, Enum):
    WORST_CASE = "WORST_CASE"
    HISTORICAL_VAR = "HISTORICAL_VAR"
    EXPECTED_SHORTFALL = "EXPECTED_SHORTFALL"


class ShockType(str, Enum):
    RELATIVE = "RELATIVE"
    ABSOLUTE = "ABSOLUTE"


class TargetType(str, Enum):
    INSTRUMENT = "INSTRUMENT"
    RISK_GROUP = "RISK_GROUP"


class EligibilityStatus(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE_ASSET_CLASS = "INELIGIBLE_ASSET_CLASS"
    EXPIRED = "EXPIRED"
    BELOW_MIN_RESIDUAL_MATURITY = "BELOW_MIN_RESIDUAL_MATURITY"
    CONCENTRATION_CAPPED = "CONCENTRATION_CAPPED"


class CallType(str, Enum):
    IM_CALL = "IM_CALL"
    VM_CALL = "VM_CALL"
    VM_CREDIT = "VM_CREDIT"
    SETTLEMENT_CORRECTION = "SETTLEMENT_CORRECTION"


@dataclass(frozen=True)
class HaircutRule:
    asset_class: str
    eligible: bool
    min_maturity_days: int
    max_maturity_days: Optional[int]
    haircut: Decimal


@dataclass(frozen=True)
class IMPolicy:
    aggregation_method: AggregationMethod = AggregationMethod.WORST_CASE
    confidence_level: Decimal = Decimal("0.99")
    default_intra_group_offset: Decimal = Decimal("1.00")
    default_cross_group_offset: Decimal = Decimal("0.50")
    max_cross_group_offset: Decimal = Decimal("0.80")
    group_intra_offsets: Tuple[Tuple[str, Decimal], ...] = ()
    pair_cross_group_offsets: Tuple[Tuple[str, Decimal], ...] = ()

    def intra_offset_for(self, risk_group: str) -> Decimal:
        lookup = dict(self.group_intra_offsets)
        return lookup.get(risk_group, self.default_intra_group_offset)

    def pair_offset_for(self, group_a: str, group_b: str) -> Optional[Decimal]:
        lookup = dict(self.pair_cross_group_offsets)
        key1 = f"{group_a}:{group_b}"
        key2 = f"{group_b}:{group_a}"
        if key1 in lookup:
            return lookup[key1]
        if key2 in lookup:
            return lookup[key2]
        return None


@dataclass(frozen=True)
class MarginCallPolicy:
    minimum_transfer_amount: Decimal = Decimal("0.00")
    rounding_increment: Decimal = Decimal("0.00")
    vm_cash_only: bool = True
    allow_im_excess_offset_vm: bool = False


@dataclass(frozen=True)
class CollateralPolicy:
    min_residual_maturity_days: int = 1
    fx_mismatch_haircut_addon: Decimal = Decimal("0.08")
    max_single_asset_class_concentration: Decimal = Decimal("1.00")
    haircut_schedule: Tuple[HaircutRule, ...] = ()


@dataclass(frozen=True)
class MarginPolicy:
    schema_version: str
    policy_id: str
    base_currency: str
    calendar: str
    valuation_time_utc: str
    decimal_scale: int
    rounding_mode: str
    supported_currencies: Tuple[str, ...]
    fx_rates_to_base: Tuple[Tuple[str, Decimal], ...]
    im_policy: IMPolicy
    margin_call_policy: MarginCallPolicy
    collateral_policy: CollateralPolicy

    def fx_rate(self, currency: str) -> Decimal:
        rates = dict(self.fx_rates_to_base)
        if currency not in rates:
            raise ValidationError(
                "UNSUPPORTED_CURRENCY",
                f"Currency '{currency}' has no FX rate in policy '{self.policy_id}'",
                {"currency": currency, "policy_id": self.policy_id},
            )
        return rates[currency]

    def convert_to_currency(self, amount: Decimal, from_ccy: str, to_ccy: str) -> Decimal:
        if from_ccy == to_ccy:
            return amount
        amount_in_base = amount * self.fx_rate(from_ccy)
        return amount_in_base / self.fx_rate(to_ccy)

    def quantize(self, amount: Decimal) -> Decimal:
        return quantize_amount(amount, scale=self.decimal_scale, rounding_mode=self.rounding_mode)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MarginPolicy":
        if not isinstance(data, Mapping):
            raise ValidationError("INVALID_CONFIG", "Policy payload must be a JSON object")
        required = [
            "schema_version",
            "policy_id",
            "base_currency",
            "calendar",
            "valuation_time_utc",
            "decimal_scale",
            "rounding_mode",
            "supported_currencies",
            "fx_rates_to_base",
            "im_policy",
            "margin_call_policy",
            "collateral_policy",
        ]
        missing = [k for k in required if k not in data]
        if missing:
            raise ValidationError(
                "MISSING_CONFIG_FIELD",
                f"Policy missing required fields: {missing}",
                {"missing": missing},
            )
        im_raw = data["im_policy"]
        try:
            agg = AggregationMethod(im_raw["aggregation_method"])
        except (KeyError, ValueError) as exc:
            raise ValidationError(
                "INVALID_CONFIG",
                f"Invalid IM aggregation_method: {im_raw.get('aggregation_method')!r}",
            ) from exc

        im_policy = IMPolicy(
            aggregation_method=agg,
            confidence_level=to_decimal(im_raw.get("confidence_level", "0.99"), "confidence_level"),
            default_intra_group_offset=to_decimal(
                im_raw.get("default_intra_group_offset", "1.00"),
                "default_intra_group_offset",
            ),
            default_cross_group_offset=to_decimal(
                im_raw.get("default_cross_group_offset", "0.50"),
                "default_cross_group_offset",
            ),
            max_cross_group_offset=to_decimal(
                im_raw.get("max_cross_group_offset", "0.80"),
                "max_cross_group_offset",
            ),
            group_intra_offsets=tuple(
                sorted(
                    (k, to_decimal(v, f"group_intra_offsets.{k}"))
                    for k, v in im_raw.get("group_intra_offsets", {}).items()
                )
            ),
            pair_cross_group_offsets=tuple(
                sorted(
                    (k, to_decimal(v, f"pair_cross_group_offsets.{k}"))
                    for k, v in im_raw.get("pair_cross_group_offsets", {}).items()
                )
            ),
        )

        mc_raw = data["margin_call_policy"]
        mc_policy = MarginCallPolicy(
            minimum_transfer_amount=to_decimal(
                mc_raw.get("minimum_transfer_amount", "0.00"),
                "minimum_transfer_amount",
            ),
            rounding_increment=to_decimal(
                mc_raw.get("rounding_increment", "0.00"),
                "rounding_increment",
            ),
            vm_cash_only=bool(mc_raw.get("vm_cash_only", True)),
            allow_im_excess_offset_vm=bool(mc_raw.get("allow_im_excess_offset_vm", False)),
        )

        col_raw = data["collateral_policy"]
        rules: List[HaircutRule] = []
        for idx, r in enumerate(col_raw.get("haircut_schedule", [])):
            rules.append(
                HaircutRule(
                    asset_class=str(r["asset_class"]),
                    eligible=bool(r["eligible"]),
                    min_maturity_days=int(r.get("min_maturity_days", 0)),
                    max_maturity_days=(
                        None if r.get("max_maturity_days") is None else int(r["max_maturity_days"])
                    ),
                    haircut=to_decimal(r["haircut"], f"haircut_schedule[{idx}].haircut"),
                )
            )
        col_policy = CollateralPolicy(
            min_residual_maturity_days=int(col_raw.get("min_residual_maturity_days", 1)),
            fx_mismatch_haircut_addon=to_decimal(
                col_raw.get("fx_mismatch_haircut_addon", "0.08"),
                "fx_mismatch_haircut_addon",
            ),
            max_single_asset_class_concentration=to_decimal(
                col_raw.get("max_single_asset_class_concentration", "1.00"),
                "max_single_asset_class_concentration",
            ),
            haircut_schedule=tuple(rules),
        )

        fx_rates = tuple(
            sorted(
                (str(k), to_decimal(v, f"fx_rates_to_base.{k}"))
                for k, v in data["fx_rates_to_base"].items()
            )
        )
        return cls(
            schema_version=str(data["schema_version"]),
            policy_id=str(data["policy_id"]),
            base_currency=str(data["base_currency"]),
            calendar=str(data["calendar"]),
            valuation_time_utc=str(data["valuation_time_utc"]),
            decimal_scale=int(data["decimal_scale"]),
            rounding_mode=str(data["rounding_mode"]),
            supported_currencies=tuple(str(c) for c in data["supported_currencies"]),
            fx_rates_to_base=fx_rates,
            im_policy=im_policy,
            margin_call_policy=mc_policy,
            collateral_policy=col_policy,
        )

    def with_haircut_overrides(self, overrides: Mapping[str, Any]) -> "MarginPolicy":
        """Return a new MarginPolicy with updated haircuts per asset class."""
        if not overrides:
            return self
        parsed = {k: to_decimal(v, f"haircut_overrides.{k}") for k, v in overrides.items()}
        updated_rules = tuple(
            dataclasses.replace(r, haircut=parsed[r.asset_class])
            if r.asset_class in parsed
            else r
            for r in self.collateral_policy.haircut_schedule
        )
        updated_col = dataclasses.replace(self.collateral_policy, haircut_schedule=updated_rules)
        return dataclasses.replace(self, collateral_policy=updated_col)


@dataclass(frozen=True)
class Shock:
    target_type: TargetType
    target_id: str
    shock_type: ShockType
    value: Decimal


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    description: str
    shocks: Tuple[Shock, ...]


@dataclass(frozen=True)
class ScenarioSet:
    schema_version: str
    scenario_set_id: str
    description: str
    scenarios: Tuple[Scenario, ...]
    seed: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ScenarioSet":
        if not isinstance(data, Mapping) or "scenarios" not in data:
            raise ValidationError("INVALID_SCENARIO", "ScenarioSet requires 'scenarios' list")
        scenarios: List[Scenario] = []
        for s_idx, s in enumerate(data["scenarios"]):
            shocks: List[Shock] = []
            for sh_idx, sh in enumerate(s.get("shocks", [])):
                try:
                    t_type = TargetType(sh["target_type"])
                    s_type = ShockType(sh["shock_type"])
                except (KeyError, ValueError) as exc:
                    raise ValidationError(
                        "INVALID_SCENARIO",
                        f"Invalid shock enum at scenario[{s_idx}].shocks[{sh_idx}]",
                    ) from exc
                shocks.append(
                    Shock(
                        target_type=t_type,
                        target_id=str(sh["target_id"]),
                        shock_type=s_type,
                        value=to_decimal(sh["value"], f"scenario[{s_idx}].shocks[{sh_idx}].value"),
                    )
                )
            scenarios.append(
                Scenario(
                    scenario_id=str(s["scenario_id"]),
                    description=str(s.get("description", "")),
                    shocks=tuple(shocks),
                )
            )
        return cls(
            schema_version=str(data.get("schema_version", SUPPORTED_SCHEMA_VERSION)),
            scenario_set_id=str(data.get("scenario_set_id", "CUSTOM")),
            description=str(data.get("description", "")),
            scenarios=tuple(scenarios),
            seed=data.get("seed"),
        )


@dataclass(frozen=True)
class NettingSet:
    netting_set_id: str
    account_id: str
    base_currency: str
    cross_group_offset_override: Optional[Decimal] = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NettingSet":
        override = data.get("cross_group_offset_override")
        return cls(
            netting_set_id=str(data["netting_set_id"]),
            account_id=str(data["account_id"]),
            base_currency=str(data["base_currency"]),
            cross_group_offset_override=(
                None if override is None else to_decimal(override, "cross_group_offset_override")
            ),
        )


@dataclass(frozen=True)
class Position:
    position_id: str
    netting_set_id: str
    instrument_id: str
    risk_group: str
    quantity: Decimal
    contract_multiplier: Decimal
    currency: str
    prior_settlement_price: Decimal
    settled_cash_adjustment: Decimal = Decimal("0.00")
    allow_negative_price: bool = False

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Position":
        return cls(
            position_id=str(data["position_id"]),
            netting_set_id=str(data["netting_set_id"]),
            instrument_id=str(data["instrument_id"]),
            risk_group=str(data["risk_group"]),
            quantity=to_decimal(data["quantity"], "quantity"),
            contract_multiplier=to_decimal(data["contract_multiplier"], "contract_multiplier"),
            currency=str(data["currency"]),
            prior_settlement_price=to_decimal(
                data["prior_settlement_price"], "prior_settlement_price"
            ),
            settled_cash_adjustment=to_decimal(
                data.get("settled_cash_adjustment", "0.00"), "settled_cash_adjustment"
            ),
            allow_negative_price=bool(data.get("allow_negative_price", False)),
        )


@dataclass(frozen=True)
class SettlementPrice:
    instrument_id: str
    valuation_date: date
    price: Decimal
    currency: str
    revision: int = 1

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SettlementPrice":
        return cls(
            instrument_id=str(data["instrument_id"]),
            valuation_date=parse_date(data["valuation_date"], "valuation_date"),
            price=to_decimal(data["price"], "price"),
            currency=str(data["currency"]),
            revision=int(data.get("revision", 1)),
        )


@dataclass(frozen=True)
class CollateralLot:
    lot_id: str
    netting_set_id: str
    asset_class: str
    security_id: str
    currency: str
    market_value: Decimal
    maturity_date: Optional[date] = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CollateralLot":
        mat = data.get("maturity_date")
        return cls(
            lot_id=str(data["lot_id"]),
            netting_set_id=str(data["netting_set_id"]),
            asset_class=str(data["asset_class"]),
            security_id=str(data["security_id"]),
            currency=str(data["currency"]),
            market_value=to_decimal(data["market_value"], "market_value"),
            maturity_date=None if mat is None else parse_date(mat, "maturity_date"),
        )


@dataclass(frozen=True)
class PositionValuation:
    position_id: str
    netting_set_id: str
    instrument_id: str
    risk_group: str
    quantity: Decimal
    contract_multiplier: Decimal
    currency: str
    base_currency: str
    prior_price: Decimal
    current_price: Decimal
    prior_mtm_base: Decimal
    current_mtm_base: Decimal
    daily_mtm_movement_base: Decimal
    settled_cash_adjustment_base: Decimal
    variation_margin_base: Decimal


@dataclass(frozen=True)
class IMResult:
    netting_set_id: str
    base_currency: str
    aggregation_method: AggregationMethod
    group_standalone_im: Tuple[Tuple[str, Decimal], ...]
    gross_im: Decimal
    joint_im: Decimal
    diversification_benefit: Decimal
    applied_cross_group_offset: Decimal
    offset_credit: Decimal
    net_im: Decimal
    worst_scenario_id: Optional[str]
    scenario_losses: Tuple[Tuple[str, Decimal], ...]


@dataclass(frozen=True)
class CollateralValuationResult:
    lot_id: str
    netting_set_id: str
    asset_class: str
    security_id: str
    currency: str
    base_currency: str
    market_value_native: Decimal
    market_value_base: Decimal
    residual_maturity_days: Optional[int]
    base_haircut: Decimal
    fx_haircut_addon: Decimal
    effective_haircut: Decimal
    post_haircut_value_base: Decimal
    status: EligibilityStatus
    reason: str


@dataclass(frozen=True)
class MarginCall:
    call_id: str
    idempotency_key: str
    valuation_date: date
    netting_set_id: str
    account_id: str
    call_type: CallType
    currency: str
    amount: Decimal
    revision: int
    reason: str
    reference_call_id: Optional[str] = None


@dataclass(frozen=True)
class NettingSetMarginResult:
    netting_set_id: str
    account_id: str
    base_currency: str
    valuation_date: date
    revision: int
    position_valuations: Tuple[PositionValuation, ...]
    total_mtm: Decimal
    daily_mtm_movement: Decimal
    total_settled_cash_adjustment: Decimal
    variation_margin: Decimal
    im_result: IMResult
    collateral_valuations: Tuple[CollateralValuationResult, ...]
    gross_collateral_value: Decimal
    eligible_collateral_value: Decimal
    im_shortfall: Decimal
    im_excess: Decimal
    vm_call: Decimal
    vm_credit: Decimal
    total_call_due: Decimal
    margin_calls: Tuple[MarginCall, ...]


@dataclass(frozen=True)
class JournalEntry:
    entry_id: str
    run_id: str
    valuation_date: date
    netting_set_id: str
    call_id: Optional[str]
    account: str
    asset_id: str
    currency: str
    debit: Decimal
    credit: Decimal
    entry_type: str
    reference: str


@dataclass(frozen=True)
class RunDiagnostics:
    positions_valued: int
    scenarios_evaluated: int
    collateral_lots_valued: int
    ineligible_collateral_lots: int
    margin_calls_issued: int
    duplicate_calls_blocked: int
    journal_entries_posted: int


@dataclass(frozen=True)
class RunResult:
    schema_version: str
    run_id: str
    valuation_date: date
    valuation_time_utc: str
    revision: int
    policy_id: str
    policy_hash: str
    scenario_set_id: str
    scenario_hash: str
    input_hash: str
    seed: Optional[int]
    netting_set_results: Tuple[NettingSetMarginResult, ...]
    diagnostics: RunDiagnostics
    is_settlement_correction: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return json.loads(canonical_json(self))
