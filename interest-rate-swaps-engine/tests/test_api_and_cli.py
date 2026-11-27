"""FastAPI service endpoint tests and CLI integration tests."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from irs_engine.api.app import create_app
from irs_engine.cli import main as cli_main
from irs_engine.domain.calendars import CalendarRegistry


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_api_health_and_valuation_endpoints(
    fixtures_dir: Path, config_dir: Path, calendar_registry: CalendarRegistry
) -> None:
    app = create_app(calendar_registry=calendar_registry)
    client = TestClient(app)

    r_health = client.get("/health")
    assert r_health.status_code == 200
    assert r_health.json()["status"] == "ok"

    bundle = _load_json(fixtures_dir / "valid" / "usd_5y_vanilla_swap.json")
    req_body = {
        "trade": bundle["trade"],
        "discount_curve": bundle["discount_curve"],
        "forward_curve": bundle["forward_curve"],
        "fixing_history": bundle["fixing_history"],
    }

    r_sched = client.post("/v1/schedule", json={"trade": bundle["trade"]})
    assert r_sched.status_code == 200
    assert len(r_sched.json()["fixed_schedule"]["periods"]) == 10
    assert len(r_sched.json()["floating_schedule"]["periods"]) == 20

    r_price = client.post("/v1/price", json=req_body)
    assert r_price.status_code == 200
    priced = r_price.json()
    assert priced["valuation_id"].startswith("val_")
    assert priced["trade_id"] == "USD-5Y-IRS-001"

    r_par = client.post("/v1/par-rate", json=req_body)
    assert r_par.status_code == 200
    assert abs(r_par.json()["par_rate"] - priced["par_rate"]) < 1e-12

    r_sens = client.post("/v1/sensitivities", json=req_body)
    assert r_sens.status_code == 200
    assert r_sens.json()["reconciliation_within_tolerance"] is True

    r_oracle = client.post("/v1/oracle-check", json=req_body)
    assert r_oracle.status_code == 200
    assert r_oracle.json()["analytic_oracle"]["status"] == "PASSED"

    scen_cfg = _load_json(config_dir / "scenarios.json")
    r_scen = client.post(
        "/v1/scenarios",
        json={
            **req_body,
            "deterministic_scenarios": scen_cfg["deterministic_scenarios"][:3],
            "stochastic": {"seed": 42, "num_paths": 25},
        },
    )
    assert r_scen.status_code == 200
    assert len(r_scen.json()["deterministic_scenarios"]) == 3
    assert r_scen.json()["stochastic_summary"]["num_paths"] == 25

    r_metrics = client.get("/v1/metrics")
    assert r_metrics.status_code == 200
    assert r_metrics.json()["counters"]["valuations.succeeded"] >= 1


def test_api_returns_structured_error_envelope_on_domain_errors(
    fixtures_dir: Path, calendar_registry: CalendarRegistry
) -> None:
    app = create_app(calendar_registry=calendar_registry)
    client = TestClient(app)

    # Missing historical fixing -> 422 with MISSING_FIXING error code
    bad_fix = _load_json(fixtures_dir / "invalid" / "missing_historical_fixing.json")
    r_err = client.post(
        "/v1/price",
        json={
            "trade": bad_fix["trade"],
            "discount_curve": bad_fix["discount_curve"],
            "forward_curve": bad_fix["forward_curve"],
            "fixing_history": bad_fix["fixing_history"],
        },
    )
    assert r_err.status_code == 422
    assert r_err.json()["error_code"] == "MISSING_FIXING"

    # Currency mismatch -> 422 with CURVE_ERROR
    bad_ccy = _load_json(fixtures_dir / "invalid" / "currency_mismatch.json")
    r_ccy = client.post(
        "/v1/price",
        json={
            "trade": bad_ccy["trade"],
            "discount_curve": bad_ccy["discount_curve"],
            "forward_curve": bad_ccy["forward_curve"],
        },
    )
    assert r_ccy.status_code == 422
    assert r_ccy.json()["error_code"] == "CURVE_ERROR"


def test_cli_commands_and_error_exit_code(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    valid_path = str(fixtures_dir / "valid" / "usd_5y_vanilla_swap.json")
    out_file = tmp_path / "cli_price.json"

    assert cli_main(["price", valid_path, "--output", str(out_file)]) == 0
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["trade_id"] == "USD-5Y-IRS-001"

    assert cli_main(["schedule", valid_path, "--output", str(tmp_path / "sched.json")]) == 0
    assert cli_main(["sensitivities", valid_path, "--output", str(tmp_path / "sens.json")]) == 0
    assert cli_main(["oracle", valid_path, "--output", str(tmp_path / "oracle.json")]) == 0
    assert cli_main(["scenarios", valid_path, "--output", str(tmp_path / "scen.json")]) == 0

    invalid_path = str(fixtures_dir / "invalid" / "missing_historical_fixing.json")
    assert cli_main(["price", invalid_path]) == 1
