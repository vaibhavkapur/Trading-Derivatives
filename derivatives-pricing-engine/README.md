# Derivatives Pricing Engine

An independently developed educational and research prototype for valuing European and American vanilla equity options across four numerical and analytic methods, with explicit pricing conventions, strict input validation, deterministic replay, and quantified convergence and uncertainty analysis.

> **Scope notice:** Offline prototype using synthetic fixtures only. Flat synthetic volatility inputs are **not** a calibrated market volatility surface. Live capital, real customer records, production credentials, and proprietary artifacts are excluded.

## Core Deliverables

- **Black-Scholes-Merton Analytic Pricer** (`analytics/black_scholes.py`): Closed-form European call/put pricing and exact Greeks (Delta, Gamma, Vega, Theta, Rho) with explicit UTC day-count conventions (`ACT/365F`, `ACT/360`, `ACT/365.25`, `30/360`), continuous discounting, and exact $T=0$ / $\sigma=0$ limits.
- **Cox-Ross-Rubinstein Binomial Tree** (`analytics/binomial.py`): Vectorized European and American pricing with tree Greeks, no-arbitrage probability validation ($0 < p < 1$), early-exercise node tracking, and multi-resolution convergence analysis.
- **Seeded Antithetic Monte Carlo** (`analytics/monte_carlo.py`): Deterministic `PCG64` simulation with antithetic variates, paired-average standard errors, calibrated confidence intervals, variance-reduction ratios, and multi-seed empirical coverage verification.
- **Finite-Difference PDE Solver** (`analytics/pde_fd.py`): Explicit (with CFL stability guard), Implicit, and Crank-Nicolson (with Rannacher smoothing and vectorized Red-Black Projected SOR for American early exercise) solvers with Dirichlet boundary conditions and grid-refinement convergence analysis.

## Repository Layout

```text
derivatives-pricing-engine/
├── config/default.json             # Versioned solver & benchmark defaults
├── data/fixtures/                  # Synthetic valid, reference, boundary, and invalid fixtures
├── docs/
│   ├── adr/0001-architecture-and-conventions.md
│   ├── architecture.md             # Component diagram, data flow, invariants, trade-offs
│   ├── contracts.md                # Input/output schemas, units, versions, error codes
│   ├── integration.md              # Sibling project boundaries & adapter contracts
│   └── runbook.md                  # Setup, demo, validation, diagnostics & recovery
├── reports/
│   ├── acceptance.md               # Acceptance criteria mapped to measured evidence
│   ├── baseline_demo.json          # Raw cross-method demo output + env provenance
│   ├── convergence_report.json     # Raw 5-res Binomial & 3-res PDE convergence data
│   └── mc_coverage_report.json     # Raw MC path sweep & 200-trial CI coverage data
├── scripts/
│   ├── run_demo.py                 # End-to-end reproducible demo runner
│   └── run_benchmarks.py           # Convergence & MC coverage benchmark runner
├── src/derivatives_pricing_engine/ # Pure analytics + typed service + CLI
├── tests/                          # 39 unit, integration, boundary, and failure tests
├── pyproject.toml
├── requirements.txt
└── requirements-lock.txt
```

## Quickstart & Setup

Run from `derivatives-pricing-engine/`:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-lock.txt
```

### Run the Reproducible Demo

```bash
.venv/bin/python scripts/run_demo.py
```

### Price a Single Fixture and Verify Deterministic Replay

```bash
PYTHONPATH=src .venv/bin/python -m derivatives_pricing_engine price \
  --input data/fixtures/valid_european_call.json \
  --output /tmp/envelope.json

PYTHONPATH=src .venv/bin/python -m derivatives_pricing_engine replay \
  --input /tmp/envelope.json
```

### Run Automated Tests and Benchmarks

```bash
# Run full unit, integration, and boundary test suite (39 tests)
PYTHONPATH=src .venv/bin/pytest

# Regenerate versioned convergence and Monte Carlo coverage reports in reports/
.venv/bin/python scripts/run_benchmarks.py
```

## Example Output & Measured Results

All figures below are **measured observations** from `scripts/run_benchmarks.py` on synthetic 1-year ATM fixtures (`S=100, K=100, r=0.05, sigma=0.20, ACT/365F`; recorded on Python `3.9.6`, NumPy `2.0.2`, SciPy `1.13.1`, arm64 macOS):

- **European Call (`q = 0.02`):**
  - Black-Scholes analytic: `9.227006` (`0.018 ms`; analytic reference error $\le 10^{-12}$ vs independent `math.erfc` oracle — target $\le 10^{-8}$)
  - CRR Binomial (`400` steps): `9.222147` (error `-0.004859`, `0.457 ms`)
  - Crank-Nicolson PDE (`400x400`): `9.224600` (error `-0.002405`, `5.497 ms`, empirical order `2.002`)
  - Antithetic Monte Carlo (`100,000` paths, `seed=42`): `9.241294`, 95% CI `[9.177029, 9.305558]`, SE `0.032789`, variance reduction `1.794x` (`0.848 ms`)
- **American Put (`q = 0.00`, European BS = `5.573526`):**
  - CRR Binomial (`400` steps): `6.088406` (early exercise premium `+0.514880`, `2.001 ms`)
  - Crank-Nicolson + Red-Black PSOR PDE (`400x400`): `6.087364` (early exercise premium `+0.513838`, `75.907 ms`, max PSOR iters `15`)
- **Monte Carlo CI Coverage (`200` trials × `10,000` paths, nominal 95% CI):**
  - Empirical coverage: `185 / 200` (`92.5%`), mean error `-0.002333`, mean VRR `1.800x`, total runtime `21.79 ms`.

Full acceptance evidence and raw JSON links are in [reports/acceptance.md](reports/acceptance.md).

## Documentation

- [Architecture & Trade-Offs](docs/architecture.md)
- [ADR-0001: Architecture & Pricing Conventions](docs/adr/0001-architecture-and-conventions.md)
- [Data Contracts & Error Semantics](docs/contracts.md)
- [Integration Boundaries & Dependencies](docs/integration.md)
- [Operator & Developer Runbook](docs/runbook.md)
- [Acceptance & Verification Report](reports/acceptance.md)

## Assumptions and Known Limitations

- **Flat synthetic inputs:** Constant risk-free rate $r$, continuous dividend yield $q$, and flat volatility $\sigma$ (not a calibrated volatility surface or term structure).
- **Supported styles only:** Vanilla European and American calls and puts only. Bermudan, Asian, Barrier, and other exotics are rejected with `UNSUPPORTED_FEATURE`.
- **Method restrictions:** `black_scholes` and `monte_carlo` reject American options explicitly (Longstaff-Schwartz LSM is not implemented).
- **Optional extensions not run:** Optional `QuantLib` and `C++20` backends from `plan.md` are not implemented in this baseline.
- **Not production-ready:** Intended solely for education and numerical research; any production deployment requires independent risk, legal, security, and operational review.
