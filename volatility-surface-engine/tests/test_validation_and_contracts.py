"""Unit tests for configuration loading, schema validation, and data contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from volatility_surface_engine import (
    ConfigurationError,
    OptionChainSnapshot,
    ValidationError,
    compute_year_fraction,
    load_chain_snapshot,
    load_config,
    validate_chain_snapshot,
    validate_config,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "default_config.json"
FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"


def test_load_default_config_and_hash_determinism() -> None:
    cfg1 = load_config(CONFIG_PATH)
    cfg2 = load_config(CONFIG_PATH)
    assert cfg1.seed == 42
    assert cfg1.conventions.day_count == "ACT/365F"
    assert cfg1.content_hash() == cfg2.content_hash()
    assert len(cfg1.content_hash()) == 16


def test_missing_or_malformed_config_raises_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not found"):
        load_config(tmp_path / "missing_config.json")

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Malformed JSON"):
        load_config(bad_json)


def test_invalid_config_parameters_rejected() -> None:
    with pytest.raises(ConfigurationError, match="Unknown keys"):
        validate_config({"cleaning": {"nonexistent_param": 1}})

    with pytest.raises(ConfigurationError, match="seed"):
        validate_config({"seed": -1})

    with pytest.raises(ConfigurationError, match="SABR"):
        validate_config({"sabr": {"beta": 1.5}})

    with pytest.raises(ConfigurationError, match="Unsupported interpolation"):
        validate_config({"interpolation": {"method": "cubic_magic"}})


def test_compute_year_fraction_day_counts() -> None:
    t_365 = compute_year_fraction("2026-01-15T16:00:00Z", "2026-04-15T16:00:00Z", "ACT/365F")
    t_360 = compute_year_fraction("2026-01-15T16:00:00Z", "2026-04-15T16:00:00Z", "ACT/360")
    assert abs(t_365 - 90.0 / 365.0) < 1e-12
    assert abs(t_360 - 90.0 / 360.0) < 1e-12

    with pytest.raises(ValidationError, match="timezone"):
        compute_year_fraction("2026-01-15T16:00:00", "2026-04-15T16:00:00Z", "ACT/365F")

    with pytest.raises(ValidationError, match="Unsupported day_count"):
        compute_year_fraction("2026-01-15T16:00:00Z", "2026-04-15T16:00:00Z", "30/360")


def test_valid_reference_chain_passes_validation() -> None:
    snap = load_chain_snapshot(FIXTURES_DIR / "valid_chain_reference.json")
    resolved = validate_chain_snapshot(snap)
    assert len(resolved) == 4
    assert all(t > 0.0 for t in resolved.values())


def test_invalid_chain_contracts_raise_validation_error() -> None:
    raw = json.loads((FIXTURES_DIR / "valid_chain_reference.json").read_text(encoding="utf-8"))

    # Unsupported exercise style
    bad_style = json.loads(json.dumps(raw))
    bad_style["conventions"]["exercise_style"] = "american"
    with pytest.raises(ValidationError, match="Only European"):
        validate_chain_snapshot(OptionChainSnapshot.from_dict(bad_style))

    # Unsupported option type
    bad_opt = json.loads(json.dumps(raw))
    bad_opt["quotes"][0]["option_type"] = "digital"
    with pytest.raises(ValidationError, match="Unsupported option_type"):
        validate_chain_snapshot(OptionChainSnapshot.from_dict(bad_opt))

    # Unknown expiry reference
    bad_exp = json.loads(json.dumps(raw))
    bad_exp["quotes"][0]["expiry_timestamp"] = "2099-01-01T00:00:00Z"
    with pytest.raises(ValidationError, match="unknown expiry_timestamp"):
        validate_chain_snapshot(OptionChainSnapshot.from_dict(bad_exp))

    # Non-positive forward
    bad_fwd = json.loads(json.dumps(raw))
    bad_fwd["expiries"][0]["forward"] = -10.0
    with pytest.raises(ValidationError, match="Forward must be finite and positive"):
        validate_chain_snapshot(OptionChainSnapshot.from_dict(bad_fwd))

    # Duplicate quote_id
    dup_q = json.loads(json.dumps(raw))
    dup_q["quotes"][1]["quote_id"] = dup_q["quotes"][0]["quote_id"]
    with pytest.raises(ValidationError, match="Duplicate quote_id"):
        validate_chain_snapshot(OptionChainSnapshot.from_dict(dup_q))
