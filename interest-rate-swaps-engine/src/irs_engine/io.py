"""Versioned JSON I/O, schema parsing, configuration loading, and replay bundle helpers."""

from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from irs_engine.domain.enums import (
    BusinessDayConvention,
    Compounding,
    CurveRole,
    DayCountConvention,
    FiniteDifferenceScheme,
    FixingFallbackPolicy,
    Frequency,
    InterpolationMethod,
    StubType,
    SwapDirection,
    ValuationDateFixingPolicy,
)
from irs_engine.domain.errors import ConfigurationError, CurveError, ValidationError
from irs_engine.domain.models import (
    CurveNode,
    CurveSnapshot,
    FixedLegSpec,
    FixingEntry,
    FixingHistory,
    FloatingLegSpec,
    SwapTrade,
    ValuationConfig,
)


def to_plain_dict(obj: Any) -> Any:
    """Recursively convert dataclasses, dates, and enums into JSON-safe Python primitives."""
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: to_plain_dict(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [to_plain_dict(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): to_plain_dict(v) for k, v in obj.items()}
    return obj


def _parse_enum(enum_cls: type[Enum], raw: Any, field_name: str) -> Any:
    try:
        return enum_cls(raw)
    except ValueError as exc:
        allowed = [e.value for e in enum_cls]
        raise ValidationError(
            f"Invalid value '{raw}' for '{field_name}'. Allowed values: {allowed}.",
            details={"field": field_name, "value": raw, "allowed": allowed},
        ) from exc


def _parse_date(raw: Any, field_name: str) -> date:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise ValidationError(
                f"Invalid ISO date '{raw}' for '{field_name}'. Expected YYYY-MM-DD.",
                details={"field": field_name, "value": raw},
            ) from exc
    raise ValidationError(
        f"Expected ISO date string for '{field_name}', got {type(raw).__name__}.",
        details={"field": field_name},
    )


def parse_trade(raw: dict[str, Any]) -> SwapTrade:
    """Parse and validate a SwapTrade dictionary."""
    try:
        fl = raw["fixed_leg"]
        flt = raw["floating_leg"]
    except KeyError as exc:
        raise ValidationError(
            f"Missing required trade field: {exc.args[0]}",
            details={"missing_field": exc.args[0]},
        ) from exc

    fixed_leg = FixedLegSpec(
        fixed_rate=float(fl["fixed_rate"]),
        frequency=_parse_enum(Frequency, fl["frequency"], "fixed_leg.frequency"),
        day_count=_parse_enum(DayCountConvention, fl["day_count"], "fixed_leg.day_count"),
        business_day_convention=_parse_enum(
            BusinessDayConvention,
            fl.get("business_day_convention", "MODIFIED_FOLLOWING"),
            "fixed_leg.business_day_convention",
        ),
        calendar=str(fl.get("calendar", "USD_NY")),
        stub_type=_parse_enum(StubType, fl.get("stub_type", "FRONT_SHORT"), "fixed_leg.stub_type"),
        end_of_month=bool(fl.get("end_of_month", False)),
        payment_lag_days=int(fl.get("payment_lag_days", 0)),
    )

    floating_leg = FloatingLegSpec(
        index_name=str(flt["index_name"]),
        frequency=_parse_enum(Frequency, flt["frequency"], "floating_leg.frequency"),
        day_count=_parse_enum(DayCountConvention, flt["day_count"], "floating_leg.day_count"),
        spread=float(flt.get("spread", 0.0)),
        business_day_convention=_parse_enum(
            BusinessDayConvention,
            flt.get("business_day_convention", "MODIFIED_FOLLOWING"),
            "floating_leg.business_day_convention",
        ),
        calendar=str(flt.get("calendar", "USD_NY")),
        stub_type=_parse_enum(StubType, flt.get("stub_type", "FRONT_SHORT"), "floating_leg.stub_type"),
        end_of_month=bool(flt.get("end_of_month", False)),
        fixing_lag_days=int(flt.get("fixing_lag_days", 2)),
        payment_lag_days=int(flt.get("payment_lag_days", 0)),
    )

    try:
        return SwapTrade(
            trade_id=str(raw["trade_id"]),
            currency=str(raw["currency"]),
            notional=float(raw["notional"]),
            direction=_parse_enum(SwapDirection, raw["direction"], "direction"),
            effective_date=_parse_date(raw["effective_date"], "effective_date"),
            maturity_date=_parse_date(raw["maturity_date"], "maturity_date"),
            fixed_leg=fixed_leg,
            floating_leg=floating_leg,
            schema_version=str(raw.get("schema_version", "1.0.0")),
        )
    except KeyError as exc:
        raise ValidationError(
            f"Missing required trade field: {exc.args[0]}",
            details={"missing_field": exc.args[0]},
        ) from exc


def parse_curve_snapshot(raw: dict[str, Any]) -> CurveSnapshot:
    """Parse and validate a CurveSnapshot dictionary."""
    try:
        raw_nodes = raw["nodes"]
    except KeyError as exc:
        raise CurveError("CurveSnapshot missing 'nodes' list.") from exc

    if not isinstance(raw_nodes, list):
        raise CurveError("CurveSnapshot 'nodes' must be a list.")

    nodes = tuple(
        CurveNode(
            tenor_years=float(n["tenor_years"]),
            zero_rate=float(n["zero_rate"]),
            label=str(n.get("label", f"{n['tenor_years']}Y")),
        )
        for n in raw_nodes
    )

    try:
        return CurveSnapshot(
            curve_id=str(raw["curve_id"]),
            role=_parse_enum(CurveRole, raw["role"], "curve.role"),
            currency=str(raw["currency"]),
            valuation_date=_parse_date(raw["valuation_date"], "curve.valuation_date"),
            nodes=nodes,
            day_count=_parse_enum(
                DayCountConvention, raw.get("day_count", "ACT/365F"), "curve.day_count"
            ),
            compounding=_parse_enum(
                Compounding, raw.get("compounding", "CONTINUOUS"), "curve.compounding"
            ),
            interpolation=_parse_enum(
                InterpolationMethod, raw.get("interpolation", "LINEAR_ZERO"), "curve.interpolation"
            ),
            index_name=raw.get("index_name"),
            schema_version=str(raw.get("schema_version", "1.0.0")),
        )
    except KeyError as exc:
        raise CurveError(
            f"Missing required curve field: {exc.args[0]}",
            details={"missing_field": exc.args[0]},
        ) from exc


def parse_fixing_history(raw: dict[str, Any] | list[dict[str, Any]] | None) -> FixingHistory:
    """Parse FixingHistory from dict or list of entry dicts."""
    if raw is None:
        return FixingHistory()
    if isinstance(raw, dict):
        entries_raw = raw.get("entries", [])
        schema_ver = str(raw.get("schema_version", "1.0.0"))
    else:
        entries_raw = raw
        schema_ver = "1.0.0"

    entries = tuple(
        FixingEntry(
            index_name=str(e["index_name"]),
            fixing_date=_parse_date(e["fixing_date"], "fixing.fixing_date"),
            rate=float(e["rate"]),
        )
        for e in entries_raw
    )
    return FixingHistory(entries=entries, schema_version=schema_ver)


def parse_valuation_config(raw: dict[str, Any] | None) -> ValuationConfig:
    """Parse ValuationConfig from a dictionary."""
    if not raw:
        return ValuationConfig()
    return ValuationConfig(
        default_bump_bps=float(raw.get("default_bump_bps", 1.0)),
        finite_difference_scheme=_parse_enum(
            FiniteDifferenceScheme,
            raw.get("finite_difference_scheme", "CENTRAL"),
            "finite_difference_scheme",
        ),
        missing_fixing_policy=_parse_enum(
            FixingFallbackPolicy,
            raw.get("missing_fixing_policy", "ERROR"),
            "missing_fixing_policy",
        ),
        valuation_date_fixing_policy=_parse_enum(
            ValuationDateFixingPolicy,
            raw.get("valuation_date_fixing_policy", "ALLOW_PROJECT"),
            "valuation_date_fixing_policy",
        ),
        par_swap_pv_tolerance=float(raw.get("par_swap_pv_tolerance", 1e-6)),
        symmetry_pv_tolerance=float(raw.get("symmetry_pv_tolerance", 1e-9)),
        kr01_reconciliation_rel_tolerance=float(raw.get("kr01_reconciliation_rel_tolerance", 0.005)),
        kr01_reconciliation_abs_tolerance=float(raw.get("kr01_reconciliation_abs_tolerance", 0.5)),
        oracle_pv_tolerance=float(raw.get("oracle_pv_tolerance", 1e-6)),
    )


def load_config_file(path: Path) -> ValuationConfig:
    """Load engine configuration JSON file; raises ConfigurationError if missing or invalid."""
    if not path.exists():
        raise ConfigurationError(
            f"Required configuration file not found: {path}",
            details={"path": str(path)},
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"Malformed JSON in configuration file {path}: {exc}",
            details={"path": str(path)},
        ) from exc
    return parse_valuation_config(raw)


def load_valuation_bundle(
    path: Path,
) -> tuple[SwapTrade, CurveSnapshot, CurveSnapshot, FixingHistory, ValuationConfig, dict[str, Any]]:
    """Load a complete valuation fixture/replay bundle from JSON."""
    if not path.exists():
        raise ConfigurationError(
            f"Valuation bundle file not found: {path}",
            details={"path": str(path)},
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    trade = parse_trade(raw["trade"])
    disc = parse_curve_snapshot(raw["discount_curve"])
    fwd = parse_curve_snapshot(raw["forward_curve"])
    fixings = parse_fixing_history(raw.get("fixing_history"))
    cfg = parse_valuation_config(raw.get("config"))
    meta = raw.get("metadata", {})
    return trade, disc, fwd, fixings, cfg, meta
