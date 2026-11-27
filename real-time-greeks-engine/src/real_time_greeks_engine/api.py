"""FastAPI service exposing Greeks snapshots, stale-data alerts, scenarios, and reconciliation dashboard."""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from real_time_greeks_engine.engine import RealTimeGreeksEngine
from real_time_greeks_engine.errors import (
    GreeksEngineError,
    MissingMarketDataError,
    SnapshotVersionConflictError,
    StaleMarketDataError,
    UnsupportedInstrumentError,
    ValidationError,
)
from real_time_greeks_engine.models import (
    OptionPosition,
    ScenarioShock,
    SpotTick,
    VolSurfaceSnapshot,
    YieldCurveSnapshot,
)


class ScenarioBatchRequest(BaseModel):
    shocks: list[ScenarioShock] = Field(min_length=1)
    max_workers: int | None = Field(default=None, ge=1, le=32)


class ValuationTimeRequest(BaseModel):
    valuation_time_utc: datetime
    recompute_all: bool = True


def _raise_http_for_domain_error(exc: GreeksEngineError) -> None:
    if isinstance(exc, ValidationError):
        raise HTTPException(status_code=422, detail={"error_code": exc.error_code, "message": exc.message, "details": exc.details})
    if isinstance(exc, UnsupportedInstrumentError):
        raise HTTPException(status_code=400, detail={"error_code": exc.error_code, "message": exc.message, "details": exc.details})
    if isinstance(exc, MissingMarketDataError):
        raise HTTPException(status_code=404, detail={"error_code": exc.error_code, "message": exc.message, "details": exc.details})
    if isinstance(exc, StaleMarketDataError):
        raise HTTPException(status_code=503, detail={"error_code": exc.error_code, "message": exc.message, "details": exc.details})
    if isinstance(exc, SnapshotVersionConflictError):
        raise HTTPException(status_code=409, detail={"error_code": exc.error_code, "message": exc.message, "details": exc.details})
    raise HTTPException(status_code=500, detail={"error_code": exc.error_code, "message": exc.message, "details": exc.details})


def _render_dashboard_html(engine: RealTimeGreeksEngine) -> str:
    snap = engine.get_snapshot()
    recon = engine.reconcile()
    diag = engine.diagnostics()
    lat = diag["update_latency_ms"]
    recon_badge = (
        '<span class="badge ok">PASS</span>'
        if recon.passed
        else '<span class="badge crit">DRIFT DETECTED</span>'
    )
    frozen_badge = (
        '<span class="badge warn">FROZEN POSITIONS PRESENT</span>'
        if snap.has_frozen_positions
        else '<span class="badge ok">LIVE</span>'
    )

    u_rows = "\n".join(
        f"<tr>"
        f"<td><strong>{escape(b.underlying)}</strong></td>"
        f"<td>{b.position_count}</td>"
        f"<td>{b.pv:,.2f}</td>"
        f"<td>{b.delta_shares:,.2f}</td>"
        f"<td>{b.dollar_delta:,.2f}</td>"
        f"<td>{b.dollar_gamma_1pct:,.2f}</td>"
        f"<td>{b.vega:,.2f}</td>"
        f"<td>{b.theta:,.2f}</td>"
        f"<td>{b.rho:,.2f}</td>"
        f"<td>{escape(', '.join(f.value for f in b.flags))}</td>"
        f"</tr>"
        for b in snap.by_underlying.values()
    ) or '<tr><td colspan="10">No active positions</td></tr>'

    ue_rows = "\n".join(
        f"<tr>"
        f"<td>{escape(b.underlying)}</td>"
        f"<td>{escape(b.expiry_date or '')}</td>"
        f"<td>{b.position_count}</td>"
        f"<td>{b.pv:,.2f}</td>"
        f"<td>{b.dollar_delta:,.2f}</td>"
        f"<td>{b.vega:,.2f}</td>"
        f"<td>{b.theta:,.2f}</td>"
        f"<td>{escape(', '.join(f.value for f in b.flags))}</td>"
        f"</tr>"
        for b in snap.by_underlying_expiry.values()
    ) or '<tr><td colspan="8">No active expiry buckets</td></tr>'

    alert_rows = "\n".join(
        f"<tr>"
        f"<td><span class='badge {'crit' if a.severity.value == 'CRITICAL' else 'warn'}'>{escape(a.severity.value)}</span></td>"
        f"<td>{escape(a.code)}</td>"
        f"<td>{escape(a.resource)}</td>"
        f"<td>{escape(a.message)}</td>"
        f"<td>{escape(a.detected_at_utc.isoformat())}</td>"
        f"</tr>"
        for a in snap.alerts
    ) or '<tr><td colspan="5">No active stale-data or operational alerts</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Real-Time Greeks Engine — Reconciliation Dashboard</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 24px; background: #0f172a; color: #e2e8f0; }}
    h1, h2 {{ color: #f8fafc; margin-bottom: 8px; }}
    .meta {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 20px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 24px; }}
    .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 14px; }}
    .card .label {{ font-size: 0.78rem; text-transform: uppercase; color: #94a3b8; }}
    .card .val {{ font-size: 1.35rem; font-weight: 600; margin-top: 4px; color: #f8fafc; }}
    table {{ width: 100%; border-collapse: collapse; margin-bottom: 24px; background: #1e293b; border-radius: 8px; overflow: hidden; }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid #334155; text-align: right; font-size: 0.9rem; }}
    th:first-child, td:first-child, th:last-child, td:last-child {{ text-align: left; }}
    th {{ background: #0b1220; color: #94a3b8; font-weight: 600; }}
    .badge {{ padding: 2px 8px; border-radius: 999px; font-size: 0.75rem; font-weight: 700; }}
    .badge.ok {{ background: #14532d; color: #86efac; }}
    .badge.warn {{ background: #78350f; color: #fde68a; }}
    .badge.crit {{ background: #7f1d1d; color: #fca5a5; }}
  </style>
</head>
<body>
  <h1>Real-Time Greeks Engine — Reconciliation Dashboard</h1>
  <div class="meta">
    Snapshot: <code>{escape(snap.snapshot_id)}</code> (seq {snap.snapshot_seq}) |
    Valuation UTC: <code>{escape(snap.valuation_time_utc.isoformat())}</code> |
    Status: {frozen_badge} | Reconciliation: {recon_badge} (max abs diff: {recon.max_abs_diff:.3e})
  </div>

  <div class="grid">
    <div class="card"><div class="label">Total PV</div><div class="val">{snap.totals.pv:,.2f}</div></div>
    <div class="card"><div class="label">Dollar Delta</div><div class="val">{snap.totals.dollar_delta:,.2f}</div></div>
    <div class="card"><div class="label">Dollar Gamma (1%)</div><div class="val">{snap.totals.dollar_gamma_1pct:,.2f}</div></div>
    <div class="card"><div class="label">Portfolio Vega (1%)</div><div class="val">{snap.totals.vega:,.2f}</div></div>
    <div class="card"><div class="label">Portfolio Theta (1d)</div><div class="val">{snap.totals.theta:,.2f}</div></div>
    <div class="card"><div class="label">Update p50 / p99 (ms)</div><div class="val">{lat['p50']:.3f} / {lat['p99']:.3f}</div></div>
  </div>

  <h2>Aggregated Greeks by Underlying</h2>
  <table>
    <thead>
      <tr>
        <th>Underlying</th><th>Positions</th><th>PV</th><th>Delta Shares</th>
        <th>Dollar Delta</th><th>Dollar Gamma 1%</th><th>Vega</th><th>Theta</th><th>Rho</th><th>Flags</th>
      </tr>
    </thead>
    <tbody>{u_rows}</tbody>
  </table>

  <h2>Aggregated Greeks by Underlying &amp; Expiry</h2>
  <table>
    <thead>
      <tr>
        <th>Underlying</th><th>Expiry</th><th>Positions</th><th>PV</th>
        <th>Dollar Delta</th><th>Vega</th><th>Theta</th><th>Flags</th>
      </tr>
    </thead>
    <tbody>{ue_rows}</tbody>
  </table>

  <h2>Stale-Data &amp; Operational Alerts</h2>
  <table>
    <thead>
      <tr><th>Severity</th><th>Code</th><th>Resource</th><th>Message</th><th>Detected UTC</th></tr>
    </thead>
    <tbody>{alert_rows}</tbody>
  </table>
</body>
</html>"""


def create_app(engine: RealTimeGreeksEngine | None = None) -> FastAPI:
    """Create the FastAPI application bound to a RealTimeGreeksEngine instance."""
    eng = engine or RealTimeGreeksEngine()
    app = FastAPI(
        title="Real-Time Greeks Engine API",
        version="0.1.0",
        description="Option sensitivities, incremental aggregation, stale-data alerts, and reconciliation.",
    )
    app.state.engine = eng

    @app.get("/health")
    def health() -> dict[str, Any]:
        snap = eng.get_snapshot()
        return {
            "status": "ok",
            "engine_id": eng.config.engine_id,
            "snapshot_seq": snap.snapshot_seq,
            "has_frozen_positions": snap.has_frozen_positions,
            "active_alerts": len(snap.alerts),
        }

    @app.get("/metrics")
    def metrics() -> dict[str, Any]:
        return eng.diagnostics()

    @app.get("/v1/snapshot")
    def get_snapshot() -> dict[str, Any]:
        return eng.get_snapshot().model_dump(mode="json")

    @app.get("/v1/alerts")
    def get_alerts() -> dict[str, Any]:
        alerts = eng.get_alerts()
        return {"count": len(alerts), "alerts": [a.model_dump(mode="json") for a in alerts]}

    @app.get("/v1/reconcile")
    def reconcile() -> dict[str, Any]:
        return eng.reconcile().model_dump(mode="json")

    @app.post("/v1/valuation-time")
    def set_valuation_time(
        req: ValuationTimeRequest,
        expected_snapshot_seq: int | None = Query(default=None),
    ) -> dict[str, Any]:
        try:
            snap = eng.set_valuation_time(
                req.valuation_time_utc,
                recompute_all=req.recompute_all,
                expected_snapshot_seq=expected_snapshot_seq,
            )
            return snap.model_dump(mode="json")
        except GreeksEngineError as exc:
            _raise_http_for_domain_error(exc)

    @app.post("/v1/positions")
    def upsert_position(
        position: OptionPosition,
        expected_snapshot_seq: int | None = Query(default=None),
    ) -> dict[str, Any]:
        try:
            snap = eng.upsert_position(position, expected_snapshot_seq=expected_snapshot_seq)
            return snap.model_dump(mode="json")
        except GreeksEngineError as exc:
            _raise_http_for_domain_error(exc)

    @app.delete("/v1/positions/{position_id}")
    def remove_position(
        position_id: str,
        expected_snapshot_seq: int | None = Query(default=None),
    ) -> dict[str, Any]:
        try:
            snap = eng.remove_position(position_id, expected_snapshot_seq=expected_snapshot_seq)
            return snap.model_dump(mode="json")
        except GreeksEngineError as exc:
            _raise_http_for_domain_error(exc)

    @app.post("/v1/market/spot")
    def ingest_spot(
        tick: SpotTick,
        advance_valuation_time: bool = Query(default=False),
        expected_snapshot_seq: int | None = Query(default=None),
    ) -> dict[str, Any]:
        try:
            outcome, snap = eng.ingest_spot_tick(
                tick,
                advance_valuation_time=advance_valuation_time,
                expected_snapshot_seq=expected_snapshot_seq,
            )
            return {
                "accepted": outcome.accepted,
                "reason": outcome.reason,
                "snapshot": snap.model_dump(mode="json"),
            }
        except GreeksEngineError as exc:
            _raise_http_for_domain_error(exc)

    @app.post("/v1/market/curve")
    def upsert_curve(
        curve: YieldCurveSnapshot,
        expected_snapshot_seq: int | None = Query(default=None),
    ) -> dict[str, Any]:
        try:
            snap = eng.upsert_yield_curve(curve, expected_snapshot_seq=expected_snapshot_seq)
            return snap.model_dump(mode="json")
        except GreeksEngineError as exc:
            _raise_http_for_domain_error(exc)

    @app.post("/v1/market/vol")
    def upsert_vol(
        surface: VolSurfaceSnapshot,
        expected_snapshot_seq: int | None = Query(default=None),
    ) -> dict[str, Any]:
        try:
            snap = eng.upsert_vol_surface(surface, expected_snapshot_seq=expected_snapshot_seq)
            return snap.model_dump(mode="json")
        except GreeksEngineError as exc:
            _raise_http_for_domain_error(exc)

    @app.post("/v1/scenarios")
    def run_scenarios(req: ScenarioBatchRequest) -> dict[str, Any]:
        try:
            results = eng.run_scenarios(req.shocks, max_workers=req.max_workers)
            return {
                "count": len(results),
                "results": [r.model_dump(mode="json") for r in results],
            }
        except GreeksEngineError as exc:
            _raise_http_for_domain_error(exc)

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard() -> HTMLResponse:
        return HTMLResponse(content=_render_dashboard_html(eng))

    return app
