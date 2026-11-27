"""Boundary, negative fixture, and operational failure tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from derivatives_pricing_engine import (
    ConfigurationError,
    PricingEngine,
    PricingEngineError,
    PricingRequest,
    ValidationError,
    load_config,
)
from derivatives_pricing_engine.cli import main as cli_main


def test_boundary_fixtures_succeed(engine: PricingEngine, fixtures_dir: Path) -> None:
    payload = json.loads((fixtures_dir / "boundary_cases.json").read_text(encoding="utf-8"))
    for case in payload["cases"]:
        req = PricingRequest.from_dict(
            {
                "contract": case["contract"],
                "market": case["market"],
                "method": "black_scholes",
            }
        )
        res = engine.price(req)
        if "expected_price" in case:
            assert res.unit_price == pytest.approx(case["expected_price"], abs=1e-10)
        if "expected_price_min" in case:
            assert res.unit_price >= case["expected_price_min"]


def test_all_invalid_fixtures_fail_with_expected_error_codes(engine: PricingEngine, fixtures_dir: Path) -> None:
    payload = json.loads((fixtures_dir / "invalid_cases.json").read_text(encoding="utf-8"))
    for case in payload["cases"]:
        with pytest.raises(PricingEngineError) as exc_info:
            req = PricingRequest.from_dict(case["payload"])
            engine.price(req)
        assert exc_info.value.error_code == case["expected_error_code"], f"Mismatch on case {case['name']}"


def test_unknown_method_params_rejected(engine: PricingEngine, fixtures_dir: Path) -> None:
    raw = json.loads((fixtures_dir / "valid_european_call.json").read_text(encoding="utf-8"))
    raw["method"] = "binomial"
    raw["method_params"] = {"steps": 100, "typo_param": 999}
    req = PricingRequest.from_dict(raw)
    with pytest.raises(ValidationError):
        engine.price(req)


def test_operational_missing_and_malformed_config(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(ConfigurationError):
        load_config(missing)

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not-valid-json", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(bad_json)

    wrong_version = tmp_path / "wrong_version.json"
    wrong_version.write_text(
        json.dumps({"config_version": "9.9.9", "engine": {}, "methods": {}, "benchmarks": {}}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError):
        load_config(wrong_version)


def test_cli_returns_nonzero_on_invalid_input_and_missing_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc_cfg = cli_main(["demo", "--config", str(tmp_path / "missing.json")])
    assert rc_cfg == 1
    err = json.loads(capsys.readouterr().err)
    assert err["status"] == "error"
    assert err["error"]["error_code"] == "CONFIGURATION_ERROR"
