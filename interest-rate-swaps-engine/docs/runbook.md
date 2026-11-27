# Operator & Developer Runbook — Interest Rate Swaps Engine

## 1. Environment Setup (Clean Checkout)

```bash
cd interest-rate-swaps-engine

# Option A: uv (recommended, works offline if cache is populated)
python3 -m uv venv --python 3.12 .venv
python3 -m uv pip install --python .venv/bin/python -r requirements-lock.txt

# Option B: standard venv + pip
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-lock.txt
```

---

## 2. Running Demo, Tests, and Report Generation

```bash
# 1. Full unit, integration, boundary, API, and CLI test suite
PYTHONPATH=src .venv/bin/pytest -v

# 2. Reproducible demo walkthrough
PYTHONPATH=src .venv/bin/python scripts/run_demo.py

# 3. Regenerate raw versioned JSON reports + reports/acceptance.md
PYTHONPATH=src .venv/bin/python scripts/generate_reports.py
```

---

## 3. Running the Pricing API Locally

```bash
PYTHONPATH=src .venv/bin/python -m irs_engine.cli serve --host 127.0.0.1 --port 8000
```

Verify health and metrics:

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/v1/metrics
```

Submit a valuation bundle:

```bash
curl -s -X POST http://127.0.0.1:8000/v1/price \
  -H "Content-Type: application/json" \
  -d @data/fixtures/valid/usd_5y_vanilla_swap.json
```

---

## 4. Diagnostics, Replay, and Deterministic Rerun

- **Structured JSON Logs:** API and engine events emit single-line JSON logs with `timestamp_utc`, `level`, `trade_id`, `valuation_id`, `npv`, `par_rate`, and structured error fields.
- **Diagnostic Counters:** `GET /v1/metrics` returns request counts, successful valuation counts, error counts by `error_code`, and runtime environment metadata (`python_version`, package versions, `engine_version`).
- **Deterministic Replay:** Save any input bundle JSON (trade + discount curve + forward curve + fixing history + config). Re-running via CLI:
  ```bash
  PYTHONPATH=src .venv/bin/python -m irs_engine.cli price <saved_bundle.json> --output replay_out.json
  ```
  Verify that `valuation_id` and `npv` match the original run identically.

---

## 5. Troubleshooting & Recovery

| Symptom / Error Code | Likely Cause | Recovery Action |
|---|---|---|
| `MISSING_FIXING` (HTTP 422 / CLI exit 1) | Seasoned swap has an active floating period with `fixing_date < valuation_date` absent from `fixing_history` | Supply the historical `(index_name, fixing_date, rate)` entry in `fixing_history`, OR explicitly set `config.missing_fixing_policy = "PROJECT_FALLBACK"` if curve projection is intentionally desired (note: flagged as `FALLBACK_PROJECTED` in cashflow rows and diagnostics). |
| `CURVE_ERROR` — role/currency/date mismatch | Forward curve passed as discount curve, currency differs from trade, or `valuation_date` differs between curves | Verify `discount_curve.role == "DISCOUNT"`, `forward_curve.role == "FORWARD"`, matching `currency`, and identical `valuation_date`. |
| `SCHEDULE_ERROR` | Trade dates are not an integer multiple of frequency with `stub_type: "NONE"` | Set an explicit stub type (`FRONT_SHORT`, `FRONT_LONG`, `BACK_SHORT`, or `BACK_LONG`). |
| `CALENDAR_ERROR` | Unrecognized holiday calendar name or missing `config/calendars.json` | Use a configured calendar (`USD_NY`, `EUR_TARGET`, `GBP_LON`, `WEEKEND_ONLY`, `NONE`) or pass `--calendars <path>`. |
| `CONFIGURATION_ERROR` | Missing or malformed JSON config file | Restore `config/engine_config.json` and `config/scenarios.json` from version control. |
| `QuantLib Oracle: NOT_RUN` | Optional `QuantLib` package is not installed | Expected in offline default setup. Primary verification uses the independent analytic direct-summation & bisection oracle (`status=PASSED`). |
