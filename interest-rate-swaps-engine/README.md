# Interest Rate Swaps Engine

Educational and research prototype for dual-curve vanilla fixed-floating interest rate swap valuation, par rate computation, parallel PV01 / key-rate (KR01) sensitivities, deterministic curve shock scenarios, and fixed-seed Monte Carlo risk snapshots.

> **Scope & Disclaimer:** Offline synthetic fixtures only — no live capital, market feeds, credentials, or production collateral/CSA workflows.

---

## Key Capabilities

- **Explicit Dual-Curve Architecture:** Strictly separates the **discount curve** (`role=DISCOUNT`, e.g., OIS/SOFR) from the **forward projection curve** (`role=FORWARD`, e.g., 3M SOFR/EURIBOR) with currency, valuation-date, and index validation.
- **Schedule Generation with Stubs:** Supports `ANNUAL`, `SEMI_ANNUAL`, `QUARTERLY`, and `MONTHLY` legs; `FRONT_SHORT`, `FRONT_LONG`, `BACK_SHORT`, `BACK_LONG`, and strict `NONE` stub modes; EOM leap-year roll preservation; ISDA business day conventions (`UNADJUSTED`, `FOLLOWING`, `MODIFIED_FOLLOWING`, `PRECEDING`, `MODIFIED_PRECEDING`); and fixing/payment business-day lags.
- **ISDA Day Counts:** `ACT/360`, `ACT/365F`, `30/360` (US Bond Basis), `30E/360` (Eurobond Basis), and `ACT/ACT` (ISDA).
- **Known vs Projected Fixings:** Seasoned swaps mark past cashflows `SETTLED` (`PV = 0`), apply historical fixings from `FixingHistory`, project future resets from the forward curve, and fail fast with `MISSING_FIXING` when required historical fixings are absent (unless `PROJECT_FALLBACK` is explicitly configured).
- **Risk & Sensitivities:** Exact analytic par rate, par floating spread, fixed DV01/annuity, parallel PV01 (discount, forward, combined), parallel convexity, and per-pillar key-rate sensitivities (`KR01`) with automated reconciliation against parallel PV01.
- **Scenarios & Independent Reference Oracle:** Deterministic curve shocks (parallel, steepener/flattener twist, butterfly, forward-discount basis widening), seeded 3-factor PCA + basis stochastic scenarios, an independent direct-summation + bisection reference oracle, and an optional QuantLib adapter (explicitly marked `NOT_RUN` when unavailable).
- **Clean Service Separation:** Pure zero-I/O domain core (`src/irs_engine/domain/`), FastAPI REST service (`src/irs_engine/api/`), CLI (`src/irs_engine/cli.py`), and typed integration ports (`src/irs_engine/adapters.py`).

---

## Quickstart & Local Setup

```bash
cd interest-rate-swaps-engine

# Create virtual environment with Python 3.12+ and install pinned dependencies
python3 -m uv venv --python 3.12 .venv
python3 -m uv pip install --python .venv/bin/python -r requirements-lock.txt

# Or using standard python3 -m venv / pip:
# python3.12 -m venv .venv && .venv/bin/pip install -r requirements-lock.txt
```

### Run Validation & Tests

```bash
PYTHONPATH=src .venv/bin/pytest -v
```

### Run Reproducible End-to-End Demo

```bash
PYTHONPATH=src .venv/bin/python scripts/run_demo.py
```

### Regenerate Acceptance Evidence & Raw Reports

```bash
PYTHONPATH=src .venv/bin/python scripts/generate_reports.py
```

### Run CLI & FastAPI Service

```bash
# CLI pricing, sensitivities, scenarios, and oracle comparison
PYTHONPATH=src .venv/bin/python -m irs_engine.cli price data/fixtures/valid/usd_5y_vanilla_swap.json
PYTHONPATH=src .venv/bin/python -m irs_engine.cli sensitivities data/fixtures/valid/usd_5y_vanilla_swap.json
PYTHONPATH=src .venv/bin/python -m irs_engine.cli scenarios data/fixtures/valid/usd_5y_vanilla_swap.json
PYTHONPATH=src .venv/bin/python -m irs_engine.cli oracle data/fixtures/valid/hand_calculated_2y_oracle.json

# Start local FastAPI server (http://127.0.0.1:8000/docs)
PYTHONPATH=src .venv/bin/python -m irs_engine.cli serve --host 127.0.0.1 --port 8000
```

---

## Example Measured Output (Synthetic 5Y USD Vanilla Swap)

From `scripts/run_demo.py` on `data/fixtures/valid/usd_5y_vanilla_swap.json` (`10,000,000 USD` Payer, Fixed `4.25%` Semi-Annual `30/360`, Floating `USD-SOFR-3M` Quarterly `ACT/360`, valued `2026-01-05`):

```text
Valuation ID:    val_115da881c42aab8e
Fixed Leg PV:    1,890,152.32 USD
Floating Leg PV: 2,067,751.66 USD
Swap NPV:        177,599.34 USD
Par Fixed Rate:  4.649331%
Fixed DV01:      4,447.42 USD/bp
Symmetry Check:  PV(PAYER) + PV(RECEIVER) = 0.000e+00 USD
At-Market Par:   NPV at par rate = -2.328e-10 USD

Discount PV01:   -30.4515 | Sum(Disc KR01): -30.4515
Forward PV01:    4,276.5495 | Sum(Fwd KR01):  4,276.5495
Parallel PV01:   4,246.0981 | Sum(All KR01):  4,246.0979
KR01 Reconcile:  diff=-0.000172 (rel=0.0000%) | within_tol=True

Analytic Oracle: status=PASSED | abs_npv_diff=0.000e+00 | abs_par_diff=0.000e+00
QuantLib Oracle: status=NOT_RUN (Optional dependency QuantLib is not installed in the offline Python environment.)
```

*(All figures above are actual observations measured against synthetic offline fixtures, not live market quotes.)*

---

## Documentation & Acceptance Evidence

| Document | Purpose |
|---|---|
| [reports/acceptance.md](reports/acceptance.md) | Acceptance criteria mapped to live measured test and benchmark evidence |
| [reports/baseline_valuation.json](reports/baseline_valuation.json) | Versioned raw valuation outputs for baseline, par, symmetry, and seasoned swaps |
| [reports/oracle_comparison.json](reports/oracle_comparison.json) | Raw comparisons against hand-calculated & independent reference oracles |
| [reports/sensitivities_report.json](reports/sensitivities_report.json) | Raw parallel PV01, convexity, and KR01 reconciliation outputs |
| [reports/scenario_snapshots.json](reports/scenario_snapshots.json) | Raw deterministic curve shocks & seeded stochastic scenario snapshots |
| [docs/architecture.md](docs/architecture.md) | Component diagram, data flow, domain invariants, and design trade-offs |
| [docs/contracts.md](docs/contracts.md) | Input/output schemas, units, timestamp semantics, identifiers, and error codes |
| [docs/integration.md](docs/integration.md) | Reusable ports, adapter boundaries, and sibling project compatibility |
| [docs/runbook.md](docs/runbook.md) | Operator/developer setup, demo, validation, diagnostics, and recovery guide |
| [docs/adr/0001-dual-curve-architecture.md](docs/adr/0001-dual-curve-architecture.md) | Architectural Decision Record for dual-curve separation and pure domain core |
| [plan.md](plan.md) | Original project implementation plan |

---

## Assumptions, Limitations & Provenance

- **Single-currency vanilla swaps only:** Supports `USD`, `EUR`, and `GBP`. Cross-currency swaps, amortizing/accreting notionals, compounding OIS in-arrears lookbacks, Bermudan swaptions, and CSA/collateral-choice discounting are explicitly out of scope.
- **Explicit curve snapshots:** Discount and forward curves are provided as zero-rate term structures (`CurveSnapshot`); bootstrapping curves from raw futures/deposit/par-swap quotes is externalized behind `CurveProviderPort`.
- **Optional QuantLib dependency:** Not installed in the offline environment; its oracle status is explicitly logged as `NOT_RUN` rather than faked or silently skipped.
- **Fixture provenance & licenses:** All fixtures under `data/fixtures/` and calendars under `config/calendars.json` are synthetic offline artifacts authored specifically for this prototype. Runtime dependencies are MIT/BSD-licensed (`fastapi` MIT, `pydantic` MIT, `uvicorn` BSD-3-Clause, `pytest` MIT, `httpx` BSD-3-Clause).
