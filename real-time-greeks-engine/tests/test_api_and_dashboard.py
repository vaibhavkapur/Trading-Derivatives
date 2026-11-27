"""FastAPI service and reconciliation dashboard tests."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from real_time_greeks_engine.adapters import JsonFixtureAdapter
from real_time_greeks_engine.api import create_app
from real_time_greeks_engine.config import load_config
from real_time_greeks_engine.engine import RealTimeGreeksEngine

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "data" / "fixtures"


def _make_client() -> TestClient:
    engine = RealTimeGreeksEngine(load_config(ROOT / "config" / "default.json"))
    JsonFixtureAdapter(FIXTURES / "valid_portfolio.json").load_into(engine)
    return TestClient(create_app(engine))


def test_api_snapshot_reconcile_metrics_and_dashboard() -> None:
    client = _make_client()

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    snap = client.get("/v1/snapshot")
    assert snap.status_code == 200
    body = snap.json()
    assert body["totals"]["position_count"] == 5
    assert "ACME" in body["by_underlying"]

    recon = client.get("/v1/reconcile")
    assert recon.status_code == 200
    assert recon.json()["passed"] is True

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "p50" in metrics.json()["update_latency_ms"]
    assert "p99" in metrics.json()["update_latency_ms"]

    dash = client.get("/dashboard")
    assert dash.status_code == 200
    assert "Real-Time Greeks Engine" in dash.text
    assert "PASS" in dash.text
    assert "ACME" in dash.text


def test_api_spot_tick_conflict_and_scenarios() -> None:
    client = _make_client()
    snap = client.get("/v1/snapshot").json()
    vt = snap["valuation_time_utc"]
    seq = snap["snapshot_seq"]

    # Valid spot tick
    resp = client.post(
        "/v1/market/spot",
        json={
            "tick_id": "API-TICK-2",
            "underlying": "ACME",
            "spot": 101.5,
            "sequence_no": 2,
            "event_time_utc": vt,
            "received_time_utc": vt,
            "source": "API_TEST",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["accepted"] is True

    # Stale expected_snapshot_seq -> HTTP 409 Conflict
    conflict = client.post(
        f"/v1/market/spot?expected_snapshot_seq={seq}",
        json={
            "tick_id": "API-TICK-3",
            "underlying": "ACME",
            "spot": 102.0,
            "sequence_no": 3,
            "event_time_utc": vt,
            "received_time_utc": vt,
            "source": "API_TEST",
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["error_code"] == "SNAPSHOT_VERSION_CONFLICT"

    # Parallel scenarios endpoint
    scen = client.post(
        "/v1/scenarios",
        json={
            "shocks": [
                {"scenario_id": "DOWN_5", "spot_shock_pct": -0.05},
                {"scenario_id": "UP_5", "spot_shock_pct": 0.05},
            ],
            "max_workers": 2,
        },
    )
    assert scen.status_code == 200
    assert scen.json()["count"] == 2
