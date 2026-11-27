"""I/O boundary for loading versioned JSON configs/fixtures and writing reports."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from derivatives_margin_engine.errors import ValidationError
from derivatives_margin_engine.models import (
    CollateralLot,
    MarginPolicy,
    NettingSet,
    Position,
    ScenarioSet,
    SettlementPrice,
    canonical_json,
)


def load_json_file(path: Path, kind: str = "input") -> Dict[str, Any]:
    if not path.exists():
        code = "MISSING_CONFIG_FILE" if kind == "config" else "MISSING_INPUT_FILE"
        raise ValidationError(
            code,
            f"Required {kind} file not found: {path}",
            {"path": str(path), "kind": kind},
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(
            "INVALID_JSON",
            f"Malformed JSON in {kind} file '{path}': {exc}",
            {"path": str(path)},
        ) from exc
    if not isinstance(raw, dict):
        raise ValidationError(
            "INVALID_JSON",
            f"Expected JSON object at root of {kind} file '{path}'",
            {"path": str(path)},
        )
    return raw


def load_policy(path: Path) -> MarginPolicy:
    return MarginPolicy.from_dict(load_json_file(path, kind="config"))


def load_scenario_set(path: Path) -> ScenarioSet:
    return ScenarioSet.from_dict(load_json_file(path, kind="config"))


def parse_snapshot_payload(
    payload: Mapping[str, Any],
) -> Tuple[
    date,
    str,
    int,
    bool,
    List[NettingSet],
    List[Position],
    List[SettlementPrice],
    List[CollateralLot],
]:
    required = [
        "valuation_date",
        "netting_sets",
        "positions",
        "settlement_prices",
        "collateral_inventory",
    ]
    missing = [k for k in required if k not in payload]
    if missing:
        raise ValidationError(
            "MISSING_INPUT_FIELD",
            f"Snapshot payload missing required fields: {missing}",
            {"missing": missing},
        )
    try:
        val_date = date.fromisoformat(str(payload["valuation_date"]))
    except ValueError as exc:
        raise ValidationError(
            "INVALID_DATE", f"Invalid valuation_date: {payload['valuation_date']!r}"
        ) from exc
    val_time = str(payload.get("valuation_time_utc", "21:00:00Z"))
    revision = int(payload.get("revision", 1))
    is_corr = bool(payload.get("is_settlement_correction", False))
    netting_sets = [NettingSet.from_dict(ns) for ns in payload["netting_sets"]]
    positions = [Position.from_dict(p) for p in payload["positions"]]
    prices = [SettlementPrice.from_dict(s) for s in payload["settlement_prices"]]
    collateral = [CollateralLot.from_dict(c) for c in payload["collateral_inventory"]]
    return val_date, val_time, revision, is_corr, netting_sets, positions, prices, collateral


def write_json_report(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Use canonical_json first to guarantee Decimal/date/Enum compatibility, then pretty-print
    normalized = json.loads(canonical_json(data))
    path.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8")
