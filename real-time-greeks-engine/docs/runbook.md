# Operator & Developer Runbook — Real-Time Greeks Engine

## 1. Local Setup & Validation

```bash
cd real-time-greeks-engine

# Verify environment and run full test suite (unit, integration, failure, concurrency, API)
.venv/bin/pytest -v

# Run deterministic demo and verify reconciliation passes
.venv/bin/python scripts/run_demo.py

# Run analytic-vs-FD accuracy & 1,000-tick latency benchmark
.venv/bin/python scripts/run_benchmark.py
```

Expected exit code for all three commands is `0`.

---

## 2. Running the API & Reconciliation Dashboard

```bash
.venv/bin/python -m real_time_greeks_engine.cli serve \
  --config config/default.json \
  --fixture data/fixtures/valid_portfolio.json \
  --host 127.0.0.1 \
  --port 8000
```

Key endpoints:
- Health check: `GET http://127.0.0.1:8000/health`
- Live metrics (`p50`/`p95`/`p99` latencies, cache hit rate, counters): `GET http://127.0.0.1:8000/metrics`
- Current atomic snapshot: `GET http://127.0.0.1:8000/v1/snapshot`
- Active stale-data & operational alerts: `GET http://127.0.0.1:8000/v1/alerts`
- On-demand full-recompute reconciliation: `GET http://127.0.0.1:8000/v1/reconcile`
- Visual reconciliation dashboard: `http://127.0.0.1:8000/dashboard`

---

## 3. Diagnostics & Recovery Procedures

### A. `STALE_SPOT`, `STALE_CURVE`, or `STALE_VOL_SURFACE` Alert / Frozen Snapshot
- **Symptom:** `/health` shows `"has_frozen_positions": true`, `/dashboard` displays `FROZEN POSITIONS PRESENT`, and `/v1/alerts` lists `CRITICAL` staleness alerts.
- **Behavior:** Affected positions preserve their last reliable Greeks (`is_frozen=True`, `FROZEN_SNAPSHOT`) instead of publishing unverified numbers.
- **Recovery:**
  1. Inspect `/v1/alerts` to identify the exact stale resource (`spot:<U>`, `vol:<U>`, or `curve:<CCY>`) and its `age_seconds` vs `max_age_seconds`.
  2. Publish a fresh market snapshot with current `as_of_utc` / `event_time_utc` to `POST /v1/market/{spot|vol|curve}`.
  3. Confirm `/v1/alerts` clears the staleness alert and `/health` reports `"has_frozen_positions": false`.

### B. `DUPLICATE_TICK` or `OUT_OF_ORDER_TICK` Alerts
- **Symptom:** `POST /v1/market/spot` returns `{"accepted": false, "reason": "DUPLICATE_TICK" | "OUT_OF_ORDER_TICK"}` and `/metrics` increments `duplicate_ticks_ignored` or `out_of_order_ticks_ignored`.
- **Behavior:** Ignored safely with zero mutation to spot or Greeks.
- **Recovery:** Check upstream feed sequencing (`market_versions.spots.<U>.sequence_no` in `/v1/snapshot`) and resume publishing strictly increasing `sequence_no` with unique `tick_id`.

### C. HTTP `409 Conflict` (`SNAPSHOT_VERSION_CONFLICT`)
- **Symptom:** A writer passed `expected_snapshot_seq` that no longer matches the engine's latest `snapshot_seq`.
- **Recovery:** Re-read `GET /v1/snapshot`, verify the latest `snapshot_seq`, reconcile desired state, and retry with the updated `expected_snapshot_seq`.

### D. Missing or Invalid Configuration on Startup (`ConfigurationError`)
- **Symptom:** Startup fails immediately with `ConfigurationError` (no silent fallback to defaults).
- **Recovery:** Validate JSON syntax and required fields against `config/default.json` and retry.

### E. Reconciliation Drift (`GET /v1/reconcile` returns `"passed": false`)
- **Symptom:** Incremental bucket totals diverge from uncached full recompute beyond `abs_tolerance` / `rel_tolerance`.
- **Recovery:**
  1. Capture `/v1/reconcile` JSON (`mismatched_buckets`) and `/v1/snapshot` for post-mortem.
  2. Trigger a full state rebuild by restarting with the latest position & market snapshot or replaying the canonical event log (`engine.replay_events(...)`).
  3. Re-run `GET /v1/reconcile` and confirm `"passed": true`.
