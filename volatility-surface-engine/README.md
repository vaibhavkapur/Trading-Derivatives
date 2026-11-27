# Volatility Surface Engine

Arbitrage-aware implied-volatility surface engine for European options.

> **Scope & Disclaimer:** Educational / research prototype; offline synthetic and mock data only. Not for live trading or production use.

## Overview

`volatility-surface-engine` constructs, validates, queries, and snapshots arbitrage-aware implied-volatility surfaces from timestamped option chains:

1. **Contract Validation & Quote Cleaning (`validation.py`, `cleaning.py`)**: Explicit pricing conventions (`USD`, `ACT/365F`, `european`), bid/ask hygiene filters (crossed quotes, wide relative/absolute spreads, non-positive bids, intrinsic bound violations, expired tenors, near-zero vega), OTM wing selection, and bid-ask-weighted put-call parity forward estimation.
2. **Robust Black-76 Pricing & IV Inversion (`black76.py`)**: Analytic Black-76 pricing and Greeks (delta, gamma, vega), strict no-arbitrage price bounds enforcement (no IV estimate without valid bounds), and OTM-parity safeguarded Newton-Raphson + Brent inversion.
3. **Bid-Ask Weighted Slice Calibration (`svi.py`, `sabr.py`)**: Deterministic fixed-seed multi-start calibration for raw SVI slices (with Durrleman density & Lee wing penalties) alongside Hagan (2002) SABR benchmarks.
4. **Maturity Interpolation & Static Arbitrage Checks (`arbitrage.py`, `surface.py`)**: Piecewise-linear total-variance interpolation $w(k, T)$ across expiries, Durrleman local-density butterfly check $g(k) \ge 0$, discrete Black-76 call butterfly convexity $\partial^2 C / \partial K^2 \ge 0$, and calendar monotonicity $w(k, T_2) \ge w(k, T_1)$. Surfaces with violations are explicitly marked invalid (or rejected in strict mode).
5. **Surface Queries, Snapshots & Tooling (`surface.py`, `cli.py`, `viz.py`)**: Strict extrapolation rejection by default, deterministic versioned JSON snapshots (`surface_id`), replay verification, Polars DataFrame export, and Plotly 3D HTML visualization.

## Quickstart & Local Setup

Tested environment: macOS ARM64, Python `3.9.6`, NumPy `2.0.2`, SciPy `1.13.1`, Polars `1.36.1`, Plotly `7.1.0`, pytest `8.4.2`.

```bash
cd volatility-surface-engine
python3 -m venv .venv
.venv/bin/pip install -r requirements-lock.txt
.venv/bin/python setup.py develop --no-deps
```

## Demo, CLI, and Validation Commands

```bash
# 1. Regenerate deterministic synthetic fixtures (optional; already checked in)
.venv/bin/python scripts/generate_fixtures.py

# 2. Run the full automated test suite (70 unit, integration, and boundary tests)
.venv/bin/pytest

# 3. Run the reproducible end-to-end demo (writes reports/demo_results.json, demo_snapshot.json, demo_surface.html)
.venv/bin/python scripts/run_demo.py

# 4. Run acceptance benchmark & oracle verification (writes reports/acceptance_results.json, baseline_comparison.json)
.venv/bin/python scripts/run_acceptance.py

# 5. CLI usage examples
.venv/bin/vse validate --input data/fixtures/valid_chain_reference.json
.venv/bin/vse calibrate --input data/fixtures/valid_chain_reference.json \
  --config config/default_config.json \
  --output reports/demo_snapshot.json \
  --html reports/demo_surface.html
.venv/bin/vse query --snapshot reports/demo_snapshot.json --expiry-years 0.1643835616 --log-moneyness 0.0
.venv/bin/vse replay --input data/fixtures/valid_chain_reference.json \
  --snapshot reports/demo_snapshot.json \
  --config config/default_config.json
```

## Example Measured Output (Synthetic Fixtures)

All metrics below are **measured outputs on synthetic deterministic fixtures** (`seed=42`, `config_hash=1d170c4694db4aba`); they are not live-market performance claims. See [reports/acceptance.md](reports/acceptance.md) and raw JSON outputs in `reports/`.

| Scenario | Measured Metric | Result | Evidence |
|---|---|---|---|
| Reference SVI recovery (`valid_chain_reference.json`) | Weighted IV RMSE (SVI / SABR) | `1.074e-07` / `4.996e-04` | [baseline_comparison.json](reports/baseline_comparison.json) |
| Analytical & 60d interpolated oracle (15 points) | Max abs IV error (target `<= 1e-3`) | `7.789e-08` (PASS) | [baseline_comparison.json](reports/baseline_comparison.json) |
| Held-out interior strikes (16 quotes, 8 bps noise) | SVI / SABR held-out RMSE | `5.050e-04` / `3.634e-04` | [acceptance_results.json](reports/acceptance_results.json) |
| Static arbitrage check (reference surface) | Butterfly / calendar violations | `0` / `0` (`is_valid=true`) | [acceptance_results.json](reports/acceptance_results.json) |
| Calendar arbitrage violation fixture | Calendar violations / `is_valid` | `81` / `false` | [acceptance_results.json](reports/acceptance_results.json) |
| Deterministic fixed-seed replay | `surface_id` run 1 vs run 2 | `vse-048c9911e785` (exact match) | [acceptance_results.json](reports/acceptance_results.json) |
| Calibration runtime (4 expiries, SVI + SABR + arb) | Wall-clock time | `256.88 ms` | [baseline_comparison.json](reports/baseline_comparison.json) |

## Documentation & Evidence

- [Architecture & Math](docs/architecture.md)
- [ADR 0001: SVI/SABR & Total-Variance Surface Architecture](docs/adr/0001-svi-sabr-total-variance-architecture.md)
- [Data Contracts & Error Taxonomy](docs/contracts.md)
- [Integration & Dependency Provenance](docs/integration.md)
- [Operator & Developer Runbook](docs/runbook.md)
- [Acceptance Report](reports/acceptance.md)

## Assumptions & Known Limitations

- **European Black-76 only**: American early-exercise premia and discrete cash dividends are not modeled; non-European exercise styles are explicitly rejected.
- **No silent extrapolation**: Queries outside calibrated strike `[k_min(T), k_max(T)]` or maturity `[T_min, T_max]` domains raise `ExtrapolationError` unless explicitly opted into with `allow_extrapolation=True`, which flags `extrapolated=True`. Extrapolated wings carry significant model risk.
- **Independent slice fitting**: SVI slices are calibrated independently per expiry (with butterfly-density penalty) and then checked jointly for calendar arbitrage. When market data exhibits calendar inversions, the surface is explicitly marked `is_valid=False` rather than silently distorted; joint cross-maturity SSVI re-projection is deferred to future work.
- **Offline synthetic fixtures only**: Live market feeds, real customer records, production credentials, and live execution are out of scope and absent from this repository.
