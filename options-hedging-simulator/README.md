# Options Hedging Simulator

Educational and research prototype for measuring **hedge error**, **turnover**, and **transaction costs** across discrete delta-hedging policies under seeded GBM paths, Merton jump-diffusion / gap-shock stress regimes, and synthetic permitted historical bar fixtures.

> **Scope & Risk Disclaimer:** Offline educational/research prototype using synthetic fixtures and seeded simulations only. Discrete delta hedging does **not** eliminate gap, jump, liquidity, or model risk, and this repository makes no claim that any simulated policy is profitable. Live capital, real customer data, broker credentials, and production execution are strictly out of scope.

---

## Features

- **Seeded Path Simulation & Bar Loading (`paths.py`):** Reproducible `PCG64` Geometric Brownian Motion (GBM), Merton jump-diffusion with deterministic gap-shock injection, and validated historical bar CSV ingestion with staleness detection.
- **Decoupled Pricing & Numerical Oracle (`pricing.py`):** Closed-form Black-Scholes-Merton `PricingModel` with explicit conventions (`ACT/365F`, continuous/simple compounding, expiry limits) verified against an independent finite-difference Greek oracle.
- **Discrete Rebalancing Policies (`policies.py`):** Static initial hedge, periodic step-cadence rebalancing, and delta-band threshold rebalancing with lot rounding and minimum trade size filters.
- **Exact Currency-Precision Self-Financing Ledger (`ledger.py`, `simulator.py`):** `Decimal` minor-unit cash accounting (`USD`/`EUR`/`GBP` cents, `JPY` yen) enforcing exact step-by-step and cumulative cash & inventory reconciliation (`0.00` residual), cash financing + borrow spreads, continuous dividends, half-spreads, per-share/per-trade/minimum fees, contract multipliers, and both **cash** and **physical** exercise/assignment settlement.
- **Analytics, Replay & Visualization (`analytics.py`, `io.py`, `reporting.py`, `cli.py`):** Hedge error quantiles (`p05`–`p95`, `CVaR 5%`), turnover, cost decomposition, zero-cost $O(N^{-1/2})$ convergence analysis, SHA-256 fill-digest replay verification, and headless Matplotlib charts.

---

## Quickstart

From `options-hedging-simulator/`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements-lock.txt
pip install -e ".[dev]"
```

Run the full validation suite:

```bash
pytest -v
```

Run the reproducible end-to-end demo (baseline + replay check, holdout GBM comparison, holdout jump/gap stress comparison, and zero-cost convergence study):

```bash
python scripts/run_demo.py
```

### CLI Examples

```bash
# Validate a config
python -m options_hedging_simulator validate --config config/baseline_gbm.json

# Run a simulation and verify exact fill replay
python -m options_hedging_simulator run --config config/baseline_gbm.json --output-dir reports/baseline
python -m options_hedging_simulator replay --run-dir reports/baseline

# Compare static, periodic, and threshold policies across shared holdout paths
python -m options_hedging_simulator compare --config config/holdout_comparison.json --output-dir reports/holdout_gbm

# Run zero-cost convergence study as rebalancing interval shrinks
python -m options_hedging_simulator convergence --config config/convergence_study.json --steps 12,24,63,126,252 --output-dir reports/convergence
```

---

## Measured Example Output (Synthetic Seeded Benchmarks)

Measured on Python 3.9.6 (`numpy 2.0.2`, `pandas 2.3.3`, `matplotlib 3.9.4`) via `scripts/run_demo.py`:

### Holdout GBM Policy Comparison (`config/holdout_comparison.json`, 500 paths, `seed=900101`)

| Policy | Mean Error | Std Dev | p05 | p95 | Turnover | Mean Tx Cost | Max Residual |
|---|---:|---:|---:|---:|---:|---:|---:|
| `static_initial` | -\$339.25 | \$3,104.03 | -\$5,629.60 | \$3,549.88 | 1.12x | \$34.64 | \$0.00 |
| `periodic_weekly` | -\$146.51 | \$877.96 | -\$1,630.99 | \$1,189.59 | 2.23x | \$75.49 | \$0.00 |
| `threshold_band_10` | -\$169.01 | \$664.68 | -\$1,254.69 | \$949.38 | 2.46x | \$79.41 | \$0.00 |
| `threshold_band_05` | -\$173.34 | \$498.56 | -\$1,019.86 | \$527.52 | 3.01x | \$104.02 | \$0.00 |
| `threshold_band_02` | -\$203.78 | \$433.49 | -\$940.55 | \$476.48 | 3.43x | \$130.46 | \$0.00 |
| `periodic_daily` | -\$223.68 | \$421.13 | -\$941.28 | \$425.38 | 3.59x | \$153.08 | \$0.00 |

### Zero-Cost Convergence (`config/convergence_study.json`, 300 paths, `seed=424242`)

- Empirical log-log slope of hedge error std vs. step count $N$: **`-0.4900`** (theoretical Black-Scholes $O(N^{-1/2})$ target: **`-0.5000`**).
- Std dev declines monotonically from `\$1,037.70` (`22.49%` of premium at `N=12`) to `\$221.53` (`4.80%` of premium at `N=252`), with exact `\$0.00` ledger residual at every step count.

---

## Repository Layout & Documentation

- `src/options_hedging_simulator/` — Domain contracts, BSM pricer & FD oracle, path generators, hedging policies, exact ledger, simulator, analytics, reporting, and CLI.
- `tests/` — 45 unit, reconciliation, integration, boundary, and CLI tests.
- `config/` — Versioned baseline, holdout comparison, stressed jump, and convergence JSON configs.
- `data/fixtures/` — Synthetic deterministic, historical bar, stale-price, gap-shock, and invalid-contract fixtures.
- [docs/architecture.md](docs/architecture.md) — Component diagram, lifecycle, invariants, and trade-offs.
- [docs/contracts.md](docs/contracts.md) — Input/output schemas, units, error hierarchy, and fixture inventory.
- [docs/integration.md](docs/integration.md) — Adapter boundaries (`PricingModel`, margin feed), licenses, and out-of-scope notes.
- [docs/runbook.md](docs/runbook.md) — Local setup, CLI recipes, diagnostics, and recovery procedures.
- [docs/adr/0001-architecture-and-ledger-precision.md](docs/adr/0001-architecture-and-ledger-precision.md) — Core design decisions.
- [reports/acceptance.md](reports/acceptance.md) — Acceptance criteria mapped to measured test and benchmark evidence.
- [plan.md](plan.md) — Original implementation plan specification.

---

## Assumptions & Known Limitations

- **European exercise only:** American/Bermudan options fail fast with `UnsupportedContractError`.
- **Constant hedging parameters:** Default `BlackScholesPricer` uses a fixed `hedge_volatility`, flat `risk_free_rate`, and flat `dividend_yield`. Plug in a custom `PricingModel` adapter for local/stochastic volatility surfaces.
- **Linear market impact model:** Execution costs model proportional half-spread, per-share/per-trade/minimum fees, and negative-cash borrow spread; order-book depth and temporary/permanent price impact are not modeled.
- **No gap-risk immunity:** Stressed Merton jump + `-8%` gap-shock benchmarks (`reports/holdout_stress_jump/`) explicitly demonstrate severe tail losses (`CVaR 5% < -\$7,000`) even under daily rebalancing.
