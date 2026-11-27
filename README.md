# Derivatives Trading

Seven independent Python projects use synthetic market, curve, and portfolio fixtures to price options and fixed-income instruments, calibrate arbitrage-aware volatility surfaces, aggregate real-time Greeks, simulate discrete delta hedging, value dual-curve interest rate swaps, and replay daily margin settlement journals.

## Getting started

Start with the Fixed Income Analytics Engine. Its offline demo requires Python 3.9 or later and uses only the standard library to bootstrap a synthetic yield curve, price a portfolio of bonds, and attribute curve-shift scenario P&L.

```bash
git clone https://github.com/vaibhavkapur/Trading-Derivatives.git
cd Trading-Derivatives/fixed-income-analytics-engine
python3 scripts/run_demo.py
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The demo prints calibrated curve metrics, portfolio bond valuations, and analytic-versus-finite-difference DV01 checks, then writes full results to `reports/demo_output.json`. Open the [checked-in acceptance summary](fixed-income-analytics-engine/reports/acceptance.md) for the full validation matrix. Each project has its own dependencies, demo, and validation commands. Run them from that project's directory.

## Projects

- [Derivatives Pricing Engine](derivatives-pricing-engine/) — Values European and American vanilla equity options across Black-Scholes-Merton, CRR binomial trees, antithetic Monte Carlo, and finite-difference PDE solvers with convergence and confidence-interval checks.
- [Volatility Surface Engine](volatility-surface-engine/) — Cleans synthetic option chains, fits bid-ask-weighted SVI and SABR slices, interpolates total variance across expiries, and enforces static butterfly and calendar arbitrage checks.
- [Real-Time Greeks Engine](real-time-greeks-engine/) — Computes analytical and bump-and-revalue option sensitivities, maintains incremental portfolio buckets with stale-data freeze protection, and serves a FastAPI reconciliation dashboard.
- [Options Hedging Simulator](options-hedging-simulator/) — Compares static, periodic, and delta-band rebalancing policies under GBM and jump-diffusion paths using a currency-precision self-financing ledger with exact fill replay.
- [Fixed Income Analytics Engine](fixed-income-analytics-engine/) — Builds bond cash-flow schedules, bootstraps synthetic yield curves, computes clean/dirty prices, YTM, Z-spread, duration, convexity, and key-rate DV01, and attributes scenario P&L.
- [Interest Rate Swaps Engine](interest-rate-swaps-engine/) — Values dual-curve fixed-floating vanilla swaps with stub schedules, historical fixings, parallel PV01 and KR01 reconciliation, and deterministic and stochastic curve scenarios via CLI and FastAPI.
- [Derivatives Margin Engine](derivatives-margin-engine/) — Simulates initial and variation margin, collateral eligibility and haircuts, margin calls, and idempotent SQLite double-entry daily settlement journal replay under versioned policies.

Each project README links to its architecture, data contracts, integration guide, operator runbook, and validation evidence.
