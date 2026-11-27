# Real-Time Greeks Engine

Compute and incrementally aggregate option sensitivities with explicit unit definitions, version-aware caching, stale-data freeze protection, parallel scenario recomputation, and a FastAPI + HTML reconciliation dashboard.

> **Scope & Safety Notice:** Independently developed educational/research prototype. Uses synthetic offline fixtures only. No live capital, real customer records, market-data credentials, or proprietary artifacts are included or supported.

---

## Key Capabilities

- **Analytic BSM Greeks & Bump-and-Revalue Fallback (`src/real_time_greeks_engine/pricing.py`):** Closed-form Black-Scholes-Merton sensitivities (`price`, `delta`, `gamma`, `vega`, `theta`, `rho`, `vanna`, `charm`, `volga`) with explicit unit conventions (`GreeksUnitSpec`) and central finite-difference fallback for custom pricers or numerical verification.
- **Explicit Numerical Boundary Flags:** Near-expiry (`< 1 day` or expired), low-vol (`sigma * sqrt(T) < 1e-3`), and extreme moneyness positions are flagged explicitly (`NEAR_EXPIRY`, `LOW_VOL`, `EXTREME_MONEYNESS`) so discontinuities are never hidden.
- **Incremental $O(1)$ Aggregation (`src/real_time_greeks_engine/aggregator.py`):** Maintains live totals and buckets by `underlying` and `underlying|expiry_date` on every spot tick, surface/curve update, or position sign/multiplier change, verified by `reconcile()` against an uncached full recompute from scratch.
- **Version-Aware Cache & Atomic Snapshots (`cache.py`, `engine.py`):** Composite version keys prevent stale cache collisions; `RLock` + atomic immutable `PortfolioSnapshot` swaps + optional optimistic `expected_snapshot_seq` checks prevent snapshot races.
- **Bounded Staleness & Freeze-on-Stale (`market_data.py`, `engine.py`):** Enforces per-feed TTLs for spots, yield curves, and volatility surfaces. Stale feeds raise structured `StaleDataAlert`s and freeze last known reliable Greeks (`is_frozen=True`, `FROZEN_SNAPSHOT`) until refreshed (or fail loudly in strict mode).
- **FastAPI Service & Reconciliation Dashboard (`api.py`):** JSON endpoints for snapshots, alerts, reconciliation, ingestion, parallel scenarios, and live `p50`/`p95`/`p99` latency metrics, plus an interactive HTML reconciliation dashboard at `/dashboard`.

---

## Project Layout

```text
real-time-greeks-engine/
├── config/default.json              # Pinned engine configuration
├── data/fixtures/                   # Valid, invalid/boundary, and replay synthetic fixtures
├── docs/
│   ├── adr/0001-architecture-and-greeks-units.md
│   ├── architecture.md              # Component diagram, data flow, invariants, trade-offs
│   ├── contracts.md                 # Schemas, units, timestamp semantics, error codes
│   ├── integration.md               # Adapter boundaries and optional integrations
│   └── runbook.md                   # Demo, diagnostics, validation, and recovery steps
├── reports/
│   ├── acceptance.md                # Acceptance criteria mapped to measured evidence
│   ├── benchmark_results.json       # Raw analytic-vs-FD & latency benchmark output
│   └── demo_output.json             # Raw deterministic demo & replay output
├── scripts/
│   ├── run_demo.py                  # Deterministic end-to-end demo
│   └── run_benchmark.py             # Analytic-vs-FD + 1,000-tick p50/p99 benchmark
├── src/real_time_greeks_engine/     # Core domain, engine, adapters, FastAPI app, CLI
├── tests/                           # Unit, integration, failure, concurrency, and API tests
├── pyproject.toml                   # Package metadata and dependency ranges
└── requirements.lock                # Exact pinned versions verified in local environment
```

---

## Quickstart & Setup

Requires **Python 3.11+** (verified on Python `3.12.15`, macOS arm64).

```bash
cd real-time-greeks-engine

# Create virtual environment and install pinned dependencies + package
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
pip install --no-deps -e .
```

*(With `uv`, run `python3 -m uv venv --python 3.12 .venv && python3 -m uv pip install --python .venv/bin/python -r requirements.lock && python3 -m uv pip install --python .venv/bin/python --no-build-isolation --no-deps -e .`.)*

---

## Demo, Validation, and API Walkthrough

```bash
# 1. Run all unit, integration, failure, concurrency, and API tests
.venv/bin/pytest -v

# 2. Run deterministic end-to-end demo (writes reports/demo_output.json)
.venv/bin/python scripts/run_demo.py

# 3. Run analytic-vs-FD and 1,000-tick latency benchmark (writes reports/benchmark_results.json)
.venv/bin/python scripts/run_benchmark.py

# 4. Run CLI demo directly
.venv/bin/python -m real_time_greeks_engine.cli demo

# 5. Start local FastAPI service & reconciliation dashboard
.venv/bin/python -m real_time_greeks_engine.cli serve --host 127.0.0.1 --port 8000
# Open http://127.0.0.1:8000/dashboard or GET http://127.0.0.1:8000/v1/snapshot
```

### Example Demo Output (`reports/demo_output.json`, after deterministic replay)

```json
{
  "snapshot_id": "SNAP-000018-28ac8c40ef2a",
  "totals": {
    "position_count": 5,
    "pv": 55176.2076378621,
    "dollar_delta": 2700618.3098455383,
    "dollar_gamma_1pct": -16645.11306565307,
    "vega": -1069.4246923896405,
    "theta": -90.2054926071881,
    "rho": 6919.842224987919,
    "unreliable_count": 0,
    "frozen_count": 0
  },
  "reconciliation_passed": true,
  "reconciliation_max_abs_diff": 4.656612873077393e-10
}
```

---

## Measured Results Summary

All numbers below are **actual local observations** from synthetic benchmark and demo fixtures on `Python 3.12.15` (`macOS-26.5.1-arm64`), not projections or targets. Full evidence is in [reports/acceptance.md](reports/acceptance.md), [reports/benchmark_results.json](reports/benchmark_results.json), and [reports/demo_output.json](reports/demo_output.json).

| Check | Target | Measured Result | Evidence |
|---|---|---|---|
| Test suite (`pytest -v`) | All required scenarios pass | **22 passed** in `0.29s` | `tests/` |
| Analytic vs FD max relative error (primary Greeks) | `<= 1e-4` | **`3.80e-8`** | `reports/benchmark_results.json` |
| Analytic vs FD max relative error (all 9 Greeks incl. vanna/charm/volga) | `<= 1e-4` | **`8.60e-7`** | `reports/benchmark_results.json` |
| Incremental vs full recompute reconciliation (after 1,000 ticks, 200 options) | Within `1e-8` abs / `1e-9` rel | **PASS** (`max_abs_diff = 2.79e-9`, `max_rel_diff = 8.52e-14`) | `reports/benchmark_results.json` |
| Incremental spot-tick update latency (200 options, 10 underlyings, 1,000 ticks) | Publish p50 / p99 | **`p50 = 0.4474 ms`**, **`p99 = 0.8357 ms`** (`mean = 0.5194 ms`) | `reports/benchmark_results.json` |
| 24 scenarios × 200 options (serial vs 4 threads) | Deterministic match | `max_pv_diff = 0.0` (`46.185 ms` serial vs `47.678 ms` 4-thread; GIL-bound) | `reports/benchmark_results.json` |

---

## Assumptions, Limitations, and Untested Optional Capabilities

- **Model scope:** Closed-form BSM assumes European exercise, flat continuous dividend yield per underlying, piecewise linear continuous zero-rate curve, and 2D bilinear implied volatility interpolation with flat boundary clamping. American exercise without an explicit custom pricer adapter is rejected loudly with `UnsupportedInstrumentError`.
- **Near-expiry discontinuities:** Options within `min_reliable_tte_days` (default `1.0` calendar day) or expired (`T <= 0`) are flagged `NEAR_EXPIRY`; expired options return intrinsic value and zero Greeks.
- **CPython GIL on parallel scenarios:** `ThreadPoolExecutor` offloads scenario evaluation off the live engine write lock and produces bit-identical results to serial execution, but pure-Python BSM math is GIL-bound so thread pools do not linearly speed up CPU-only runs (see [docs/architecture.md](docs/architecture.md)).
- **Explicitly not implemented / not run (optional in plan):** Optional C++20 native pricing kernel and optional live Redis pub/sub backend are not built or tested in this offline prototype; adapter boundaries are documented in [docs/integration.md](docs/integration.md).

---

## Documentation

- [Architecture & Trade-Offs](docs/architecture.md)
- [ADR 0001: Canonical Greeks Units & Freeze-on-Stale](docs/adr/0001-architecture-and-greeks-units.md)
- [Data Contracts & Error Taxonomy](docs/contracts.md)
- [Integration & Adapter Boundaries](docs/integration.md)
- [Operator & Developer Runbook](docs/runbook.md)
- [Acceptance & Verification Report](reports/acceptance.md)

---

## Dependency Licenses & Fixture Provenance

- **Dependencies:** Core runtime uses MIT/BSD-licensed open-source packages (`fastapi` MIT, `pydantic` MIT, `uvicorn` BSD-3-Clause, `starlette` BSD-3-Clause; dev: `pytest` MIT, `httpx` BSD-3-Clause). Exact pinned versions are in [requirements.lock](requirements.lock).
- **Fixture provenance:** All portfolios, spot ticks, yield curves, and volatility surfaces under `data/fixtures/` (`ACME`, `SPX_SYN`, `TECH_SYN`, `UND_00..09`) are 100% synthetic data authored specifically for this prototype. No confidential, customer, or proprietary data is present.
