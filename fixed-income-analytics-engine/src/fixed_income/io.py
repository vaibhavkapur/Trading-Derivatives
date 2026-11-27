"""I/O boundary for loading versioned configs/fixtures and writing machine-readable reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .errors import ConfigurationError, ValidationError
from .models import (
    BondQuote,
    BondSpec,
    DepositQuote,
    ScenarioSpec,
    ValuationConfig,
)


def load_json_file(path: Path, *, label: str = "file") -> Dict[str, Any]:
    """Load and parse a JSON object from disk with explicit ConfigurationError reporting."""
    if not path.exists():
        raise ConfigurationError(
            f"Missing required {label}: {path.name}",
            details={"path": str(path), "label": label},
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"Malformed JSON in {label} ({path.name}): {exc.msg}",
            details={"path": str(path), "line": exc.lineno, "col": exc.colno},
        ) from exc
    if not isinstance(raw, dict):
        raise ConfigurationError(
            f"Top-level JSON in {label} ({path.name}) must be an object",
            details={"path": str(path), "actual_type": type(raw).__name__},
        )
    return raw


def load_valuation_config(path: Path) -> ValuationConfig:
    """Load and validate a ValuationConfig JSON file."""
    data = load_json_file(path, label="valuation config")
    if "valuation_date" not in data:
        raise ConfigurationError(
            "Valuation config is missing required key 'valuation_date'",
            details={"path": str(path)},
        )
    try:
        return ValuationConfig.from_dict(data)
    except ValidationError as exc:
        raise ConfigurationError(
            f"Invalid valuation config: {exc.message}",
            details={"path": str(path), "validation": exc.details},
        ) from exc


def load_market_fixture(
    path: Path,
) -> Tuple[ValuationConfig, List[DepositQuote], List[BondQuote], List[BondSpec], List[ScenarioSpec]]:
    """Load a complete market fixture (config, calibration instruments, target bonds, scenarios)."""
    data = load_json_file(path, label="market fixture")

    if "config" not in data:
        raise ValidationError("Market fixture missing required 'config' object")
    config = ValuationConfig.from_dict(data["config"])

    deposits = [DepositQuote.from_dict(d) for d in data.get("deposits", [])]
    bond_quotes = [BondQuote.from_dict(bq) for bq in data.get("calibration_bonds", [])]
    target_bonds = [BondSpec.from_dict(b) for b in data.get("portfolio_bonds", [])]

    scenarios: List[ScenarioSpec] = []
    for raw_s in data.get("scenarios", []):
        scenarios.append(
            ScenarioSpec(
                scenario_id=str(raw_s["scenario_id"]),
                name=str(raw_s["name"]),
                shift_type=str(raw_s["shift_type"]),
                magnitude_bp=float(raw_s.get("magnitude_bp", 0.0)),
                pivot_years=float(raw_s.get("pivot_years", 5.0)),
                node_shifts_bp={
                    str(k): float(v) for k, v in raw_s.get("node_shifts_bp", {}).items()
                },
            )
        )

    return config, deposits, bond_quotes, target_bonds, scenarios


def write_json_report(path: Path, payload: Dict[str, Any]) -> None:
    """Write deterministic formatted JSON report to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
